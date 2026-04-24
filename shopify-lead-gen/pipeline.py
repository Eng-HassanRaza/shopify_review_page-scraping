"""
Job orchestrator.

One background thread per job: scrape reviews → find URLs → scrape emails.
Control via start_job() / stop_job() / get_status().
"""
import logging
import threading
from typing import Dict, Optional

import config
import database as db
from finders.gemini_finder import GeminiFinder
from scrapers.email_scraper import EmailScraper
from scrapers.review_scraper import ReviewScraper, extract_app_name
from utils.ai_email_filter import AIEmailFilter

logger = logging.getLogger(__name__)

# job_id → threading.Event (set = stop requested)
_stop_events: Dict[int, threading.Event] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_job(job_id: int) -> None:
    with _lock:
        if job_id in _stop_events:
            return  # already running
        event = threading.Event()
        _stop_events[job_id] = event

    t = threading.Thread(target=_run_job, args=(job_id, event), daemon=True)
    t.start()


def stop_job(job_id: int) -> None:
    with _lock:
        ev = _stop_events.get(job_id)
    if ev:
        ev.set()


def is_running(job_id: int) -> bool:
    with _lock:
        ev = _stop_events.get(job_id)
    return ev is not None and not ev.is_set()


def get_status(job_id: int) -> Dict:
    job = db.get_job(job_id)
    if not job:
        return {}
    stats = db.get_job_stats(job_id)
    return {**job, "stats": stats, "running": is_running(job_id)}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _should_stop(event: threading.Event) -> bool:
    return event.is_set()


def _run_job(job_id: int, stop: threading.Event) -> None:
    try:
        db.update_job(job_id, status="running")
        job = db.get_job(job_id)
        limit = job.get("limit_count") or 0

        finder = GeminiFinder()
        email_scraper = EmailScraper()
        ai_filter = _build_ai_filter()
        review_scraper = ReviewScraper()

        app_name = job.get("app_name") or extract_app_name(job["app_url"])
        db.update_job(job_id, app_name=app_name)

        # --- Phase 1: scrape reviews and immediately process each batch ---
        total_reviews = 0
        for batch in review_scraper.scrape(job["app_url"], limit=limit):
            if _should_stop(stop):
                db.update_job(job_id, status="paused")
                return

            inserted = 0
            for review in batch:
                store_id = db.upsert_store(job_id, review)
                if store_id:
                    inserted += 1

            total_reviews += len(batch)
            db.update_job(job_id, total_reviews_found=total_reviews)

            # Process newly inserted stores immediately (interleaved pipeline)
            _process_pending(job_id, stop, finder, email_scraper, ai_filter)
            if _should_stop(stop):
                db.update_job(job_id, status="paused")
                return

        # --- Final pass: process any stores still pending ---
        _process_pending(job_id, stop, finder, email_scraper, ai_filter, drain=True)

        if _should_stop(stop):
            db.update_job(job_id, status="paused")
        else:
            db.update_job(job_id, status="completed")

    except Exception as e:
        logger.error("Job %d failed: %s", job_id, e, exc_info=True)
        db.update_job(job_id, status="failed", error=str(e))
    finally:
        with _lock:
            _stop_events.pop(job_id, None)


def _process_pending(
    job_id: int,
    stop: threading.Event,
    finder: GeminiFinder,
    email_scraper: EmailScraper,
    ai_filter: Optional["AIEmailFilter"],
    drain: bool = False,
) -> None:
    """Process all stores currently in 'pending' status."""
    while True:
        if _should_stop(stop):
            return

        store = db.get_next_pending_store(job_id)
        if not store:
            break  # nothing left to process right now

        _process_store(store, finder, email_scraper, ai_filter)

        processed = db.get_job(job_id).get("stores_processed", 0) + 1
        db.update_job(job_id, stores_processed=processed)

        if not drain:
            break  # in interleaved mode, process one then check for new scraped reviews


def _process_store(
    store: Dict,
    finder: GeminiFinder,
    email_scraper: EmailScraper,
    ai_filter: Optional["AIEmailFilter"],
) -> None:
    store_id = store["id"]
    store_name = store["store_name"]
    country = store.get("country", "")

    # Phase 2: Find URL
    try:
        url, confidence = finder.find(store_name, country)
    except Exception as e:
        logger.warning("URL finding failed for %r: %s", store_name, e)
        db.update_store(store_id, status="failed", error=f"URL finder: {e}")
        return

    if not url or confidence < config.URL_CONFIDENCE_THRESHOLD:
        db.update_store(store_id, status="url_not_found", store_url=url, url_confidence=confidence)
        return

    db.update_store(store_id, status="url_found", store_url=url, url_confidence=confidence)

    # Phase 3: Scrape emails
    try:
        raw_emails = email_scraper.scrape(url)
    except Exception as e:
        logger.warning("Email scraping failed for %s: %s", url, e)
        db.update_store(store_id, status="failed", error=f"Email scraper: {e}")
        return

    if not raw_emails:
        db.update_store(store_id, status="no_emails", emails=[])
        return

    emails = raw_emails
    if ai_filter:
        try:
            emails = ai_filter.filter(raw_emails, store_url=url, store_name=store_name)
        except Exception as e:
            logger.warning("AI filter failed for %s: %s — using raw emails", url, e)

    final_emails = emails or raw_emails
    db.update_store(store_id, status="emails_found", emails=final_emails)
    logger.info("Store %r → %d email(s)", store_name, len(final_emails))


def _build_ai_filter() -> Optional["AIEmailFilter"]:
    if not config.OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set — skipping AI email filter")
        return None
    try:
        return AIEmailFilter()
    except Exception as e:
        logger.warning("Could not init AIEmailFilter: %s", e)
        return None
