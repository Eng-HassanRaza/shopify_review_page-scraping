"""Background worker for automated URL finding"""
import logging
import time
import signal
import threading
import sys
import psycopg2.extras
from typing import Dict, Optional, List, Any
from datetime import datetime

from config import (
    WORKER_SLEEP_SECONDS,
    WORKER_BATCH_SIZE,
    WORKER_MAX_RETRIES,
    PROVIDER_PRIORITY,
    AUTO_SAVE_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    GEMINI_API_KEY,
    PERPLEXITY_API_KEY,
    URL_VALIDATION_ENABLED,
    URL_VALIDATION_TIMEOUT,
    URL_VALIDATION_FOLLOW_REDIRECTS,
)
from database import Database

logger = logging.getLogger(__name__)


class BackgroundWorker:
    """Background worker for processing stores and finding URLs automatically"""
    
    def __init__(self, database: Database, selected_provider: str = None):
        self.db = database
        self.running = False
        self.processed_count = 0
        self.saved_count = 0
        self.needs_review_count = 0
        self.not_found_count = 0
        self.error_count = 0
        self.start_time = None
        self.current_store_id = None
        self.current_store_name = None
        self.selected_provider = selected_provider  # Use only this provider if set
        
        # Initialize providers (lazy import to avoid circular dependencies)
        self.gemini = None
        self.perplexity = None
        self.ai_selector = None
        self.url_validator = None
        
        self._init_providers()
        self._init_url_validator()
        
        # Setup signal handlers for graceful shutdown (main thread only)
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, self._handle_shutdown)
                signal.signal(signal.SIGINT, self._handle_shutdown)
        except Exception as e:
            logger.warning(f"Signal handler setup skipped: {e}")
    
    def _init_providers(self):
        """Initialize available providers"""
        try:
            if GEMINI_API_KEY:
                from modules.gemini_search import GeminiSearch
                from config import (
                    GEMINI_MODEL, GEMINI_TIMEOUT, GEMINI_TOP_N,
                    GEMINI_CACHE_TTL_SECONDS, GEMINI_VERIFY_SHOPIFY,
                    GEMINI_MAX_RETRIES, GEMINI_RETRY_DELAY
                )
                self.gemini = GeminiSearch(
                    GEMINI_API_KEY,
                    model=GEMINI_MODEL,
                    timeout=GEMINI_TIMEOUT,
                    top_n=GEMINI_TOP_N,
                    cache_ttl_seconds=GEMINI_CACHE_TTL_SECONDS,
                    verify_shopify=GEMINI_VERIFY_SHOPIFY,
                    max_retries=GEMINI_MAX_RETRIES,
                    initial_retry_delay=GEMINI_RETRY_DELAY,
                )
                logger.info("Gemini provider initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize Gemini provider: {e}")
        
        try:
            if PERPLEXITY_API_KEY:
                from modules.perplexity_search import PerplexitySearch
                from config import (
                    PERPLEXITY_MODEL, PERPLEXITY_TIMEOUT, PERPLEXITY_TOP_N,
                    PERPLEXITY_CACHE_TTL_SECONDS
                )
                self.perplexity = PerplexitySearch(
                    PERPLEXITY_API_KEY,
                    model=PERPLEXITY_MODEL,
                    timeout=PERPLEXITY_TIMEOUT,
                    top_n=PERPLEXITY_TOP_N,
                    cache_ttl_seconds=PERPLEXITY_CACHE_TTL_SECONDS,
                )
                logger.info("Perplexity provider initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize Perplexity provider: {e}")
        
        try:
            from modules.ai_url_selector import AIURLSelector
            self.ai_selector = AIURLSelector()
            logger.info("AI URL Selector initialized")
        except Exception as e:
            logger.warning(f"AI URL Selector not available: {e}")
    
    def _init_url_validator(self):
        """Initialize URL validator if enabled"""
        if URL_VALIDATION_ENABLED:
            try:
                from modules.url_validator import URLValidator
                self.url_validator = URLValidator(
                    timeout=URL_VALIDATION_TIMEOUT,
                    follow_redirects=URL_VALIDATION_FOLLOW_REDIRECTS
                )
                logger.info("URL Validator initialized")
            except Exception as e:
                logger.warning(f"URL Validator not available: {e}")
                self.url_validator = None
        else:
            logger.info("URL validation disabled")
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    def should_auto_save(self, confidence: Optional[float], threshold: float = None) -> bool:
        """Determine if URL should be auto-saved based on confidence"""
        if confidence is None:
            return False
        threshold = threshold or AUTO_SAVE_THRESHOLD
        return confidence >= threshold
    
    def find_url_with_fallback(
        self,
        store_name: str,
        country: str = "",
        review_text: str = ""
    ) -> Dict[str, Any]:
        """
        Try providers - use only selected_provider if set, otherwise use priority order
        
        Returns:
            {
                'success': bool,
                'url': str or None,
                'confidence': float or None,
                'reasoning': str,
                'provider': str,
                'results': List[Dict] or None,
                'error': str or None
            }
        """
        providers_to_try = []
        
        # If selected_provider is set, use only that provider
        if self.selected_provider:
            if self.selected_provider == 'gemini' and self.gemini:
                providers_to_try.append(('gemini', self.gemini))
            elif self.selected_provider == 'perplexity' and self.perplexity:
                providers_to_try.append(('perplexity', self.perplexity))
            elif self.selected_provider == 'extension':
                # Extension is not supported in background worker
                return {
                    'success': False,
                    'url': None,
                    'confidence': None,
                    'reasoning': 'Chrome Extension is not supported in background worker',
                    'provider': 'extension',
                    'results': None,
                    'error': 'Chrome Extension requires manual interaction and cannot be used in background worker'
                }
        else:
            # Build provider list based on priority configuration (fallback)
            for provider_name in PROVIDER_PRIORITY:
                if provider_name == 'gemini' and self.gemini:
                    providers_to_try.append(('gemini', self.gemini))
                elif provider_name == 'perplexity' and self.perplexity:
                    providers_to_try.append(('perplexity', self.perplexity))
        
        if not providers_to_try:
            return {
                'success': False,
                'url': None,
                'confidence': None,
                'reasoning': f'Selected provider "{self.selected_provider}" is not configured' if self.selected_provider else 'No providers configured',
                'provider': self.selected_provider if self.selected_provider else None,
                'results': None,
                'error': f'Provider "{self.selected_provider}" is not available' if self.selected_provider else 'No URL finding providers are configured'
            }
        
        last_error = None
        
        for provider_name, provider in providers_to_try:
            try:
                logger.info(f"Trying provider: {provider_name} for store: {store_name}")
                
                if provider_name == 'gemini':
                    result = provider.find_store_url(
                        store_name=store_name,
                        country=country,
                        review_text=review_text
                    )
                    selected_url = result.get('selected_url')
                    confidence = result.get('confidence')
                    reasoning = result.get('reasoning', '')
                    results = result.get('results', [])
                    
                    if selected_url and confidence is not None:
                        return {
                            'success': True,
                            'url': selected_url,
                            'confidence': confidence,
                            'reasoning': reasoning,
                            'provider': 'gemini',
                            'results': results,
                            'error': None
                        }
                    elif results:
                        # Low confidence but has results
                        return {
                            'success': True,
                            'url': results[0].get('url') if results else None,
                            'confidence': confidence or 0.0,
                            'reasoning': reasoning or 'Low confidence result',
                            'provider': 'gemini',
                            'results': results,
                            'error': None
                        }
                    else:
                        last_error = 'No results from Gemini'
                        continue
                
                elif provider_name == 'perplexity':
                    result = provider.find_store_url(
                        store_name=store_name,
                        country=country,
                        review_text=review_text
                    )
                    selected_url = result.get('selected_url')
                    confidence = result.get('confidence')
                    reasoning = result.get('reasoning', '')
                    results = result.get('results', [])
                    
                    if selected_url and confidence is not None:
                        return {
                            'success': True,
                            'url': selected_url,
                            'confidence': confidence,
                            'reasoning': reasoning,
                            'provider': 'perplexity',
                            'results': results,
                            'error': None
                        }
                    elif results:
                        return {
                            'success': True,
                            'url': results[0].get('url') if results else None,
                            'confidence': confidence or 0.0,
                            'reasoning': reasoning or 'Low confidence result',
                            'provider': 'perplexity',
                            'results': results,
                            'error': None
                        }
                    else:
                        last_error = 'No results from Perplexity'
                        continue
                
            except Exception as e:
                error_msg = f"{provider_name} error: {str(e)}"
                logger.error(error_msg, exc_info=True)
                last_error = error_msg
                continue
        
        # All providers failed
        return {
            'success': False,
            'url': None,
            'confidence': None,
            'reasoning': 'All providers failed',
            'provider': None,
            'results': None,
            'error': last_error or 'Unknown error'
        }
    
    def process_store(self, store_id: int, store_data: Dict) -> Dict[str, Any]:
        """
        Process a single store to find its URL
        
        Returns:
            {
                'success': bool,
                'action': str ('saved', 'needs_review', 'not_found', 'error'),
                'url': str or None,
                'confidence': float or None,
                'provider': str or None,
                'error': str or None
            }
        """
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
            self.db.save_store_candidate_urls(store_id, deduped)

        store_name = store_data.get('store_name', '').strip()
        country = store_data.get('country', '').strip()
        review_text = store_data.get('review_text', '').strip()
        
        if not store_name:
            error_msg = 'Store name is empty'
            self.db.mark_store_not_found(store_id, error_msg)
            return {
                'success': False,
                'action': 'not_found',
                'url': None,
                'confidence': None,
                'provider': None,
                'error': error_msg
            }
        
        # Try to find URL with fallback
        result = self.find_url_with_fallback(
            store_name=store_name,
            country=country,
            review_text=review_text
        )
        
        if not result['success']:
            # All providers failed
            error_msg = result.get('error', 'No providers available')
            self.db.mark_store_not_found(store_id, error_msg, result.get('provider'))
            self.not_found_count += 1
            return {
                'success': False,
                'action': 'not_found',
                'url': None,
                'confidence': None,
                'provider': result.get('provider'),
                'error': error_msg
            }
        
        url = result.get('url')
        confidence = result.get('confidence')
        provider = result.get('provider', 'unknown')
        reasoning = result.get('reasoning', '')
        candidate_results = result.get('results', [])
        
        if not url:
            # No URL found despite success
            error_msg = 'No URL returned from provider'
            self.db.mark_store_not_found(store_id, error_msg, provider)
            self.not_found_count += 1
            return {
                'success': False,
                'action': 'not_found',
                'url': None,
                'confidence': confidence,
                'provider': provider,
                'error': error_msg
            }
        
        # Validate URL if validation is enabled
        validated_url = url
        validation_error = None
        if URL_VALIDATION_ENABLED and self.url_validator:
            validation_result = self.url_validator.validate_url(url)
            if not validation_result['is_valid']:
                logger.warning(
                    f"Store {store_id} ({store_name}): Primary URL validation failed: {url} - "
                    f"{validation_result.get('error')} ({validation_result.get('error_type')})"
                )
                validation_error = validation_result.get('error')
                
                # Try alternative candidates from results
                if candidate_results:
                    logger.info(f"Trying {len(candidate_results)} alternative candidate URLs")
                    for candidate in candidate_results[:5]:  # Try up to 5 candidates
                        # Handle both dict and string formats
                        if isinstance(candidate, dict):
                            candidate_url = candidate.get('url', '')
                        else:
                            candidate_url = str(candidate) if candidate else ''
                        
                        if candidate_url and candidate_url != url:
                            candidate_validation = self.url_validator.validate_url(candidate_url)
                            if candidate_validation['is_valid']:
                                logger.info(
                                    f"Store {store_id} ({store_name}): Found valid alternative URL: {candidate_url}"
                                )
                                validated_url = candidate_url
                                validation_error = None
                                # Update confidence slightly lower for alternative
                                if confidence:
                                    confidence = max(0.0, confidence - 0.1)
                                break
                
                # If no valid candidate found
                if validation_error:
                    error_msg = f"URL validation failed: {validation_error} (error_type: {validation_result.get('error_type')})"
                    if not candidate_results:
                        error_msg += ". No alternative candidates available."
                    else:
                        error_msg += f". Tried {len(candidate_results)} alternatives, all failed validation."
                    
                    # If high confidence but validation failed, mark for review instead of auto-saving
                    if self.should_auto_save(confidence, AUTO_SAVE_THRESHOLD):
                        logger.warning(
                            f"Store {store_id} ({store_name}): High confidence URL failed validation, "
                            f"marking for review instead of auto-saving"
                        )
                        save_candidate_urls(url, candidate_results)
                        self.db.mark_store_needs_review(
                            store_id,
                            error_msg,
                            provider,
                            confidence
                        )
                        self.needs_review_count += 1
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
                        self.db.mark_store_not_found(store_id, error_msg, provider)
                        self.not_found_count += 1
                        return {
                            'success': False,
                            'action': 'not_found',
                            'url': url,
                            'confidence': confidence,
                            'provider': provider,
                            'error': error_msg
                        }
        
        # Determine action based on confidence
        if self.should_auto_save(confidence, AUTO_SAVE_THRESHOLD):
            # High confidence - auto-save (URL is validated if validation enabled)
            try:
                from modules.url_finder import URLFinder
                url_finder = URLFinder(headless=True)
                cleaned_url = url_finder.clean_url(validated_url)
                self.db.update_store_url(
                    store_id,
                    cleaned_url,
                    confidence=confidence,
                    provider=provider
                )
                self.saved_count += 1
                validation_note = " (validated)" if URL_VALIDATION_ENABLED and self.url_validator else ""
                logger.info(
                    f"Store {store_id} ({store_name}): Auto-saved URL {cleaned_url} "
                    f"with {confidence:.2%} confidence via {provider}{validation_note}"
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
                self.db.mark_store_needs_review(
                    store_id,
                    error_msg,
                    provider,
                    confidence
                )
                self.error_count += 1
                return {
                    'success': False,
                    'action': 'error',
                    'url': validated_url,
                    'confidence': confidence,
                    'provider': provider,
                    'error': error_msg
                }
        
        elif confidence is not None and confidence >= LOW_CONFIDENCE_THRESHOLD:
            # Medium confidence - needs review
            reason = f"Low confidence ({confidence:.2%}): {reasoning}"
            # Save candidate URLs for review
            candidate_urls = []
            if candidate_results:
                for candidate in candidate_results[:10]:  # Save up to 10 candidates
                    if isinstance(candidate, dict):
                        candidate_urls.append(candidate.get('url', ''))
                    else:
                        candidate_urls.append(str(candidate))
            if validated_url:
                candidate_urls.insert(0, validated_url)  # Put validated URL first
            self.db.save_store_candidate_urls(store_id, candidate_urls)
            save_candidate_urls(validated_url, candidate_results)
            self.db.mark_store_needs_review(store_id, reason, provider, confidence)
            self.needs_review_count += 1
            logger.info(
                f"Store {store_id} ({store_name}): Marked for review "
                f"({confidence:.2%} confidence via {provider})"
            )
            return {
                'success': True,
                'action': 'needs_review',
                'url': validated_url if validated_url else url,
                'confidence': confidence,
                'provider': provider,
                'error': None
            }
        
        else:
            # Very low confidence or no confidence - mark as not found (but don't save URL)
            # Only mark as not_found if we don't have a valid URL
            confidence_str = f"{confidence:.2%}" if confidence else 'unknown'
            reason = f"Very low confidence ({confidence_str}): {reasoning}"
            
            # If we have a validated URL but low confidence, mark for review instead
            if validated_url and validated_url != url:
                # We found a valid alternative URL but confidence is low - mark for review
                save_candidate_urls(validated_url, candidate_results)
                self.db.mark_store_needs_review(store_id, reason, provider, confidence)
                self.needs_review_count += 1
                logger.info(
                    f"Store {store_id} ({store_name}): Marked for review (low confidence but valid URL found)"
                )
                return {
                    'success': True,
                    'action': 'needs_review',
                    'url': validated_url,
                    'confidence': confidence,
                    'provider': provider,
                    'error': None
                }
            else:
                # No valid URL found - mark as not found
                self.db.mark_store_not_found(store_id, reason, provider)
                self.not_found_count += 1
                logger.info(
                    f"Store {store_id} ({store_name}): Marked as not found "
                    f"({confidence_str} confidence via {provider})"
                )
                return {
                    'success': False,
                    'action': 'not_found',
                    'url': validated_url if validated_url else None,
                    'confidence': confidence,
                    'provider': provider,
                    'error': reason
                }
    
    def run_continuous(self, app_name: Optional[str] = None):
        """Run continuous processing loop"""
        self.running = True
        self.start_time = datetime.now()
        logger.info("Background worker started")
        
        # Unlock stuck stores periodically (every 10 cycles)
        cycle_count = 0
        
        while self.running:
            try:
                # Periodically unlock stuck stores
                cycle_count += 1
                if cycle_count % 10 == 0:
                    try:
                        unlocked = self.db.unlock_stuck_stores(timeout_minutes=30)
                        if unlocked > 0:
                            logger.info(f"Unlocked {unlocked} stuck stores")
                    except Exception as e:
                        logger.warning(f"Failed to unlock stuck stores: {e}")
                
                # Get pending stores (excluding those being processed)
                stores = self.db.get_pending_stores_excluding_processing(
                    limit=WORKER_BATCH_SIZE,
                    app_name=app_name
                )
                
                if not stores:
                    # No stores to process, sleep and continue
                    logger.debug(f"No pending stores, sleeping for {WORKER_SLEEP_SECONDS}s")
                    time.sleep(WORKER_SLEEP_SECONDS)
                    continue
                
                logger.info(f"Processing batch of {len(stores)} stores")
                
                for store in stores:
                    if not self.running:
                        break
                    
                    store_id = store['id']
                    store_name = store.get('store_name', 'Unknown')
                    
                    # Update current store being processed
                    self.current_store_id = store_id
                    self.current_store_name = store_name
                    
                    # Try to lock store for processing
                    if not self.db.lock_store_for_processing(store_id):
                        logger.debug(f"Store {store_id} is already being processed, skipping")
                        self.current_store_id = None
                        self.current_store_name = None
                        continue
                    
                    try:
                        # Check retry limit
                        attempts = store.get('url_finding_attempts', 0)
                        if attempts >= WORKER_MAX_RETRIES:
                            logger.warning(
                                f"Store {store_id} ({store_name}): "
                                f"Max retries ({WORKER_MAX_RETRIES}) reached, marking as needs_review"
                            )
                            self.db.mark_store_needs_review(
                                store_id,
                                f"Max retries ({WORKER_MAX_RETRIES}) reached",
                                None,
                                None
                            )
                            self.db.unlock_store(store_id)
                            continue
                        
                        # Increment attempt counter
                        self.db.increment_url_finding_attempts(store_id)
                        
                        # Process store
                        result = self.process_store(store_id, store)
                        self.processed_count += 1
                        
                        # Unlock store
                        self.db.unlock_store(store_id)
                        
                        # Small delay between stores
                        time.sleep(1)
                        
                    except Exception as e:
                        logger.error(
                            f"Error processing store {store_id} ({store_name}): {e}",
                            exc_info=True
                        )
                        self.error_count += 1
                        self.db.unlock_store(store_id)
                        self.current_store_id = None
                        self.current_store_name = None
                        continue
                
                # Sleep before next batch
                if self.running:
                    time.sleep(WORKER_SLEEP_SECONDS)
            
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                self.error_count += 1
                if self.running:
                    time.sleep(WORKER_SLEEP_SECONDS)
        
        logger.info("Background worker stopped")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current worker status"""
        uptime = None
        if self.start_time:
            uptime = (datetime.now() - self.start_time).total_seconds()
        
        # Calculate rates
        processed_per_hour = None
        if uptime and uptime > 0:
            processed_per_hour = (self.processed_count / uptime) * 3600
        
        success_rate = None
        if self.processed_count > 0:
            success_rate = (self.saved_count / self.processed_count) * 100
        
        current_store = None
        if self.current_store_id:
            current_store = {
                'id': self.current_store_id,
                'name': self.current_store_name or 'Unknown'
            }
        
        # Get pending count
        pending_count = self.db.count_pending_url_stores()
        
        # Get recently processed stores (last 10) - exclude 'not_found' if they have URLs
        conn = self.db.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, store_name, base_url, status, url_confidence, url_finding_provider, updated_at
            FROM stores
            WHERE (
                status IN ('url_verified', 'url_found', 'needs_review')
                OR (status = 'not_found' AND base_url IS NOT NULL AND base_url != '')
            )
            AND updated_at > NOW() - INTERVAL '1 hour'
            ORDER BY updated_at DESC
            LIMIT 10
        """)
        recent_stores = []
        for row in cursor.fetchall():
            recent_stores.append({
                'id': row['id'],
                'store_name': row['store_name'],
                'url': row['base_url'],
                'status': row['status'],
                'confidence': row['url_confidence'],
                'provider': row['url_finding_provider'],
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
            })
        conn.close()
        
        return {
            'running': self.running,
            'processed_count': self.processed_count,
            'saved_count': self.saved_count,
            'needs_review_count': self.needs_review_count,
            'not_found_count': self.not_found_count,
            'error_count': self.error_count,
            'pending_count': pending_count,
            'uptime_seconds': uptime,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'processed_per_hour': processed_per_hour,
            'success_rate_percent': success_rate,
            'current_store': current_store,
            'recent_stores': recent_stores
        }
    
    def stop(self):
        """Stop the worker"""
        self.running = False
        logger.info("Worker stop requested")
