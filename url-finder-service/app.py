"""URL Finder Service - Local service for finding store URLs using Chrome extension"""
from flask import Flask, render_template, jsonify, request, Response
from flask_cors import CORS
import logging
import threading
import time
import json
import uuid
import psycopg2.extras
from config import (
    HOST,
    PORT,
    DEBUG,
    DATABASE_URL,
    URL_FINDER_PROVIDER,
    PERPLEXITY_API_KEY,
    PERPLEXITY_MODEL,
    PERPLEXITY_TIMEOUT,
    PERPLEXITY_TOP_N,
    PERPLEXITY_AUTOSAVE_THRESHOLD,
    PERPLEXITY_CACHE_TTL_SECONDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TIMEOUT,
    GEMINI_TOP_N,
    GEMINI_AUTOSAVE_THRESHOLD,
    GEMINI_CACHE_TTL_SECONDS,
    GEMINI_VERIFY_SHOPIFY,
    GEMINI_MAX_RETRIES,
    GEMINI_RETRY_DELAY,
    URL_VALIDATION_ENABLED,
    URL_VALIDATION_TIMEOUT,
    URL_VALIDATION_FOLLOW_REDIRECTS,
)
from database import Database
from modules.review_scraper import ReviewScraper
from modules.url_finder import URLFinder
from modules.ai_url_selector import AIURLSelector
from modules.perplexity_search import PerplexitySearch
from modules.gemini_search import GeminiSearch
from typing import Optional, Dict, Any

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
review_scraper = ReviewScraper()
url_finder = URLFinder(headless=False)  # Visible browser for manual search

# Initialize Perplexity client (if configured)
perplexity = None
if PERPLEXITY_API_KEY:
    perplexity = PerplexitySearch(
        PERPLEXITY_API_KEY,
        model=PERPLEXITY_MODEL,
        timeout=PERPLEXITY_TIMEOUT,
        top_n=PERPLEXITY_TOP_N,
        cache_ttl_seconds=PERPLEXITY_CACHE_TTL_SECONDS,
    )
    logger.info("Perplexity client initialized")
else:
    logger.warning("Perplexity not configured. Set PERPLEXITY_API_KEY to enable.")

# Initialize Gemini client (if configured)
gemini = None
if GEMINI_API_KEY:
    gemini = GeminiSearch(
        GEMINI_API_KEY,
        model=GEMINI_MODEL,
        timeout=GEMINI_TIMEOUT,
        top_n=GEMINI_TOP_N,
        cache_ttl_seconds=GEMINI_CACHE_TTL_SECONDS,
        verify_shopify=GEMINI_VERIFY_SHOPIFY,
        max_retries=GEMINI_MAX_RETRIES,
        initial_retry_delay=GEMINI_RETRY_DELAY,
    )
    logger.info("Gemini client initialized")
else:
    logger.warning("Gemini not configured. Set GEMINI_API_KEY (or GOOGLE_API_KEY) to enable.")

# Initialize AI URL Selector
try:
    ai_selector = AIURLSelector()
    logger.info("AI URL Selector initialized successfully")
except Exception as e:
    logger.warning(f"Failed to initialize AI URL Selector: {e}. AI features will be disabled.")
    ai_selector = None

# Store pending search requests
pending_searches = {}
search_results = {}

# Add CORS headers to all responses for extension access
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')

@app.route('/data')
def data_page():
    """Data display page"""
    return render_template('data.html')

@app.route('/review')
def review_page():
    """Review page for manual URL selection"""
    return render_template('review.html')

@app.route('/api/url-finder/config', methods=['GET'])
def get_url_finder_config():
    """Expose URL finder configuration to the frontend"""
    return jsonify({
        'default_provider': URL_FINDER_PROVIDER,
        'perplexity_configured': bool(perplexity),
        'perplexity_autosave_threshold': PERPLEXITY_AUTOSAVE_THRESHOLD,
        'gemini_configured': bool(gemini),
        'gemini_autosave_threshold': GEMINI_AUTOSAVE_THRESHOLD,
    })

# ==================== Review Scraping Endpoints ====================

@app.route('/api/jobs', methods=['POST'])
def create_job():
    """Create a new scraping job or resume existing incomplete job"""
    data = request.json
    app_url = data.get('app_url')
    max_reviews = data.get('max_reviews', 0)
    max_pages = data.get('max_pages', 0)
    
    if not app_url:
        return jsonify({'error': 'app_url is required'}), 400
    
    if max_reviews < 0 or max_pages < 0:
        return jsonify({'error': 'max_reviews and max_pages must be >= 0'}), 400
    
    app_name = review_scraper.extract_app_name(app_url)
    
    # Check if job already exists
    existing_job = db.get_job_by_url(app_url)
    
    if existing_job:
        job_id = existing_job['id']
        is_complete = db.is_job_complete(job_id)
        
        if is_complete:
            return jsonify({
                'error': 'This review URL has already been completely scraped.',
                'job_id': job_id,
                'message': f'Job completed. Total reviews: {existing_job.get("reviews_scraped", 0)}'
            }), 400
        
        # Resume existing incomplete job
        logger.info(f"Resuming existing job {job_id} for {app_url}")
        current_page = existing_job.get('current_page', 0) or 0
        start_page = max(1, current_page + 1)
        existing_reviews_count = existing_job.get('reviews_scraped', 0) or 0
        
        existing_max_reviews = existing_job.get('max_reviews_limit', 0) or 0
        existing_max_pages = existing_job.get('max_pages_limit', 0) or 0
        
        final_max_reviews_limit = max_reviews if max_reviews > 0 else existing_max_reviews
        final_max_pages_limit = max_pages if max_pages > 0 else existing_max_pages
        
        if (max_reviews > 0 and max_reviews != existing_max_reviews) or (max_pages > 0 and max_pages != existing_max_pages):
            db.update_job_status(job_id, existing_job.get('status', 'scraping_reviews'),
                                max_reviews_limit=final_max_reviews_limit,
                                max_pages_limit=final_max_pages_limit)
        
        remaining_reviews = final_max_reviews_limit - existing_reviews_count if final_max_reviews_limit > 0 else 0
        remaining_pages = final_max_pages_limit - current_page if final_max_pages_limit > 0 else 0
        
        def resume_scraping():
            try:
                final_page_tracked = [current_page]
                
                def progress_callback(message, current_page_val, total_pages, reviews_count):
                    final_page_tracked[0] = current_page_val
                    total_so_far = existing_reviews_count + reviews_count
                    db.update_job_status(
                        job_id, 
                        'scraping_reviews',
                        progress_message=message,
                        current_page=current_page_val,
                        total_pages=total_pages,
                        reviews_scraped=total_so_far,
                        total_stores=total_so_far,
                        max_reviews_limit=final_max_reviews_limit,
                        max_pages_limit=final_max_pages_limit
                    )
                
                reviews = review_scraper.scrape_all_pages(
                    app_url, 
                    max_pages=remaining_pages if final_max_pages_limit > 0 else 0, 
                    start_page=start_page,
                    max_reviews=remaining_reviews if final_max_reviews_limit > 0 else 0,
                    progress_callback=progress_callback
                )
                
                total_reviews = existing_reviews_count + len(reviews)
                final_current_page = final_page_tracked[0] if final_page_tracked[0] > current_page else (current_page + (len(reviews) // 10 + 1) if reviews else current_page)
                
                reached_reviews_limit = final_max_reviews_limit > 0 and total_reviews >= final_max_reviews_limit
                reached_pages_limit = final_max_pages_limit > 0 and final_current_page >= final_max_pages_limit
                no_more_reviews = len(reviews) == 0
                
                if reviews:
                    db.add_stores(reviews, job_id, app_name)
                
                if no_more_reviews:
                    db.update_job_status(
                        job_id,
                        'finding_urls',
                        total_stores=total_reviews,
                        reviews_scraped=total_reviews,
                        current_page=final_current_page,
                        progress_message=f"Finished scraping. Total reviews: {total_reviews}. Ready for URL finding."
                    )
                elif reached_reviews_limit or reached_pages_limit:
                    limit_msg = []
                    if reached_reviews_limit:
                        limit_msg.append(f"reached max reviews limit ({final_max_reviews_limit})")
                    if reached_pages_limit:
                        limit_msg.append(f"reached max pages limit ({final_max_pages_limit})")
                    
                    db.update_job_status(
                        job_id,
                        'scraping_reviews',
                        total_stores=total_reviews,
                        reviews_scraped=total_reviews,
                        current_page=final_current_page,
                        progress_message=f"Batch complete. Scraped {total_reviews} total reviews ({len(reviews)} new). {', '.join(limit_msg)}. Paste URL again to continue."
                    )
                else:
                    db.update_job_status(
                        job_id,
                        'finding_urls',
                        total_stores=total_reviews,
                        reviews_scraped=total_reviews,
                        current_page=final_current_page,
                        progress_message=f"Scraped {total_reviews} total reviews ({len(reviews)} new). Ready for URL finding."
                    )
            except Exception as e:
                logger.error(f"Error resuming review scraping: {e}", exc_info=True)
                db.update_job_status(job_id, 'error', progress_message=f"Error: {str(e)}")
        
        thread = threading.Thread(target=resume_scraping)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id, 
            'app_name': app_name,
            'resumed': True,
            'message': f'Resuming from page {start_page}. Previously scraped {current_page} pages, {existing_reviews_count} reviews.',
            'remaining_pages': remaining_pages if final_max_pages_limit > 0 else 'unlimited',
            'remaining_reviews': remaining_reviews if final_max_reviews_limit > 0 else 'unlimited'
        })
    
    # Create new job
    job_id = db.create_job(app_name, app_url, max_reviews_limit=max_reviews, max_pages_limit=max_pages)
    logger.info(f"Created new job {job_id} for {app_url} with limits: max_reviews={max_reviews}, max_pages={max_pages}")
    
    def scrape_reviews():
        try:
            def progress_callback(message, current_page, total_pages, reviews_count):
                db.update_job_status(
                    job_id, 
                    'scraping_reviews',
                    progress_message=message,
                    current_page=current_page,
                    total_pages=total_pages,
                    reviews_scraped=reviews_count,
                    total_stores=reviews_count,
                    max_reviews_limit=max_reviews,
                    max_pages_limit=max_pages
                )
            
            final_page_count = [0]
            
            def tracked_progress_callback(message, current_page_val, total_pages, reviews_count):
                final_page_count[0] = current_page_val
                progress_callback(message, current_page_val, total_pages, reviews_count)
            
            reviews = review_scraper.scrape_all_pages(
                app_url, 
                max_pages=max_pages, 
                start_page=1,
                max_reviews=max_reviews,
                progress_callback=tracked_progress_callback
            )
            
            if reviews:
                db.add_stores(reviews, job_id, app_name)
            
            total_reviews_scraped = len(reviews)
            final_page = final_page_count[0] if final_page_count[0] > 0 else (max_pages if max_pages > 0 else (len(reviews) // 10 + 1))
            
            reached_reviews_limit = max_reviews > 0 and total_reviews_scraped >= max_reviews
            reached_pages_limit = max_pages > 0 and final_page >= max_pages
            no_more_reviews = total_reviews_scraped == 0
            
            if no_more_reviews:
                db.update_job_status(
                    job_id, 
                    'finding_urls', 
                    total_stores=total_reviews_scraped,
                    reviews_scraped=total_reviews_scraped,
                    current_page=final_page,
                    progress_message=f"Finished scraping. Found {total_reviews_scraped} reviews. Ready for URL finding."
                )
            elif reached_reviews_limit or reached_pages_limit:
                limit_msg = []
                if reached_reviews_limit:
                    limit_msg.append(f"reached max reviews limit ({max_reviews})")
                if reached_pages_limit:
                    limit_msg.append(f"reached max pages limit ({max_pages})")
                
                db.update_job_status(
                    job_id,
                    'scraping_reviews',
                    total_stores=total_reviews_scraped,
                    reviews_scraped=total_reviews_scraped,
                    current_page=final_page,
                    max_reviews_limit=max_reviews,
                    max_pages_limit=max_pages,
                    progress_message=f"Batch complete. Scraped {total_reviews_scraped} reviews. {', '.join(limit_msg)}. Paste URL again to continue."
                )
            else:
                db.update_job_status(
                    job_id, 
                    'finding_urls', 
                    total_stores=total_reviews_scraped,
                    reviews_scraped=total_reviews_scraped,
                    current_page=final_page,
                    progress_message=f"Scraped {total_reviews_scraped} reviews. Ready for URL finding."
                )
        except Exception as e:
            logger.error(f"Error scraping reviews: {e}", exc_info=True)
            db.update_job_status(job_id, 'error', progress_message=f"Error: {str(e)}")
    
    thread = threading.Thread(target=scrape_reviews)
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id, 'app_name': app_name, 'resumed': False})

@app.route('/api/jobs')
def get_all_jobs():
    """Get all jobs"""
    jobs = db.get_all_jobs()
    return jsonify(jobs)

@app.route('/api/jobs/<int:job_id>')
def get_job(job_id):
    """Get job status"""
    job = db.get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    stats = db.get_statistics(job_id)
    job['statistics'] = stats
    
    return jsonify(job)

# ==================== Store Management Endpoints ====================

def _app_name_from_request():
    """Resolve job_id or app_name query param to app_name for scoping. Returns None for 'all apps'."""
    job_id = request.args.get('job_id', type=int)
    app_name = request.args.get('app_name', type=str)
    if job_id:
        job = db.get_job(job_id)
        return job.get('app_name') if job else None
    if app_name:
        return app_name.strip() or None
    return None

@app.route('/api/stores/pending')
def get_pending_stores():
    """Get stores pending URL verification, optionally filtered by job_id or app_name"""
    limit = request.args.get('limit', type=int)
    app_name = _app_name_from_request()
    stores = db.get_pending_stores(limit=limit, app_name=app_name)
    return jsonify(stores)

@app.route('/api/stores/next')
def get_next_store():
    """Get the next pending store (one at a time), optionally for a specific app (job_id or app_name)"""
    app_name = _app_name_from_request()
    store = db.get_next_pending_store(app_name=app_name)
    if not store:
        return jsonify({'store': None, 'message': 'No more stores pending'})
    return jsonify({'store': store})

@app.route('/api/stores/<int:store_id>/skip', methods=['POST'])
def skip_store(store_id):
    """Skip a store"""
    db.skip_store(store_id)
    return jsonify({'success': True})

@app.route('/api/stores/<int:store_id>')
def get_store(store_id):
    """Get a single store"""
    store = db.get_store(store_id)
    if not store:
        return jsonify({'error': 'Store not found'}), 404
    return jsonify(store)

@app.route('/api/stores/<int:store_id>/url', methods=['PUT'])
def update_store_url(store_id):
    """Update store URL with optional validation"""
    data = request.json or {}
    url = data.get('url')
    confidence = data.get('confidence')
    provider = data.get('provider')
    skip_validation = data.get('skip_validation', False)  # Allow skipping validation for manual entries
    
    if not url:
        return jsonify({'error': 'url is required'}), 400
    
    cleaned_url = url_finder.clean_url(url)
    
    # Validate URL if enabled and not skipped
    validation_result = None
    if URL_VALIDATION_ENABLED and not skip_validation:
        try:
            from modules.url_validator import URLValidator
            validator = URLValidator(
                timeout=URL_VALIDATION_TIMEOUT,
                follow_redirects=URL_VALIDATION_FOLLOW_REDIRECTS
            )
            validation_result = validator.validate_url(cleaned_url)
            
            if not validation_result['is_valid']:
                logger.warning(
                    f"URL validation failed for store {store_id}: {cleaned_url} - "
                    f"{validation_result.get('error')} ({validation_result.get('error_type')})"
                )
                return jsonify({
                    'success': False,
                    'error': f"URL validation failed: {validation_result.get('error')}",
                    'error_type': validation_result.get('error_type'),
                    'url': cleaned_url,
                    'validation_result': validation_result
                }), 400
            
            # Use validated final URL if redirects were followed
            if validation_result.get('final_url') and validation_result['final_url'] != cleaned_url:
                cleaned_url = validation_result['final_url']
                logger.info(f"URL redirected: {url} -> {cleaned_url}")
        except Exception as e:
            logger.error(f"URL validation error for store {store_id}: {e}", exc_info=True)
            # Continue without validation if validator fails
            validation_result = {'is_valid': True, 'error': f'Validation error: {str(e)}'}
    
    db.update_store_url(store_id, cleaned_url, confidence=confidence, provider=provider)
    
    validation_note = " (validated)" if validation_result and validation_result.get('is_valid') else ""
    logger.info(f"URL saved for store {store_id}: {cleaned_url} (confidence: {confidence}, provider: {provider}){validation_note}")
    
    pending_count = db.count_pending_url_stores()
    
    return jsonify({
        'success': True, 
        'url': cleaned_url, 
        'message': 'URL saved successfully.',
        'pending_url_count': pending_count,
        'validated': validation_result.get('is_valid') if validation_result else None
    })

@app.route('/api/stores/url-finding-status', methods=['GET'])
def get_url_finding_status():
    """Get status of URL finding phase, optionally filtered by job_id or app_name"""
    app_name = _app_name_from_request()
    
    pending_count = db.count_pending_url_stores(app_name=app_name)
    
    conn = db.get_connection()
    cursor = conn.cursor()
    if app_name:
        cursor.execute("SELECT COUNT(*) FROM stores WHERE app_name = %s", (app_name,))
    else:
        cursor.execute("SELECT COUNT(*) FROM stores")
    total_stores = cursor.fetchone()[0]
    
    if app_name:
        cursor.execute("SELECT COUNT(*) FROM stores WHERE app_name = %s AND base_url IS NOT NULL AND base_url != ''", (app_name,))
    else:
        cursor.execute("SELECT COUNT(*) FROM stores WHERE base_url IS NOT NULL AND base_url != ''")
    stores_with_urls = cursor.fetchone()[0]
    
    cursor_dict = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if app_name:
        cursor_dict.execute("SELECT * FROM stores WHERE app_name = %s AND (status = 'pending_url' OR status = 'url_found') ORDER BY id LIMIT 10", (app_name,))
    else:
        cursor_dict.execute("SELECT * FROM stores WHERE status = 'pending_url' OR status = 'url_found' ORDER BY id LIMIT 10")
    pending_stores = []
    for row in cursor_dict.fetchall():
        store = dict(row)
        if store.get('emails'):
            try:
                store['emails'] = json.loads(store['emails'])
            except Exception:
                store['emails'] = []
        else:
            store['emails'] = []
        pending_stores.append(store)
    
    cursor2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if app_name:
        cursor2.execute("SELECT * FROM stores WHERE app_name = %s AND base_url IS NOT NULL AND base_url != '' AND (status = 'url_verified' OR status = 'url_found') ORDER BY updated_at DESC LIMIT 5", (app_name,))
    else:
        cursor2.execute("SELECT * FROM stores WHERE base_url IS NOT NULL AND base_url != '' AND (status = 'url_verified' OR status = 'url_found') ORDER BY updated_at DESC LIMIT 5")
    recently_found = []
    for row in cursor2.fetchall():
        store = dict(row)
        if store.get('emails'):
            try:
                store['emails'] = json.loads(store['emails'])
            except Exception:
                store['emails'] = []
        else:
            store['emails'] = []
        recently_found.append(store)
    
    conn.close()
    
    progress_percent = round((stores_with_urls / total_stores * 100) if total_stores > 0 else 0, 1)
    
    return jsonify({
        'pending_count': pending_count,
        'total_stores': total_stores,
        'stores_with_urls': stores_with_urls,
        'progress_percent': progress_percent,
        'pending_stores': pending_stores,
        'recently_found': recently_found,
        'is_complete': pending_count == 0,
        'app_name': app_name
    })

@app.route('/api/stores/<int:store_id>/emails', methods=['PUT'])
def update_store_emails_manual(store_id):
    """Manually update emails (both cleaned and raw) for a store"""
    data = request.json
    emails = data.get('emails', [])
    raw_emails = data.get('raw_emails', [])
    
    if not isinstance(emails, list):
        return jsonify({'error': 'emails must be a list'}), 400
    if not isinstance(raw_emails, list):
        return jsonify({'error': 'raw_emails must be a list'}), 400
    
    # Validate email format (more lenient for raw emails)
    import re
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
    valid_emails = []
    for email in emails:
        email = email.strip()
        if email and email_pattern.match(email):
            valid_emails.append(email)
    
    # For raw emails, accept any string that looks like an email (more lenient)
    valid_raw_emails = []
    for email in raw_emails:
        email = email.strip()
        if email and '@' in email:  # Basic validation for raw emails
            valid_raw_emails.append(email)
    
    # Get current store to verify it exists
    store = db.get_store(store_id)
    if not store:
        return jsonify({'error': 'Store not found'}), 404
    
    # Update both cleaned and raw emails
    db.update_store_emails(store_id, valid_emails, valid_raw_emails, None)
    logger.info(f"Manually updated {len(valid_emails)} cleaned emails and {len(valid_raw_emails)} raw emails for store {store_id}")
    
    return jsonify({
        'success': True,
        'emails': valid_emails,
        'raw_emails': valid_raw_emails,
        'message': f'Updated {len(valid_emails)} cleaned emails and {len(valid_raw_emails)} raw emails'
    })

@app.route('/api/stores')
def get_all_stores():
    """Get all stores, optionally filtered by job_id or app_name"""
    app_name = _app_name_from_request()
    stores = db.get_all_stores(app_name=app_name)
    return jsonify(stores)

@app.route('/api/stores/export', methods=['POST'])
def export_stores():
    """Export filtered stores to CSV"""
    import csv
    import io
    
    data = request.json
    store_ids = data.get('store_ids', [])
    
    if not store_ids:
        return jsonify({'error': 'No stores to export'}), 400
    
    all_stores = db.get_all_stores()
    stores_to_export = [s for s in all_stores if s['id'] in store_ids]
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'ID', 'Store Name', 'Country', 'Rating', 'Review Text', 'Review Date',
        'Usage Duration', 'Base URL', 'Raw Emails', 'Cleaned Emails',
        'Status', 'App Name', 'Created At'
    ])
    
    for store in stores_to_export:
        raw_emails = store.get('raw_emails', [])
        cleaned_emails = store.get('emails', [])
        writer.writerow([
            store.get('id'),
            store.get('store_name', ''),
            store.get('country', ''),
            store.get('rating', ''),
            store.get('review_text', ''),
            store.get('review_date', ''),
            store.get('usage_duration', ''),
            store.get('base_url', ''),
            ', '.join(raw_emails) if raw_emails else '',
            ', '.join(cleaned_emails) if cleaned_emails else '',
            store.get('status', ''),
            store.get('app_name', ''),
            store.get('created_at', '')
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=shopify_stores_export.csv'}
    )

@app.route('/api/stores/delete', methods=['POST'])
def delete_stores():
    """Delete stores and associated jobs"""
    data = request.json
    store_ids = data.get('store_ids', [])
    
    if not store_ids:
        return jsonify({'error': 'No stores to delete'}), 400
    
    if not isinstance(store_ids, list):
        return jsonify({'error': 'store_ids must be a list'}), 400
    
    try:
        result = db.delete_stores(store_ids)
        return jsonify({
            'success': True,
            'stores_deleted': result['stores_deleted'],
            'jobs_deleted': result['jobs_deleted'],
            'app_urls': result['app_urls'],
            'message': f"Deleted {result['stores_deleted']} stores and {result['jobs_deleted']} review page URLs"
        })
    except Exception as e:
        logger.error(f"Error deleting stores: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

# ==================== Chrome Extension Endpoints ====================

@app.route('/api/search/request', methods=['POST'])
def request_search():
    """Request a Google search from Chrome extension"""
    data = request.json
    store_name = data.get('store_name')
    country = data.get('country', '')
    
    if not store_name:
        return jsonify({'error': 'store_name is required'}), 400
    
    search_id = str(uuid.uuid4())
    query = f"{store_name} {country}".strip()
    
    pending_searches[search_id] = {
        'query': query,
        'store_name': store_name,
        'country': country,
        'created_at': time.time(),
        'status': 'pending'
    }
    
    logger.info(f"Created search request: {search_id} for query: {query}")
    
    return jsonify({
        'search_id': search_id,
        'query': query,
        'message': 'Search request created. Extension will process it.'
    })

@app.route('/api/search/perplexity', methods=['POST'])
def search_perplexity():
    """Use Perplexity to find/rank store URLs with confidence."""
    if not perplexity:
        return jsonify({
            'error': 'Perplexity is not configured. Set PERPLEXITY_API_KEY.'
        }), 500

    data = request.json or {}
    store_name = (data.get('store_name') or '').strip()
    country = (data.get('country') or '').strip()
    review_text = (data.get('review_text') or '').strip()

    if not store_name:
        return jsonify({'error': 'store_name is required'}), 400

    try:
        result = perplexity.find_store_url(
            store_name=store_name,
            country=country,
            review_text=review_text,
        )
        return jsonify({
            'query': result.get('query'),
            'results': result.get('results', []),
            'selected_url': result.get('selected_url'),
            'confidence': result.get('confidence'),
            'reasoning': result.get('reasoning', ''),
            'duration_ms': result.get('duration_ms'),
            'autosave_threshold': PERPLEXITY_AUTOSAVE_THRESHOLD,
        })
    except Exception as e:
        logger.error(f"Perplexity search failed: {e}", exc_info=True)
        return jsonify({'error': f'Perplexity search failed: {str(e)}'}), 500


def process_and_save_url(store_id: int, url: str, confidence: Optional[float], provider: str, reasoning: str, candidate_results: list) -> Dict[str, Any]:
    """
    Process URL from Gemini/Perplexity: validate, try candidates if needed, and save or mark for review.
    Returns dict with 'success', 'action', 'url', 'error' keys.
    """
    from modules.url_finder import URLFinder
    from config import AUTO_SAVE_THRESHOLD, LOW_CONFIDENCE_THRESHOLD
    
    url_finder = URLFinder(headless=True)

    def save_candidate_urls(primary_url: Optional[str], candidates: list):
        urls = []
        if candidates:
            for candidate in candidates[:10]:
                if isinstance(candidate, dict):
                    candidate_url = candidate.get('url', '')
                else:
                    candidate_url = str(candidate) if candidate else ''
                if candidate_url:
                    urls.append(candidate_url)
        if primary_url:
            urls.insert(0, primary_url)
        # De-duplicate while preserving order
        seen = set()
        deduped = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        db.save_store_candidate_urls(store_id, deduped)
    
    # Validate URL if validation is enabled
    validated_url = url
    validation_error = None
    if URL_VALIDATION_ENABLED:
        try:
            from modules.url_validator import URLValidator
            validator = URLValidator(
                timeout=URL_VALIDATION_TIMEOUT,
                follow_redirects=URL_VALIDATION_FOLLOW_REDIRECTS
            )
            validation_result = validator.validate_url(url)
            if not validation_result['is_valid']:
                logger.warning(
                    f"Store {store_id}: Primary URL validation failed: {url} - "
                    f"{validation_result.get('error')} ({validation_result.get('error_type')})"
                )
                validation_error = validation_result.get('error')
                
                # Try alternative candidates from results
                if candidate_results:
                    logger.info(f"Store {store_id}: Trying {len(candidate_results)} alternative candidate URLs")
                    for candidate in candidate_results[:5]:  # Try up to 5 candidates
                        if isinstance(candidate, dict):
                            candidate_url = candidate.get('url', '')
                        else:
                            candidate_url = str(candidate) if candidate else ''
                        
                        if candidate_url and candidate_url != url:
                            candidate_validation = validator.validate_url(candidate_url)
                            if candidate_validation['is_valid']:
                                logger.info(f"Store {store_id}: Found valid alternative URL: {candidate_url}")
                                validated_url = candidate_url
                                validation_error = None
                                # Update confidence slightly lower for alternative
                                if confidence:
                                    confidence = max(0.0, confidence - 0.1)
                                break
        except Exception as e:
            logger.error(f"URL validation error for store {store_id}: {e}", exc_info=True)
            # Continue without validation if validator fails
    
    # If validation failed and no valid candidate found
    if validation_error:
        error_msg = f"URL validation failed: {validation_error}"
        if not candidate_results:
            error_msg += ". No alternative candidates available."
        else:
            error_msg += f". Tried {len(candidate_results)} alternatives, all failed validation."
        
        # If high confidence but validation failed, mark for review
        if confidence and confidence >= AUTO_SAVE_THRESHOLD:
            save_candidate_urls(url, candidate_results)
            db.mark_store_needs_review(store_id, error_msg, provider, confidence)
            return {
                'success': True,
                'action': 'needs_review',
                'url': url,
                'confidence': confidence,
                'provider': provider,
                'error': error_msg
            }
        else:
            # Low confidence + validation failed = not found
            db.mark_store_not_found(store_id, error_msg, provider)
            return {
                'success': False,
                'action': 'not_found',
                'url': url,
                'confidence': confidence,
                'provider': provider,
                'error': error_msg
            }
    
    # Determine action based on confidence
    if confidence and confidence >= AUTO_SAVE_THRESHOLD:
        # High confidence - auto-save (URL is validated if validation enabled)
        try:
            cleaned_url = url_finder.clean_url(validated_url)
            db.update_store_url(store_id, cleaned_url, confidence=confidence, provider=provider)
            logger.info(
                f"Store {store_id}: Auto-saved URL {cleaned_url} "
                f"with {confidence:.2%} confidence via {provider}"
            )
            return {
                'success': True,
                'action': 'saved',
                'url': cleaned_url,
                'confidence': confidence,
                'provider': provider,
                'error': None
            }
        except Exception as e:
            error_msg = f"Failed to save URL: {str(e)}"
            logger.error(f"Store {store_id}: {error_msg}", exc_info=True)
            save_candidate_urls(validated_url, candidate_results)
            db.mark_store_needs_review(store_id, error_msg, provider, confidence)
            return {
                'success': False,
                'action': 'error',
                'url': validated_url,
                'confidence': confidence,
                'provider': provider,
                'error': error_msg
            }
    elif confidence and confidence >= LOW_CONFIDENCE_THRESHOLD:
        # Medium confidence - needs review
        reason = f"Low confidence ({confidence:.2%}): {reasoning}"
        save_candidate_urls(validated_url, candidate_results)
        db.mark_store_needs_review(store_id, reason, provider, confidence)
        return {
            'success': True,
            'action': 'needs_review',
            'url': validated_url,
            'confidence': confidence,
            'provider': provider,
            'error': None
        }
    else:
        # Low confidence - not found
        error_msg = f"Low confidence ({confidence:.2% if confidence else 'unknown'})"
        db.mark_store_not_found(store_id, error_msg, provider)
        return {
            'success': False,
            'action': 'not_found',
            'url': validated_url,
            'confidence': confidence,
            'provider': provider,
            'error': error_msg
        }

@app.route('/api/search/gemini', methods=['POST'])
def search_gemini():
    """Use Gemini with Google Search tool to find/rank store URLs with confidence.
    If store_id is provided, automatically processes and saves the URL."""
    if not gemini:
        return jsonify({
            'error': 'Gemini is not configured. Set GEMINI_API_KEY (or GOOGLE_API_KEY).'
        }), 500

    data = request.json or {}
    store_id = data.get('store_id')  # Optional: if provided, auto-save
    store_name = (data.get('store_name') or '').strip()
    country = (data.get('country') or '').strip()
    review_text = (data.get('review_text') or '').strip()

    if not store_name:
        return jsonify({'error': 'store_name is required'}), 400

    try:
        result = gemini.find_store_url(
            store_name=store_name,
            country=country,
            review_text=review_text,
        )
        
        # If store_id provided, auto-process and save
        if store_id:
            selected_url = result.get('selected_url')
            confidence = result.get('confidence')
            reasoning = result.get('reasoning', '')
            candidate_results = result.get('results', [])
            
            if not selected_url:
                # No URL found
                db.mark_store_not_found(store_id, 'No URL returned from Gemini', 'gemini')
                return jsonify({
                    'success': False,
                    'action': 'not_found',
                    'error': 'No URL found',
                    'query': result.get('query'),
                    'results': candidate_results
                }), 200
            
            # Process and save URL
            process_result = process_and_save_url(
                store_id=store_id,
                url=selected_url,
                confidence=confidence,
                provider='gemini',
                reasoning=reasoning,
                candidate_results=candidate_results
            )
            
            return jsonify({
                'success': process_result['success'],
                'action': process_result['action'],
                'url': process_result.get('url'),
                'confidence': process_result.get('confidence'),
                'provider': process_result.get('provider'),
                'error': process_result.get('error'),
                'query': result.get('query'),
                'results': candidate_results,
                'reasoning': reasoning,
                'duration_ms': result.get('duration_ms'),
            })
        
        # Legacy mode: return results for manual selection
        return jsonify({
            'query': result.get('query'),
            'results': result.get('results', []),
            'selected_url': result.get('selected_url'),
            'confidence': result.get('confidence'),
            'reasoning': result.get('reasoning', ''),
            'duration_ms': result.get('duration_ms'),
            'autosave_threshold': GEMINI_AUTOSAVE_THRESHOLD,
        })
    except ValueError as e:
        # Rate limit exhausted after retries
        error_msg = str(e)
        logger.warning(f"Gemini rate limit exhausted: {error_msg}")
        return jsonify({
            'error': error_msg,
            'error_type': 'rate_limit_exhausted',
            'suggestion': 'Please try again in a few moments, or switch to another provider (Perplexity/Extension).'
        }), 429
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Gemini search failed: {e}", exc_info=True)
        # Check if it's a 429 error that wasn't caught by retry logic
        if '429' in error_msg or 'RESOURCE_EXHAUSTED' in error_msg.upper():
            return jsonify({
                'error': 'Gemini API rate limit exceeded. The system will retry automatically, but you may want to try again later or use another provider.',
                'error_type': 'rate_limit',
                'suggestion': 'Consider switching to Perplexity or Chrome Extension provider.'
            }), 429
        return jsonify({'error': f'Gemini search failed: {error_msg}'}), 500

@app.route('/api/search/poll/<search_id>', methods=['GET'])
def poll_search_results(search_id):
    """Poll for search results"""
    if search_id in search_results:
        result = search_results[search_id]
        return jsonify({
            'status': 'complete',
            'urls': result['urls'],
            'query': result['query']
        })
    elif search_id in pending_searches:
        return jsonify({
            'status': 'pending',
            'query': pending_searches[search_id]['query']
        })
    else:
        return jsonify({
            'status': 'not_found',
            'message': 'Search ID not found'
        }), 404

@app.route('/api/search/extension/submit', methods=['POST', 'OPTIONS'])
def extension_submit_results():
    """Extension submits results here"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    data = request.json
    query = data.get('query')
    urls = data.get('urls', [])
    search_id = data.get('search_id')
    
    logger.info(f"Extension submitting results: query='{query}', search_id={search_id}, url_count={len(urls)}")
    
    if not search_id:
        for sid, search in list(pending_searches.items()):
            if search['query'].lower() == query.lower():
                search_id = sid
                logger.info(f"Matched search by query, found search_id: {search_id}")
                break
    
    if search_id:
        search_results[search_id] = {
            'urls': urls,
            'query': query,
            'received_at': time.time()
        }
        if search_id in pending_searches:
            del pending_searches[search_id]
        logger.info(f"Extension submitted {len(urls)} URLs for search_id: {search_id}, query: {query}")
    else:
        search_id = str(uuid.uuid4())
        search_results[search_id] = {
            'urls': urls,
            'query': query,
            'received_at': time.time()
        }
        logger.warning(f"Extension submitted {len(urls)} URLs without matching search_id, created new: {search_id}, query: {query}")
    
    response = jsonify({'success': True, 'search_id': search_id})
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/api/search/extension/pending', methods=['GET', 'OPTIONS'])
def get_pending_search():
    """Extension polls this to get pending searches"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    active_searches = {k: v for k, v in pending_searches.items() 
                       if v.get('status') == 'pending'}
    
    logger.info(f"Extension polling for pending searches. Active: {len(active_searches)}, Total pending: {len(pending_searches)}")
    
    if active_searches:
        oldest_id = min(active_searches.keys(), 
                       key=lambda k: active_searches[k]['created_at'])
        search = active_searches[oldest_id]
        pending_searches[oldest_id]['status'] = 'processing'
        logger.info(f"Extension requested search: {search['query']} (search_id: {oldest_id})")
        response = jsonify({
            'query': search['query'],
            'search_id': oldest_id
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    response = jsonify({'query': None, 'search_id': None})
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/api/search/extension/status', methods=['GET', 'OPTIONS'])
def extension_status():
    """Check if extension is active"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    response = jsonify({
        'status': 'active',
        'message': 'Extension can reach Flask server'
    })
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

# ==================== AI URL Selection ====================

@app.route('/api/ai/select-url', methods=['POST', 'OPTIONS'])
def ai_select_url():
    """Use AI to select the best URL from search results"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    if not ai_selector:
        return jsonify({
            'error': 'AI URL Selector is not available. Check API key configuration.'
        }), 503
    
    data = request.json
    store_name = data.get('store_name')
    country = data.get('country')
    review_text = data.get('review_text')
    search_results = data.get('search_results', [])
    
    if not store_name:
        return jsonify({'error': 'store_name is required'}), 400
    
    if not search_results or len(search_results) == 0:
        return jsonify({'error': 'search_results is required and cannot be empty'}), 400
    
    try:
        logger.info(f"AI selecting URL for store: {store_name}")
        result = ai_selector.select_best_url(
            store_name=store_name,
            country=country,
            review_text=review_text,
            search_results=search_results
        )
        
        logger.info(f"AI selected URL: {result['selected_url']} (confidence: {result['confidence']:.2f})")
        
        return jsonify({
            'success': True,
            'selected_url': result['selected_url'],
            'confidence': result['confidence'],
            'reasoning': result['reasoning'],
            'selected_index': result['selected_index']
        })
    except Exception as e:
        logger.error(f"Error in AI URL selection: {e}", exc_info=True)
        return jsonify({
            'error': f'AI selection failed: {str(e)}'
        }), 500

# ==================== Statistics ====================

@app.route('/api/statistics')
def get_statistics():
    """Get overall statistics"""
    job_id = request.args.get('job_id', type=int)
    stats = db.get_statistics(job_id=job_id)
    return jsonify(stats)

# ==================== Background Worker Endpoints ====================

# Global worker instance (will be initialized if WORKER_ENABLED)
worker = None
worker_thread = None
current_worker_provider = URL_FINDER_PROVIDER

def init_worker(selected_provider: str = None):
    """Initialize background worker if enabled"""
    global worker
    from config import WORKER_ENABLED, URL_FINDER_PROVIDER
    if WORKER_ENABLED:
        from modules.background_worker import BackgroundWorker
        # Use provided provider or fallback to URL_FINDER_PROVIDER
        provider = selected_provider or current_worker_provider or URL_FINDER_PROVIDER
        worker = BackgroundWorker(db, selected_provider=provider)
        logger.info(f"Background worker initialized with provider: {provider}")
    return worker

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    health = {
        'status': 'healthy',
        'database': 'unknown',
        'providers': {},
        'worker': None
    }
    
    # Check database connectivity
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        conn.close()
        health['database'] = 'connected'
    except Exception as e:
        health['database'] = f'error: {str(e)}'
        health['status'] = 'unhealthy'
    
    # Check provider availability
    health['providers'] = {
        'gemini': gemini is not None,
        'perplexity': perplexity is not None,
    }
    
    # Check worker status
    global worker
    if worker is None:
        worker = init_worker()
    
    if worker:
        health['worker'] = worker.get_status()
    
    return jsonify(health)

@app.route('/api/worker/start', methods=['POST'])
def start_worker():
    """Start background worker"""
    global worker, worker_thread, current_worker_provider
    import threading
    data = request.json or {}
    requested_provider = (data.get('provider') or '').strip().lower()
    if requested_provider:
        if requested_provider not in ['gemini', 'perplexity', 'extension']:
            return jsonify({'error': 'Invalid provider. Must be: gemini, perplexity, or extension'}), 400
        current_worker_provider = requested_provider
    
    if worker is None:
        worker = init_worker(selected_provider=current_worker_provider)
    
    if worker is None:
        return jsonify({
            'error': 'Worker not enabled. Set WORKER_ENABLED=true in environment.'
        }), 400
    
    if worker.running:
        return jsonify({
            'message': 'Worker is already running',
            'status': worker.get_status()
        })
    
    # Start worker in background thread
    worker_thread = threading.Thread(target=worker.run_continuous, daemon=True)
    worker_thread.start()
    
    logger.info("Background worker started via API")
    
    return jsonify({
        'message': 'Worker started',
        'status': worker.get_status()
    })

@app.route('/api/worker/stop', methods=['POST'])
def stop_worker():
    """Stop background worker"""
    global worker
    
    if worker is None:
        return jsonify({
            'error': 'Worker not initialized'
        }), 400
    
    if not worker.running:
        return jsonify({
            'message': 'Worker is not running',
            'status': worker.get_status()
        })
    
    worker.stop()
    logger.info("Background worker stopped via API")
    
    return jsonify({
        'message': 'Worker stop requested',
        'status': worker.get_status()
    })

@app.route('/api/worker/status', methods=['GET'])
def get_worker_status():
    """Get worker status"""
    global worker
    
    if worker is None:
        worker = init_worker()
    
    if worker is None:
        return jsonify({
            'enabled': False,
            'message': 'Worker not enabled'
        })
    
    status = worker.get_status()
    # Add selected provider info
    status['selected_provider'] = worker.selected_provider or current_worker_provider
    
    return jsonify({
        'enabled': True,
        'status': status
    })

@app.route('/api/worker/provider', methods=['GET', 'POST'])
def worker_provider():
    """Get or set the selected provider for the worker"""
    global worker, worker_thread, current_worker_provider
    
    if request.method == 'POST':
        data = request.json or {}
        provider = data.get('provider', '').strip().lower()
        
        if provider not in ['gemini', 'perplexity', 'extension']:
            return jsonify({'error': 'Invalid provider. Must be: gemini, perplexity, or extension'}), 400
        
        current_worker_provider = provider
        # If worker is running, stop it first
        was_running = False
        if worker and worker.running:
            was_running = True
            worker.stop()
            if worker_thread and worker_thread.is_alive():
                worker_thread.join(timeout=5)
        
        # Reinitialize worker with new provider
        worker = init_worker(selected_provider=provider)
        
        # Restart if it was running
        if was_running and worker:
            worker_thread = threading.Thread(target=worker.run_continuous, daemon=True)
            worker_thread.start()
            logger.info(f"Worker restarted with provider: {provider}")
        
        return jsonify({
            'success': True,
            'provider': provider,
            'message': f'Worker provider set to {provider}',
            'status': worker.get_status() if worker else None
        })
    
    # GET - return current provider
    if worker and hasattr(worker, 'selected_provider') and worker.selected_provider:
        provider = worker.selected_provider
    else:
        provider = current_worker_provider or URL_FINDER_PROVIDER
    
    return jsonify({
        'provider': provider
    })

@app.route('/api/worker/process-store/<int:store_id>', methods=['POST'])
def process_store_manual(store_id):
    """Manually trigger processing for a specific store"""
    global worker
    
    if worker is None:
        worker = init_worker()
    
    if worker is None:
        return jsonify({
            'error': 'Worker not enabled. Set WORKER_ENABLED=true in environment.'
        }), 400
    
    # Get store data
    store = db.get_store(store_id)
    if not store:
        return jsonify({'error': 'Store not found'}), 404
    
    # Check if store already has URL
    if store.get('base_url'):
        return jsonify({
            'message': 'Store already has URL',
            'url': store.get('base_url')
        })
    
    # Try to lock store
    if not db.lock_store_for_processing(store_id):
        return jsonify({
            'error': 'Store is already being processed'
        }), 409
    
    try:
        # Process store
        result = worker.process_store(store_id, store)
        db.unlock_store(store_id)
        
        return jsonify({
            'success': result['success'],
            'action': result['action'],
            'url': result.get('url'),
            'confidence': result.get('confidence'),
            'provider': result.get('provider'),
            'error': result.get('error')
        })
    except Exception as e:
        db.unlock_store(store_id)
        logger.error(f"Error processing store {store_id}: {e}", exc_info=True)
        return jsonify({
            'error': str(e)
        }), 500

@app.route('/api/stores/review', methods=['GET'])
def get_stores_needing_review():
    """Get stores that need manual review"""
    limit = request.args.get('limit', type=int)
    app_name = _app_name_from_request()
    
    stores = db.get_stores_needing_review(limit=limit, app_name=app_name)
    
    # Add candidate URLs to each store
    for store in stores:
        store['candidate_urls'] = db.get_store_candidate_urls(store['id'])
    
    return jsonify(stores)

@app.route('/api/stores/<int:store_id>/review/select-url', methods=['POST'])
def select_url_for_review(store_id):
    """Manually select a URL for a store in review"""
    data = request.json or {}
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'url is required'}), 400
    
    # Get store to verify it's in review status
    store = db.get_store(store_id)
    if not store:
        return jsonify({'error': 'Store not found'}), 404
    
    if store.get('status') != 'needs_review':
        return jsonify({
            'error': f'Store is not in review status (current: {store.get("status")})'
        }), 400
    
    # Update store with selected URL
    cleaned_url = url_finder.clean_url(url)
    db.update_store_url(store_id, cleaned_url, confidence=store.get('url_confidence'), provider=store.get('url_finding_provider'))
    
    logger.info(f"Manually selected URL for review store {store_id}: {cleaned_url}")
    
    return jsonify({
        'success': True,
        'url': cleaned_url,
        'message': 'URL selected and saved successfully'
    })

if __name__ == '__main__':
    logger.info(f"Starting URL Finder Service on {HOST}:{PORT}")
    
    # Initialize worker if enabled
    from config import WORKER_ENABLED
    if WORKER_ENABLED:
        init_worker()
        if worker:
            import threading
            worker_thread = threading.Thread(target=worker.run_continuous, daemon=True)
            worker_thread.start()
            logger.info("Background worker started automatically")
    
    app.run(host=HOST, port=PORT, debug=DEBUG)
