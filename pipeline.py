"""
Job orchestrator.

One background thread per job: scrape reviews → find URLs → scrape emails.
Control via start_job() / stop_job() / get_status().
"""
import logging
import threading
import time
from typing import Dict, Optional

import config
import database as db
from finders.gemini_finder import GeminiFinder
from finders.gemini_email_finder import GeminiEmailFinder
from finders.serper_search import SerperSearch
from scrapers.email_scraper import EmailScraper
from scrapers.review_scraper import ReviewScraper, extract_app_name
from utils.ai_email_filter import AIEmailFilter
from utils.country_utils import country_to_iso_code

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
            logger.info("Job %d is already running — ignoring start request", job_id)
            return
        event = threading.Event()
        _stop_events[job_id] = event

    t = threading.Thread(target=_run_job, args=(job_id, event), daemon=True, name=f"job-{job_id}")
    t.start()
    logger.info("Job %d started (thread %s)", job_id, t.name)


def stop_job(job_id: int) -> None:
    with _lock:
        ev = _stop_events.get(job_id)
    if ev:
        ev.set()
        logger.info("Job %d stop signal sent", job_id)


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


def _upsert_batch(job_id: int, batch: list) -> tuple:
    """Insert a batch of review dicts. Returns (inserted, skipped)."""
    inserted = skipped = 0
    for review in batch:
        store_id = db.upsert_store(job_id, review)
        if store_id:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def _run_job(job_id: int, stop: threading.Event) -> None:
    logger.info("=" * 60)
    logger.info("Job %d: pipeline starting", job_id)
    try:
        db.update_job(job_id, status="running")
        job = db.get_job(job_id)
        limit = job.get("limit_count") or 0
        app_url = job["app_url"]

        logger.info("Job %d: app_url=%s  limit=%s", job_id, app_url, limit or "unlimited")

        # Init components — fail fast if API keys are missing
        logger.info("Job %d: initialising Serper search…", job_id)
        try:
            serper = SerperSearch()
            logger.info("Job %d: Serper ready (%d key(s))", job_id, len(config.SERPER_API_KEYS))
        except Exception as e:
            logger.error("Job %d: FAILED to init Serper: %s", job_id, e)
            db.update_job(job_id, status="failed", error=f"Serper init failed: {e}")
            return

        logger.info("Job %d: initialising Gemini finder…", job_id)
        try:
            finder = GeminiFinder()
            logger.info("Job %d: Gemini finder ready (model=%s)", job_id, config.GEMINI_MODEL)
        except Exception as e:
            logger.error("Job %d: FAILED to init Gemini finder: %s", job_id, e)
            db.update_job(job_id, status="failed", error=f"Gemini init failed: {e}")
            return

        try:
            gemini_email_finder = GeminiEmailFinder()
            logger.info("Job %d: Gemini email finder ready", job_id)
        except Exception as e:
            logger.warning("Job %d: Gemini email finder init failed: %s — AI email search disabled", job_id, e)
            gemini_email_finder = None

        email_scraper = EmailScraper()
        ai_filter = _build_ai_filter()
        review_scraper = ReviewScraper()

        app_name = job.get("app_name") or extract_app_name(app_url)
        db.update_job(job_id, app_name=app_name)

        cursor = job.get("scrape_cursor", 0)
        total_reviews = job.get("total_reviews_found", 0)  # cumulative across runs
        inserted_this_run = 0

        # ----------------------------------------------------------------
        # Phase A: new review sweep from page 1 (only when resuming)
        # Scan forward until we hit a page that contains an already-known
        # store — that's the boundary between new and old content.
        # ----------------------------------------------------------------
        if cursor > 0:
            logger.info("Job %d: [Phase A] sweeping from page 1 for new reviews (cursor=%d)", job_id, cursor)
            for page, batch in review_scraper.scrape(app_url, start_page=1):
                if _should_stop(stop):
                    db.update_job(job_id, status="paused")
                    return

                inserted, skipped = _upsert_batch(job_id, batch)
                inserted_this_run += inserted
                total_reviews += len(batch)
                db.update_job(job_id, total_reviews_found=total_reviews)
                logger.info(
                    "Job %d: [Phase A] page %d — new=%d dup=%d run_total=%d",
                    job_id, page, inserted, skipped, inserted_this_run,
                )

                _process_pending(job_id, stop, serper, finder, gemini_email_finder, email_scraper, ai_filter, drain=False)
                if _should_stop(stop):
                    db.update_job(job_id, status="paused")
                    return

                if limit and inserted_this_run >= limit:
                    db.update_job(job_id, status="paused")
                    return

                # Found existing reviews on this page → we've reached the old boundary.
                # Jump to Phase B from cursor+1.
                if skipped > 0:
                    logger.info(
                        "Job %d: [Phase A] boundary hit at page %d — jumping to cursor+1=%d",
                        job_id, page, cursor + 1,
                    )
                    break

                # Phase A naturally advanced past the cursor (lots of new reviews).
                # Phase B will start right after the last Phase A page.
                if page >= cursor:
                    cursor = page
                    logger.info("Job %d: [Phase A] passed cursor naturally at page %d", job_id, page)
                    break

        # ----------------------------------------------------------------
        # Phase B: continue from cursor+1 (main forward scrape)
        # ----------------------------------------------------------------
        logger.info("Job %d: [Phase B] resuming from page %d", job_id, cursor + 1)
        for page, batch in review_scraper.scrape(app_url, start_page=cursor + 1):
            if _should_stop(stop):
                db.update_job(job_id, status="paused")
                return

            inserted, skipped = _upsert_batch(job_id, batch)
            inserted_this_run += inserted
            total_reviews += len(batch)
            db.update_job(job_id, total_reviews_found=total_reviews)

            # Advance cursor after each completed page
            db.update_scrape_cursor(job_id, page)

            logger.info(
                "Job %d: [Phase B] page %d — new=%d dup=%d run_total=%d",
                job_id, page, inserted, skipped, inserted_this_run,
            )

            _process_pending(job_id, stop, serper, finder, gemini_email_finder, email_scraper, ai_filter, drain=False)
            if _should_stop(stop):
                db.update_job(job_id, status="paused")
                return

            if limit and inserted_this_run >= limit:
                logger.info("Job %d: [Phase B] limit=%d reached after %d new stores", job_id, limit, inserted_this_run)
                break

        logger.info("Job %d: review scraping done — %d new stores this run, %d total", job_id, inserted_this_run, total_reviews)

        # ----------------------------------------------------------------
        # Phase 2+3: drain all remaining pending stores
        # ----------------------------------------------------------------
        pending_count = db.count_pending(job_id)
        logger.info("Job %d: [Phase 2+3] draining %d pending stores", job_id, pending_count)
        _process_pending(job_id, stop, serper, finder, gemini_email_finder, email_scraper, ai_filter, drain=True)

        if _should_stop(stop):
            logger.info("Job %d: stop requested after drain", job_id)
            db.update_job(job_id, status="paused")
        else:
            final_stats = db.get_job_stats(job_id)
            logger.info(
                "Job %d: COMPLETED — emails_found=%s  no_emails=%s  url_not_found=%s  failed=%s",
                job_id,
                final_stats.get("emails_found", 0),
                final_stats.get("no_emails", 0),
                final_stats.get("url_not_found", 0),
                final_stats.get("failed", 0),
            )
            db.update_job(job_id, status="completed")

    except Exception as e:
        logger.error("Job %d: UNHANDLED ERROR: %s", job_id, e, exc_info=True)
        db.update_job(job_id, status="failed", error=str(e))
    finally:
        with _lock:
            _stop_events.pop(job_id, None)
        logger.info("Job %d: pipeline thread exiting", job_id)
        logger.info("=" * 60)


def _process_pending(
    job_id: int,
    stop: threading.Event,
    serper: "SerperSearch",
    finder: GeminiFinder,
    gemini_email_finder: Optional["GeminiEmailFinder"],
    email_scraper: EmailScraper,
    ai_filter: Optional["AIEmailFilter"],
    drain: bool = False,
) -> None:
    processed = 0
    while True:
        if _should_stop(stop):
            return

        store = db.get_next_pending_store(job_id)
        if not store:
            if drain:
                logger.info("Job %d: no more pending stores", job_id)
            break

        logger.info(
            "Job %d: processing store #%d %r (drain=%s)",
            job_id, store["id"], store["store_name"], drain,
        )
        terminal = _process_store(store, serper, finder, gemini_email_finder, email_scraper, ai_filter)
        # Only count stores that reached a final state — not retries reset to pending
        if terminal:
            db.increment_stores_processed(job_id)
        processed += 1

        if not drain:
            break  # interleaved mode: process one then let the review loop continue

        # Pace calls to avoid rate limits
        if not _should_stop(stop):
            time.sleep(config.INTER_STORE_DELAY)

    if processed and drain:
        logger.info("Job %d: drained %d stores in this pass", job_id, processed)


def _process_store(
    store: Dict,
    serper: "SerperSearch",
    finder: GeminiFinder,
    gemini_email_finder: Optional["GeminiEmailFinder"],
    email_scraper: EmailScraper,
    ai_filter: Optional["AIEmailFilter"],
) -> bool:
    """
    Process one store through URL finding + email scraping.
    Returns True if the store reached a terminal state (done, no matter the outcome).
    Returns False if the store was reset to pending for a retry.
    """
    store_id   = store["id"]
    store_name = store["store_name"]
    country    = store.get("country") or ""
    job_id     = store["job_id"]

    # Track attempt
    attempt = db.increment_attempt_count(store_id)
    logger.info("  Store #%d %r — attempt %d/%d", store_id, store_name, attempt, config.STORE_MAX_ATTEMPTS)

    # ---- Phase 2: Find URL via Serper + Gemini ----------------------------
    logger.info("  [URL] finding URL for %r (country=%r)", store_name, country)
    try:
        url, confidence = _find_url(store_name, country, serper, finder)
        logger.info("  [URL] result: url=%s  confidence=%.2f  threshold=%.2f",
                    url, confidence, config.URL_CONFIDENCE_THRESHOLD)
    except Exception as e:
        logger.error("  [URL] FAILED for %r: %s", store_name, e)
        return _mark_failed_or_retry(store_id, job_id, f"URL finder: {e}", attempt)

    if not url or confidence < config.URL_CONFIDENCE_THRESHOLD:
        logger.info("  [URL] below threshold — marking url_not_found for %r", store_name)
        db.update_store(store_id, status="url_not_found", store_url=url, url_confidence=confidence)
        return True  # terminal

    db.update_store(store_id, status="url_found", store_url=url, url_confidence=confidence)

    # ---- Phase 3: Scrape emails -------------------------------------------
    logger.info("  [EMAIL] scraping %s for %r", url, store_name)
    try:
        raw_emails = email_scraper.scrape(url)
        logger.info("  [EMAIL] found %d raw emails: %s", len(raw_emails), raw_emails)
    except Exception as e:
        logger.error("  [EMAIL] FAILED for %s: %s", url, e)
        return _mark_failed_or_retry(store_id, job_id, f"Email scraper: {e}", attempt)

    # ---- Gemini email search fallback ------------------------------------
    if not raw_emails and gemini_email_finder:
        logger.info("  [EMAIL] web scrape found nothing — trying Gemini email search for %r", store_name)
        try:
            gemini_emails = gemini_email_finder.find(store_name, store_url=url)
            if gemini_emails:
                logger.info("  [EMAIL] Gemini found %d email(s): %s", len(gemini_emails), gemini_emails)
                raw_emails = gemini_emails
        except Exception as e:
            logger.warning("  [EMAIL] Gemini email search failed (%s) — continuing with no emails", e)

    if not raw_emails:
        logger.info("  [EMAIL] no emails found at %s", url)
        db.update_store(store_id, status="no_emails", emails=[])
        return True  # terminal

    # ---- AI filter --------------------------------------------------------
    final_emails = raw_emails
    if ai_filter:
        logger.info("  [AI] filtering %d raw emails…", len(raw_emails))
        try:
            filtered = ai_filter.filter(raw_emails, store_url=url, store_name=store_name)
            final_emails = filtered or raw_emails
            logger.info("  [AI] %d → %d emails after filter", len(raw_emails), len(final_emails))
        except Exception as e:
            logger.warning("  [AI] filter failed (%s) — keeping raw emails", e)

    db.update_store(store_id, status="emails_found", emails=final_emails)
    logger.info("  Store %r done → %d email(s): %s", store_name, len(final_emails), final_emails)
    return True  # terminal


def _find_url(
    store_name: str,
    country: str,
    serper: "SerperSearch",
    finder: GeminiFinder,
) -> tuple:
    """
    Two-step URL finding:
      1. Serper search (geo-accurate) → top-10 results
      2. Gemini reasons over those results → picks best URL
         (skipped when the top Serper result is an obvious match)

    Falls back to a second Serper query (shopify-specific) if first
    attempt returns low confidence.
    """
    country_code = country_to_iso_code(country)

    # Primary query
    primary_query = f'"{store_name}" store website'
    results = serper.search(primary_query, country_code=country_code)

    # Fast path: skip Gemini if the top result is an obvious name match.
    # This saves ~60% of Gemini calls and eliminates the 429 cascade.
    if results:
        fast_url, fast_conf = _obvious_match(store_name, results[0])
        if fast_url:
            logger.info(
                "  [URL] obvious match (no Gemini needed): url=%s conf=%.2f", fast_url, fast_conf
            )
            return fast_url, fast_conf

    url, confidence = finder.find_from_results(store_name, country, results)
    logger.info(
        "  [URL] primary query=%r gl=%r → url=%s conf=%.2f",
        primary_query, country_code, url, confidence or 0.0,
    )

    # Fallback: shopify-specific query if below threshold
    if not url or (confidence or 0.0) < config.URL_CONFIDENCE_THRESHOLD:
        fallback_query = f'"{store_name}" shopify online store'
        results2 = serper.search(fallback_query, country_code=country_code)

        # Try obvious-match on fallback top result too
        if results2:
            fast_url2, fast_conf2 = _obvious_match(store_name, results2[0])
            if fast_url2 and fast_conf2 > (confidence or 0.0):
                logger.info(
                    "  [URL] obvious match on fallback: url=%s conf=%.2f", fast_url2, fast_conf2
                )
                return fast_url2, fast_conf2

        url2, conf2 = finder.find_from_results(store_name, country, results2)
        logger.info(
            "  [URL] fallback query=%r gl=%r → url=%s conf=%.2f",
            fallback_query, country_code, url2, conf2 or 0.0,
        )
        if (conf2 or 0.0) > (confidence or 0.0):
            url, confidence = url2, conf2

    return url, float(confidence or 0.0)


def _obvious_match(store_name: str, result: dict) -> tuple:
    """
    Return (url, confidence=0.9) when the first Serper result strongly
    matches the store name without needing Gemini reasoning.

    Matching rules (all case-insensitive):
      - Store name appears verbatim in the result URL, OR
      - Store name appears verbatim in the result title AND the URL
        doesn't look like a marketplace / directory (amazon/etsy/ebay/yelp/facebook…)

    Returns ("", 0.0) if no obvious match.
    """
    _SKIP_DOMAINS = (
        # Social media
        "facebook.", "instagram.", "tiktok.", "twitter.", "x.com",
        "linkedin.", "youtube.", "pinterest.", "snapchat.",
        # Forums / Q&A / community
        "reddit.", "quora.", "tumblr.",
        # Blogging platforms
        "medium.", "substack.", "blogspot.", "wordpress.com",
        # Encyclopaedias / news
        "wikipedia.", "businessinsider.", "forbes.", "buzzfeed.",
        # Marketplaces
        "amazon.", "aliexpress.", "etsy.", "ebay.",
        "walmart.", "wish.", "alibaba.",
        # Review / directory sites
        "yelp.", "trustpilot.", "sitejabber.",
        "bbb.org", "yellowpages.", "foursquare.",
        # Search engines
        "google.", "bing.",
        # Shopify analytics / data-broker sites (not the store itself)
        "merchantgenius.io", "myip.ms", "similarweb.",
        "semrush.", "ahrefs.", "spyfu.", "builtwith.", "wappalyzer.",
        # Shopify's own domains
        "apps.shopify", "shopify.com",
    )

    link    = (result.get("link") or "").lower()
    title   = (result.get("title") or "").lower()
    name_lc = store_name.lower().strip()

    if not link or not name_lc:
        return "", 0.0

    # Skip aggregator/marketplace domains
    if any(skip in link for skip in _SKIP_DOMAINS):
        return "", 0.0

    # Normalise store name for URL matching (spaces → hyphens or no-space)
    name_slug  = name_lc.replace(" ", "-")
    name_nospace = name_lc.replace(" ", "")

    url_match = (
        name_lc    in link or
        name_slug  in link or
        name_nospace in link
    )
    title_match = name_lc in title

    if url_match and title_match:
        raw_url = result.get("link", "")
        return raw_url, 0.92
    if url_match:
        raw_url = result.get("link", "")
        return raw_url, 0.85

    return "", 0.0


def _mark_failed_or_retry(store_id: int, job_id: int, error: str, attempt: int) -> bool:
    """
    Mark store as failed permanently, or reset to pending if under retry limit.
    Returns True if permanently failed (terminal), False if reset to pending (will retry).
    """
    if attempt < config.STORE_MAX_ATTEMPTS:
        logger.info("  Store #%d: attempt %d/%d failed (%s) — will retry", store_id, attempt, config.STORE_MAX_ATTEMPTS, error)
        db.update_store(store_id, status="pending", error=error)
        return False  # not terminal — will be retried
    else:
        logger.warning("  Store #%d: exhausted %d attempts — marking failed: %s", store_id, attempt, error)
        db.update_store(store_id, status="failed", error=error)
        return True  # terminal


def _build_ai_filter() -> Optional["AIEmailFilter"]:
    if not config.OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set — AI email filter disabled")
        return None
    try:
        f = AIEmailFilter()
        logger.info("AI email filter ready (model=%s)", config.OPENAI_MODEL)
        return f
    except Exception as e:
        logger.warning("Could not init AIEmailFilter: %s", e)
        return None
