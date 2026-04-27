"""
Pick the correct store URL from Serper search results using Gemini.
Gemini no longer runs its own search — it only reasons over results
provided by SerperSearch, so there is no server geo-bias.
"""
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

from google import genai
from google.genai import types, errors

import config
from utils.url_utils import normalize_url

logger = logging.getLogger(__name__)

_IGNORED_HOSTS = (
    # Google infrastructure
    "vertexaisearch.cloud.google.com",
    "webcache.googleusercontent.com",
    "google.com",
    "bing.com",
    # Social media
    "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "pinterest.com", "snapchat.com",
    # Forums / Q&A / community
    "reddit.com", "quora.com", "tumblr.com",
    "stackexchange.com", "stackoverflow.com",
    # Blogging platforms (not the store's own site)
    "medium.com", "substack.com", "blogspot.com", "wordpress.com",
    # Encyclopaedias / news
    "wikipedia.org", "businessinsider.com", "forbes.com",
    "techcrunch.com", "buzzfeed.com", "huffpost.com",
    # Marketplaces
    "amazon.", "aliexpress.", "etsy.com", "ebay.com",
    "walmart.com", "wish.com", "dhgate.com", "alibaba.com",
    # Review / directory sites
    "yelp.com", "trustpilot.com", "sitejabber.com",
    "bbb.org", "glassdoor.com", "indeed.com",
    "yellowpages.com", "foursquare.com",
    # Shopify analytics / data-broker sites (not the store itself)
    "merchantgenius.io", "myip.ms", "similarweb.com",
    "semrush.com", "ahrefs.com", "spyfu.com",
    "builtwith.com", "wappalyzer.com",
    # Shopify's own domains (we want the merchant's site, not Shopify)
    "apps.shopify.com", "shopify.com",
)


def _is_ignored(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host or "grounding-api-redirect" in url:
        return True
    return any(part in host for part in _IGNORED_HOSTS)


def _format_results_for_prompt(results: List[Dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. Title: {r['title']}")
        lines.append(f"   URL: {r['link']}")
        if r.get("snippet"):
            lines.append(f"   Snippet: {r['snippet'][:150]}")
    return "\n".join(lines)


class GeminiFinder:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    def find_from_results(
        self,
        store_name: str,
        country: str,
        search_results: List[Dict],
    ) -> Tuple[Optional[str], float]:
        """
        Given a list of search results from Serper, ask Gemini to pick
        the best URL for this store. No Google Search grounding — pure reasoning.

        Returns (url, confidence).
        """
        if not search_results:
            return None, 0.0

        # Filter out ignored hosts from the candidate list upfront
        candidates = [r for r in search_results if not _is_ignored(r["link"])]
        if not candidates:
            return None, 0.0

        results_text = _format_results_for_prompt(candidates)

        prompt = (
            "You are verifying which search result is the official e-commerce store homepage.\n\n"
            f"Store Name: {store_name}\n"
            f"Country: {country or 'unknown'}\n\n"
            "Search results:\n"
            f"{results_text}\n\n"
            "Instructions:\n"
            "- Pick the result that is most likely the official Shopify/e-commerce homepage for this exact store\n"
            "- Prefer URLs that match the store name\n"
            "- Reject social media, marketplaces, review sites, news articles\n"
            "- If none of the results match this specific store, return NONE\n\n"
            "Return EXACTLY this format (no extra lines, no markdown):\n"
            "SELECTED_URL: <url or NONE>\n"
            "CONFIDENCE: <0.0 to 1.0>\n"
            "REASONING: <one sentence>\n"
        )

        last_err = None
        response = None
        for attempt in range(config.GEMINI_MAX_RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=200,
                        # No google_search tool — Gemini only reasons over provided data
                    ),
                )
                last_err = None
                break
            except (errors.ClientError, Exception) as e:
                last_err = e
                if self._is_rate_limit(e) and attempt < config.GEMINI_MAX_RETRIES:
                    self._backoff(attempt)
                    continue
                if not self._is_rate_limit(e):
                    raise  # non-rate-limit errors bubble up immediately
                # rate limit on final attempt — fall through to graceful degrade

        if last_err is not None:
            # All retries exhausted on 429 — degrade gracefully:
            # return the first non-ignored Serper candidate at reduced confidence
            # rather than failing the store entirely.
            logger.warning(
                "GeminiFinder rate limit exhausted for %r — using first Serper candidate",
                store_name,
            )
            for r in candidates:
                fallback = normalize_url(r["link"])
                if fallback and not _is_ignored(fallback):
                    logger.info(
                        "GeminiFinder store=%r → fallback url=%s conf=0.55 (Gemini unavailable)",
                        store_name, fallback,
                    )
                    return fallback, 0.55
            return None, 0.0

        text = getattr(response, "text", "") or ""
        url, confidence = self._parse_response(text, candidates)

        if url and _is_ignored(url):
            url = None

        if url is None:
            confidence = 0.0
        elif confidence is None:
            # Gemini didn't return a confidence — use position in results as proxy
            for i, r in enumerate(candidates):
                if r["link"] == url:
                    confidence = max(0.4, 0.9 - i * 0.1)
                    break
            else:
                confidence = 0.4

        logger.info(
            "GeminiFinder store=%r → url=%s conf=%.2f",
            store_name, url, float(confidence),
        )
        return url, float(confidence)

    @staticmethod
    def _parse_response(
        text: str, candidates: List[Dict]
    ) -> Tuple[Optional[str], Optional[float]]:
        url = conf = None

        m = re.search(r"^\s*SELECTED_URL:\s*(.+)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            raw = m.group(1).strip()
            if raw.upper() != "NONE":
                url = normalize_url(raw)

        m = re.search(r"^\s*CONFIDENCE:\s*([01](?:\.\d+)?)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                conf = float(m.group(1))
            except ValueError:
                pass

        # Fallback: if Gemini didn't follow the format, use first candidate
        if not url:
            for r in candidates:
                candidate = normalize_url(r["link"])
                if candidate and not _is_ignored(candidate):
                    url = candidate
                    break

        return url, conf

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).upper()
        return "429" in s or "RESOURCE_EXHAUSTED" in s or "RATE_LIMIT" in s

    @staticmethod
    def _backoff(attempt: int) -> None:
        # Hard floor of 15 s so a misconfigured GEMINI_RETRY_DELAY env var
        # can't cause a 429 storm on the free-tier (15 RPM) plan.
        configured = config.GEMINI_RETRY_DELAY * (2 ** attempt)
        delay = min(max(15.0, configured), 120.0)
        logger.warning("Gemini 429 — retrying in %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)
