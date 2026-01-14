"""URL Finder Service - Local service for finding store URLs using Chrome extension"""
from flask import Flask, render_template, jsonify, request, Response
from flask_cors import CORS
import logging
import threading
import time
import json
import uuid
import psycopg2.extras
from config import HOST, PORT, DEBUG, DATABASE_URL
from database import Database
from modules.review_scraper import ReviewScraper
from modules.url_finder import URLFinder
from modules.ai_url_selector import AIURLSelector
from typing import Optional

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
                    db.update_job_status(
                        job_id, 
                        'scraping_reviews',
                        progress_message=message,
                        current_page=current_page_val,
                        total_pages=total_pages,
                        reviews_scraped=existing_reviews_count + reviews_count,
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

@app.route('/api/stores/pending')
def get_pending_stores():
    """Get stores pending URL verification"""
    limit = request.args.get('limit', type=int)
    stores = db.get_pending_stores(limit=limit)
    return jsonify(stores)

@app.route('/api/stores/next')
def get_next_store():
    """Get the next pending store (one at a time)"""
    store = db.get_next_pending_store()
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
    """Update store URL"""
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'url is required'}), 400
    
    cleaned_url = url_finder.clean_url(url)
    db.update_store_url(store_id, cleaned_url)
    
    logger.info(f"URL saved for store {store_id}: {cleaned_url}")
    
    pending_count = db.count_pending_url_stores()
    
    return jsonify({
        'success': True, 
        'url': cleaned_url, 
        'message': 'URL saved successfully.',
        'pending_url_count': pending_count
    })

@app.route('/api/stores/url-finding-status', methods=['GET'])
def get_url_finding_status():
    """Get status of URL finding phase"""
    pending_count = db.count_pending_url_stores()
    
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stores")
    total_stores = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM stores WHERE base_url IS NOT NULL AND base_url != ''")
    stores_with_urls = cursor.fetchone()[0]
    conn.close()
    
    conn = db.get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM stores WHERE status = 'pending_url' OR status = 'url_found' ORDER BY id LIMIT 10")
    pending_stores = []
    for row in cursor.fetchall():
        store = dict(row)
        if store.get('emails'):
            try:
                store['emails'] = json.loads(store['emails'])
            except:
                store['emails'] = []
        else:
            store['emails'] = []
        pending_stores.append(store)
    
    cursor.execute("SELECT * FROM stores WHERE base_url IS NOT NULL AND base_url != '' AND (status = 'url_verified' OR status = 'url_found') ORDER BY updated_at DESC LIMIT 5")
    recently_found = []
    for row in cursor.fetchall():
        store = dict(row)
        if store.get('emails'):
            try:
                store['emails'] = json.loads(store['emails'])
            except:
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
        'is_complete': pending_count == 0
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
    """Get all stores"""
    stores = db.get_all_stores()
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

if __name__ == '__main__':
    logger.info(f"Starting URL Finder Service on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)
