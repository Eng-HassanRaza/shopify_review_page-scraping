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

        prompt = (
            "Find the official Shopify storefront homepage URL for this store.\n"
            "Use Google Search to verify. Prefer the main homepage, not social profiles, "
            "directories, or app stores.\n\n"
            "Return EXACTLY this format (no extra lines):\n"
            "SELECTED_URL: <url>\n"
            "CONFIDENCE: <0.0 to 1.0>\n"
            "REASONING: <one sentence>\n\n"
            f"store_name: {store_name}\n"
            f"country: {country or 'unknown'}\n"
        )

        last_err = None
        for attempt in range(config.GEMINI_MAX_RETRIES + 1):
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
                if self._is_rate_limit(e) and attempt < config.GEMINI_MAX_RETRIES:
                    self._backoff(attempt)
                    continue
                raise
            except Exception as e:
                last_err = e
                if self._is_rate_limit(e) and attempt < config.GEMINI_MAX_RETRIES:
                    self._backoff(attempt)
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

        if url is None:
            confidence = 0.0
        elif confidence is None:
            confidence = 0.5

        logger.info("GeminiFinder store=%r → url=%s conf=%.2f", store_name, url, confidence)
        return url, float(confidence)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).upper()
        return "429" in s or "RESOURCE_EXHAUSTED" in s or "RATE_LIMIT" in s

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(config.GEMINI_RETRY_DELAY * (2 ** attempt), 30.0)
        logger.warning("Gemini 429 — retrying in %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)
