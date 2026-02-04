"""Email Scraper Service - Cloud service for scraping emails from store URLs"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
import threading
import asyncio
import json
import time
import psycopg2.extras
from config import HOST, PORT, DEBUG, DATABASE_URL, EMAIL_SCRAPER_MAX_PAGES, EMAIL_SCRAPER_DELAY, EMAIL_SCRAPER_TIMEOUT, EMAIL_SCRAPER_MAX_RETRIES, EMAIL_SCRAPER_SITEMAP_LIMIT, MAX_CONCURRENT_EMAIL_SCRAPING
from database import Database
from modules.email_scraper import EmailScraper
from modules.ai_email_extractor import AIEmailExtractor
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

db = Database(DATABASE_URL)

# Initialize AI Email Extractor
try:
    ai_email_extractor = AIEmailExtractor()
    logger.info("AI Email Extractor initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize AI Email Extractor: {e}. Email extraction will fail without OpenAI API key.")
    ai_email_extractor = None

# Initialize Email Scraper with config values
email_scraper = EmailScraper(
    max_pages=EMAIL_SCRAPER_MAX_PAGES,
    delay=EMAIL_SCRAPER_DELAY,
    timeout=EMAIL_SCRAPER_TIMEOUT,
    max_retries=EMAIL_SCRAPER_MAX_RETRIES,
    sitemap_limit=EMAIL_SCRAPER_SITEMAP_LIMIT,
    email_processor=None
)

# Thread pool executor for parallel email scraping
email_scraping_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_EMAIL_SCRAPING, thread_name_prefix="email_scraper")

# Track active email scraping jobs
active_email_scraping_jobs = set()  # Track store IDs currently being scraped
email_scraping_lock = threading.Lock()  # Thread-safe access

logger.info(f"Initialized ThreadPoolExecutor with {MAX_CONCURRENT_EMAIL_SCRAPING} workers for parallel email scraping")

# Add CORS headers to all responses
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'service': 'Email Scraper Service',
        'status': 'running',
        'active_jobs': len(active_email_scraping_jobs),
        'max_concurrent': MAX_CONCURRENT_EMAIL_SCRAPING
    })

def start_next_email_scraping_job(app_name=None):
    """Start the next pending store email scraping if we have capacity. Optionally only for app_name."""
    with email_scraping_lock:
        active_count = len(active_email_scraping_jobs)
        if active_count >= MAX_CONCURRENT_EMAIL_SCRAPING:
            return None  # Already at capacity
        active_store_ids_set = set(active_email_scraping_jobs)
    
    # Get multiple pending stores and filter out already active ones (optionally for one app)
    pending_stores = db.get_stores_with_urls_no_emails(limit=MAX_CONCURRENT_EMAIL_SCRAPING * 2, app_name=app_name)
    if not pending_stores:
        return None  # No more stores to process
    
    # Filter out stores that are already active
    available_stores = [s for s in pending_stores if s['id'] not in active_store_ids_set]
    if not available_stores:
        return None  # All pending stores are already active
    
    store = available_stores[0]
    store_id = store['id']
    
    # Double-check and add atomically
    with email_scraping_lock:
        if store_id in active_email_scraping_jobs:
            return None  # Already processing (race condition caught)
        active_email_scraping_jobs.add(store_id)
    
    logger.info(f"Starting email scraping for store {store_id}: {store.get('store_name')} (Active: {len(active_email_scraping_jobs)}/{MAX_CONCURRENT_EMAIL_SCRAPING})")
    
    def scrape_emails_for_store(store):
        store_id = store['id']
        store_url = store.get('base_url')
        store_name = store.get('store_name')
        
        emails = []
        raw_emails = []
        scraping_stats = {}
        scraping_error = None
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(email_scraper.scrape_emails(store_url, store_name))
                
                if isinstance(result, dict):
                    raw_emails = result.get('raw_emails', [])
                    scraping_stats = result.get('stats', {})
                else:
                    raw_emails = result if isinstance(result, list) else []
                
                # Log detailed stats
                pages_discovered = scraping_stats.get('pages_discovered', 0)
                pages_scraped = scraping_stats.get('pages_scraped', 0)
                pages_failed = scraping_stats.get('pages_failed', 0)
                pages_with_emails = scraping_stats.get('pages_with_emails', 0)
                
                logger.info(f"Email scraping completed for store {store_id} ({store_name}): "
                          f"Raw emails: {len(raw_emails)}, "
                          f"Pages: {pages_discovered} discovered, {pages_scraped} scraped, {pages_failed} failed, {pages_with_emails} with emails")
                
                # Warn if scraping seems to have failed
                if len(raw_emails) == 0:
                    if pages_discovered == 0:
                        logger.warning(f"Store {store_id} ({store_name}): No pages discovered - possible URL issue or site blocking")
                        scraping_error = "No pages discovered"
                    elif pages_failed == pages_discovered:
                        logger.warning(f"Store {store_id} ({store_name}): All {pages_discovered} pages failed to scrape - possible rate limiting or site blocking")
                        scraping_error = f"All {pages_discovered} pages failed"
                    elif pages_scraped == 0:
                        logger.warning(f"Store {store_id} ({store_name}): No pages successfully scraped - possible connectivity or blocking issue")
                        scraping_error = "No pages successfully scraped"
                    else:
                        logger.info(f"Store {store_id} ({store_name}): Scraped {pages_scraped} pages but found no emails - likely no emails on site")
                
                if ai_email_extractor and raw_emails:
                    ai_result = ai_email_extractor.extract_relevant_emails(
                        raw_emails, store_url, store_name
                    )
                    emails = ai_result.get('emails', [])
                else:
                    emails = raw_emails
                
            except Exception as e:
                scraping_error = f"Exception during scraping: {type(e).__name__}: {str(e)}"
                logger.error(f"Error during email scraping for store {store_id} ({store_name}): {e}", exc_info=True)
            finally:
                if loop:
                    loop.close()
            
            try:
                db.update_store_emails(store_id, emails, raw_emails, scraping_error)
                if scraping_error:
                    logger.warning(f"Store {store_id} ({store_name}): Saved with error - {scraping_error}")
                logger.info(f"Stored {len(emails)} relevant emails for store {store_id} ({store_name})")
            except Exception as e:
                logger.error(f"Error updating store emails for store {store_id}: {e}", exc_info=True)
        except Exception as e:
            scraping_error = f"Critical error: {type(e).__name__}: {str(e)}"
            logger.error(f"Critical error in email scraping for store {store_id} ({store_name}): {e}", exc_info=True)
            try:
                db.update_store_emails(store_id, [], [], scraping_error)
            except:
                pass
        finally:
            with email_scraping_lock:
                active_email_scraping_jobs.discard(store_id)
                remaining_active = len(active_email_scraping_jobs)
            
            logger.info(f"Finished email scraping for store {store_id}. Active jobs: {remaining_active}/{MAX_CONCURRENT_EMAIL_SCRAPING}")
            
            # When this job completes, start the next one if available
            start_next_email_scraping_job()
    
    # Submit to executor
    email_scraping_executor.submit(scrape_emails_for_store, store)
    
    with email_scraping_lock:
        return {
            'success': True,
            'store_id': store_id,
            'store_name': store.get('store_name'),
            'active_count': len(active_email_scraping_jobs),
            'max_concurrent': MAX_CONCURRENT_EMAIL_SCRAPING
        }

@app.route('/api/email-scraping/start-next-job', methods=['POST'])
def start_next_email_scraping_job_endpoint():
    """API endpoint to start the next email scraping job if capacity available. Optional app_name in JSON body."""
    data = request.get_json(silent=True) or {}
    app_name = data.get('app_name') or request.args.get('app_name')
    if app_name and isinstance(app_name, str):
        app_name = app_name.strip() or None
    result = start_next_email_scraping_job(app_name=app_name)
    if result:
        return jsonify(result)
    else:
        with email_scraping_lock:
            active_count = len(active_email_scraping_jobs)
        return jsonify({
            'success': False,
            'message': 'No capacity or no pending stores',
            'active_count': active_count,
            'max_concurrent': MAX_CONCURRENT_EMAIL_SCRAPING
        })

@app.route('/api/email-scraping/batch/start', methods=['POST'])
def start_batch_email_scraping():
    """Start continuous email scraping: always keep stores processing. Optional app_name in JSON to scope to one app."""
    data = request.get_json(silent=True) or {}
    app_name = data.get('app_name') or request.args.get('app_name')
    if app_name and isinstance(app_name, str):
        app_name = app_name.strip() or None
    with email_scraping_lock:
        active_count = len(active_email_scraping_jobs)
        active_store_ids_set = set(active_email_scraping_jobs)
    
    # Get pending stores - get enough for all available slots (optionally for one app)
    jobs_to_start = min(MAX_CONCURRENT_EMAIL_SCRAPING - active_count, MAX_CONCURRENT_EMAIL_SCRAPING)
    pending_stores = db.get_stores_with_urls_no_emails(limit=jobs_to_start * 2, app_name=app_name)
    
    # Filter out stores that are already active
    truly_pending = [s for s in pending_stores if s['id'] not in active_store_ids_set]
    pending_count = len(truly_pending)
    
    if pending_count == 0:
        return jsonify({
            'success': False,
            'message': 'No stores with URLs pending email scraping'
        })
    
    # Start enough jobs to fill up to MAX_CONCURRENT_EMAIL_SCRAPING active
    jobs_to_start = min(MAX_CONCURRENT_EMAIL_SCRAPING - active_count, pending_count)
    
    logger.info(f"Starting {jobs_to_start} email scraping jobs (currently {active_count} active, {pending_count} pending)")
    
    # Start jobs for multiple stores at once
    actually_started = 0
    stores_to_process = truly_pending[:jobs_to_start]
    
    for store in stores_to_process:
        store_id = store['id']
        
        # Double-check it's not already active
        with email_scraping_lock:
            if store_id in active_email_scraping_jobs:
                continue  # Skip if already active
            active_email_scraping_jobs.add(store_id)
        
        logger.info(f"Starting email scraping for store {store_id}: {store.get('store_name')} (Active: {len(active_email_scraping_jobs)}/{MAX_CONCURRENT_EMAIL_SCRAPING})")
        
        def scrape_emails_for_store(store):
            store_id = store['id']
            store_url = store.get('base_url')
            store_name = store.get('store_name')
            
            emails = []
            raw_emails = []
            scraping_stats = {}
            scraping_error = None
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(email_scraper.scrape_emails(store_url, store_name))
                    
                    if isinstance(result, dict):
                        raw_emails = result.get('raw_emails', [])
                        scraping_stats = result.get('stats', {})
                    else:
                        raw_emails = result if isinstance(result, list) else []
                    
                    pages_discovered = scraping_stats.get('pages_discovered', 0)
                    pages_scraped = scraping_stats.get('pages_scraped', 0)
                    pages_failed = scraping_stats.get('pages_failed', 0)
                    pages_with_emails = scraping_stats.get('pages_with_emails', 0)
                    
                    logger.info(f"Email scraping completed for store {store_id} ({store_name}): "
                              f"Raw emails: {len(raw_emails)}, "
                              f"Pages: {pages_discovered} discovered, {pages_scraped} scraped, {pages_failed} failed, {pages_with_emails} with emails")
                    
                    if len(raw_emails) == 0:
                        if pages_discovered == 0:
                            logger.warning(f"Store {store_id} ({store_name}): No pages discovered - possible URL issue or site blocking")
                            scraping_error = "No pages discovered"
                        elif pages_failed == pages_discovered:
                            logger.warning(f"Store {store_id} ({store_name}): All {pages_discovered} pages failed to scrape - possible rate limiting or site blocking")
                            scraping_error = f"All {pages_discovered} pages failed"
                        elif pages_scraped == 0:
                            logger.warning(f"Store {store_id} ({store_name}): No pages successfully scraped - possible connectivity or blocking issue")
                            scraping_error = "No pages successfully scraped"
                        else:
                            logger.info(f"Store {store_id} ({store_name}): Scraped {pages_scraped} pages but found no emails - likely no emails on site")
                    
                    if ai_email_extractor and raw_emails:
                        ai_result = ai_email_extractor.extract_relevant_emails(
                            raw_emails, store_url, store_name
                        )
                        emails = ai_result.get('emails', [])
                    else:
                        emails = raw_emails
                    
                except Exception as e:
                    scraping_error = f"Exception during scraping: {type(e).__name__}: {str(e)}"
                    logger.error(f"Error during email scraping for store {store_id} ({store_name}): {e}", exc_info=True)
                finally:
                    if loop:
                        loop.close()
                
                try:
                    db.update_store_emails(store_id, emails, raw_emails, scraping_error)
                    if scraping_error:
                        logger.warning(f"Store {store_id} ({store_name}): Saved with error - {scraping_error}")
                    logger.info(f"Stored {len(emails)} relevant emails for store {store_id} ({store_name})")
                except Exception as e:
                    logger.error(f"Error updating store emails for store {store_id}: {e}", exc_info=True)
            except Exception as e:
                scraping_error = f"Critical error: {type(e).__name__}: {str(e)}"
                logger.error(f"Critical error in email scraping for store {store_id} ({store_name}): {e}", exc_info=True)
                try:
                    db.update_store_emails(store_id, [], [], scraping_error)
                except:
                    pass
            finally:
                with email_scraping_lock:
                    active_email_scraping_jobs.discard(store_id)
                    remaining_active = len(active_email_scraping_jobs)
                
                logger.info(f"Finished email scraping for store {store_id}. Active jobs: {remaining_active}/{MAX_CONCURRENT_EMAIL_SCRAPING}")
                
                # When this job completes, start the next one if available
                start_next_email_scraping_job()
        
        # Submit to executor
        email_scraping_executor.submit(scrape_emails_for_store, store)
        actually_started += 1
    
    # Get final active count
    with email_scraping_lock:
        final_active_count = len(active_email_scraping_jobs)
    
    logger.info(f"Started {actually_started} email scraping jobs. Now {final_active_count} active.")
    
    return jsonify({
        'success': True,
        'message': f'Email scraping started. {actually_started} stores now processing concurrently.',
        'active_count': final_active_count,
        'pending_count': pending_count - actually_started,
        'jobs_started': actually_started
    })

@app.route('/api/email-scraping/batch/status', methods=['GET'])
def get_batch_email_scraping_status():
    """Get status of continuous email scraping. Optional app_name query param to scope to one app."""
    app_name = request.args.get('app_name')
    if app_name and isinstance(app_name, str):
        app_name = app_name.strip() or None
    with email_scraping_lock:
        active_count = len(active_email_scraping_jobs)
        active_store_ids = list(active_email_scraping_jobs)
    
    # Get stores with URLs but no emails (pending), optionally for one app
    pending_stores = db.get_stores_with_urls_no_emails(app_name=app_name)
    pending_count = len(pending_stores)
    
    # Get active store details (optionally filter by app_name for scoped view)
    active_stores = []
    for store_id in active_store_ids:
        store = db.get_store(store_id)
        if store:
            if app_name and store.get('app_name') != app_name:
                continue
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except Exception:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except Exception:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            active_stores.append(store)
        else:
            if not app_name:
                active_stores.append({
                    'id': store_id,
                    'store_name': f'Store {store_id} (Loading...)',
                    'base_url': None,
                    'emails': [],
                    'raw_emails': []
                })
            logger.warning(f"Store {store_id} is in active_email_scraping_jobs but not found in database")
    
    # Filter out stores that are already active from pending list
    pending_store_ids = {s['id'] for s in pending_stores}
    active_store_ids_set = set(active_store_ids)
    truly_pending = [s for s in pending_stores if s['id'] not in active_store_ids_set]
    
    # Get completed stores (recently finished, last 10), optionally for one app
    conn = db.get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if app_name:
        cursor.execute("""
            SELECT * FROM stores
            WHERE app_name = %s AND (status = 'emails_found' OR status = 'no_emails_found')
            ORDER BY emails_scraped_at DESC
            LIMIT 10
        """, (app_name,))
    else:
        cursor.execute("""
            SELECT * FROM stores
            WHERE status = 'emails_found' OR status = 'no_emails_found'
            ORDER BY emails_scraped_at DESC
            LIMIT 10
        """)
    completed_rows = cursor.fetchall()
    
    completed_stores = []
    for row in completed_rows:
        store = dict(row)
        if store.get('emails'):
            try:
                store['emails'] = json.loads(store['emails'])
            except Exception:
                store['emails'] = []
        else:
            store['emails'] = []
        if store.get('raw_emails'):
            try:
                store['raw_emails'] = json.loads(store['raw_emails'])
            except Exception:
                store['raw_emails'] = []
        else:
            store['raw_emails'] = []
        completed_stores.append(store)
    
    # Get total counts for progress (optionally for one app)
    cursor2 = conn.cursor()
    if app_name:
        cursor2.execute("SELECT COUNT(*) FROM stores WHERE app_name = %s AND base_url IS NOT NULL AND base_url != ''", (app_name,))
    else:
        cursor2.execute("SELECT COUNT(*) FROM stores WHERE base_url IS NOT NULL AND base_url != ''")
    total_with_urls = cursor2.fetchone()[0]
    
    if app_name:
        cursor2.execute("SELECT COUNT(*) FROM stores WHERE app_name = %s AND status = 'pending_url'", (app_name,))
    else:
        cursor2.execute("SELECT COUNT(*) FROM stores WHERE status = 'pending_url'")
    pending_url_count = cursor2.fetchone()[0]
    conn.close()
    
    progress_percent = round((len(completed_stores) / total_with_urls * 100) if total_with_urls > 0 else 0, 1)
    
    is_processing = active_count > 0 or pending_count > 0
    
    logger.debug(f"Email scraping status: {active_count} active, {len(truly_pending)} pending, {len(active_stores)} active stores retrieved")
    
    out = {
        'active_count': active_count,
        'pending_count': len(truly_pending),
        'max_concurrent': MAX_CONCURRENT_EMAIL_SCRAPING,
        'available_slots': max(0, MAX_CONCURRENT_EMAIL_SCRAPING - active_count),
        'active_store_ids': active_store_ids,
        'active_stores': active_stores,
        'pending_stores': truly_pending[:20],
        'completed_stores': completed_stores[:5],
        'is_processing': is_processing,
        'progress_percent': progress_percent,
        'total_with_urls': total_with_urls,
        'pending_url_count': pending_url_count
    }
    if app_name:
        out['app_name'] = app_name
    return jsonify(out)

@app.route('/api/stores/<int:store_id>')
def get_store(store_id):
    """Get a single store"""
    store = db.get_store(store_id)
    if not store:
        return jsonify({'error': 'Store not found'}), 404
    return jsonify(store)

def continuous_worker():
    """Background worker that continuously processes email scraping jobs"""
    logger.info("Starting continuous email scraping worker...")
    while True:
        try:
            # Try to start next job if capacity available
            start_next_email_scraping_job()
            # Sleep before next check
            time.sleep(5)  # Check every 5 seconds
        except Exception as e:
            logger.error(f"Error in continuous worker: {e}", exc_info=True)
            time.sleep(10)  # Wait longer on error

# Continuous worker is DISABLED by default
# Email scraping only starts when explicitly triggered via /api/email-scraping/batch/start
# This allows URL finding to complete first before email scraping begins
# Uncomment below to enable auto-start (not recommended for the current workflow)
# worker_thread = threading.Thread(target=continuous_worker, daemon=True)
# worker_thread.start()
# logger.info("Continuous email scraping worker thread started")
logger.info("Continuous email scraping worker DISABLED. Email scraping will only start when explicitly triggered via batch/start endpoint.")

if __name__ == '__main__':
    logger.info(f"Starting Email Scraper Service on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)
