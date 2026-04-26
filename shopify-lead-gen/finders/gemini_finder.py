"""Find a Shopify store URL from a store name using Gemini + Google Search."""
import logging
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

from google import genai
from google.genai import types, errors

import config
from utils.url_utils import normalize_url

logger = logging.getLogger(__name__)

_IGNORED_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "webcache.googleusercontent.com",
    "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "pinterest.com", "wikipedia.org", "amazon.", "aliexpress.",
    "etsy.com", "ebay.com", "walmart.com", "apps.shopify.com",
    "shopify.com/blog", "help.shopify.com",
)


def _is_ignored(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host:
        return True
    if "grounding-api-redirect" in url:
        return True
    return any(part in host for part in _IGNORED_HOSTS)


def _parse_response(text: str) -> Tuple[Optional[str], Optional[float]]:
    """Extract SELECTED_URL and CONFIDENCE from Gemini's line-based response."""
    url = conf = None

    m = re.search(r"^\s*SELECTED_URL:\s*(.+)\s*$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        url = normalize_url(m.group(1).strip())

    m = re.search(r"^\s*CONFIDENCE:\s*([01](?:\.\d+)?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        try:
            conf = float(m.group(1))
        except ValueError:
            pass

    if not url:
        # Fallback: first https URL in the text that isn't ignored
        for m in re.finditer(r"https?://[^\s<>()\"']+", text, re.IGNORECASE):
            candidate = normalize_url(m.group(0))
            if candidate and not _is_ignored(candidate):
                url = candidate
                break

    return url, conf


class GeminiFinder:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    def find(self, store_name: str, country: str = "") -> Tuple[Optional[str], float]:
        """
        Returns (url, confidence).
        url is None if nothing plausible was found.
        confidence is 0.0–1.0.
        """
        store_name = (store_name or "").strip()
        if not store_name:
            return None, 0.0

        # Primary attempt
        url, confidence = self._query(store_name, country, attempt=1)

        # Fallback: if confidence is below threshold, retry with a different query angle
        if (url is None or confidence is None or confidence < config.URL_CONFIDENCE_THRESHOLD):
            logger.info("GeminiFinder: primary attempt low confidence (%.2f), trying fallback query", confidence or 0.0)
            url2, conf2 = self._query(store_name, country, attempt=2)
            if conf2 is not None and conf2 > (confidence or 0.0):
                url, confidence = url2, conf2
                logger.info("GeminiFinder: fallback improved confidence to %.2f", confidence)

        if url and _is_ignored(url):
            url = None

        if url is None:
            confidence = 0.0
        elif confidence is None:
            confidence = 0.5

        logger.info("GeminiFinder store=%r → url=%s conf=%.2f", store_name, url, float(confidence))
        return url, float(confidence)

    def _query(self, store_name: str, country: str, attempt: int) -> Tuple[Optional[str], Optional[float]]:
        if attempt == 1:
            prompt = (
                "You are a research assistant finding e-commerce store websites.\n\n"
                f"Find the official online store / e-commerce homepage URL for this Shopify merchant:\n"
                f"Store Name: {store_name}\n"
                f"Country: {country or 'unknown'}\n\n"
                "Instructions:\n"
                "- Use Google Search to find their actual website\n"
                "- Prefer their main homepage (e.g. https://storename.com), NOT social profiles, NOT marketplaces like Amazon/Etsy/eBay\n"
                "- If they sell on Shopify, the URL often ends in .myshopify.com OR they have a custom domain\n"
                "- Only return a URL you are confident belongs to this specific store\n\n"
                "Return EXACTLY this format (no extra lines, no markdown):\n"
                "SELECTED_URL: <url or NONE>\n"
                "CONFIDENCE: <0.0 to 1.0>\n"
                "REASONING: <one sentence>\n"
            )
        else:
            # Fallback: narrower Shopify-specific search
            prompt = (
                "You are a research assistant. Search Google specifically for:\n"
                f'site:myshopify.com OR shopify "{store_name}"\n\n'
                f"Store Name: {store_name}\n"
                f"Country: {country or 'unknown'}\n\n"
                "Find the Shopify storefront URL. The URL should look like:\n"
                "- https://store-name.myshopify.com  OR\n"
                "- https://www.storename.com (custom domain Shopify store)\n\n"
                "Return EXACTLY this format:\n"
                "SELECTED_URL: <url or NONE>\n"
                "CONFIDENCE: <0.0 to 1.0>\n"
                "REASONING: <one sentence>\n"
            )

        last_err = None
        for retry in range(config.GEMINI_MAX_RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}],
                        temperature=0.1,
                        max_output_tokens=300,
                    ),
                )
                break
            except errors.ClientError as e:
                last_err = e
                if self._is_rate_limit(e) and retry < config.GEMINI_MAX_RETRIES:
                    self._backoff(retry)
                    continue
                raise
            except Exception as e:
                last_err = e
                if self._is_rate_limit(e) and retry < config.GEMINI_MAX_RETRIES:
                    self._backoff(retry)
                    continue
                raise
        else:
            raise ValueError(
                f"Gemini rate limit exceeded after {config.GEMINI_MAX_RETRIES + 1} attempts"
            ) from last_err

        text = getattr(response, "text", "") or ""
        url, confidence = _parse_response(text)

        if url and _is_ignored(url):
            url = None

        return url, confidence

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).upper()
        return "429" in s or "RESOURCE_EXHAUSTED" in s or "RATE_LIMIT" in s

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(config.GEMINI_RETRY_DELAY * (2 ** attempt), 60.0)
        logger.warning("Gemini 429 — retrying in %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)
