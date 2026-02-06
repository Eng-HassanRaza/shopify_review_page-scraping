"""Gemini (Google GenAI SDK) client for URL finding using Google Search tool."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from urllib.parse import urlparse

import requests

from google import genai
from google.genai import types
from google.genai import errors

logger = logging.getLogger(__name__)


class GeminiSearch:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout: int = 20,
        top_n: int = 5,
        cache_ttl_seconds: int = 86400,
        verify_shopify: bool = True,
        max_retries: int = 3,
        initial_retry_delay: float = 1.0,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.top_n = max(1, min(int(top_n), 10))
        self.cache_ttl_seconds = cache_ttl_seconds
        self.verify_shopify = verify_shopify
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.max_retries = max(0, int(max_retries))  # Max retries for 429 errors
        self.initial_retry_delay = max(0.1, float(initial_retry_delay))  # Start delay in seconds

        # Note: the SDK primarily uses its own HTTP layer; we keep `timeout`
        # for consistency and potential future wiring via HttpOptions.
        self._client = genai.Client(api_key=self.api_key)

    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        item = self._cache.get(cache_key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            self._cache.pop(cache_key, None)
            return None
        return item["value"]

    def _set_cached(self, cache_key: str, value: Dict[str, Any]) -> None:
        self._cache[cache_key] = {"ts": time.time(), "value": value}

    @staticmethod
    def _normalize_url(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        # trim common trailing punctuation
        raw = raw.strip().strip(").,;\"'`")
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        # handle bare domains like example.com
        if re.match(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", raw, re.IGNORECASE):
            return f"https://{raw}"
        return raw

    @staticmethod
    def _is_ignored_url(url: str) -> bool:
        """Filter out tool/grounding redirect URLs and other non-target links."""
        if not url:
            return True
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if not host:
            return True
        if host.endswith("vertexaisearch.cloud.google.com"):
            return True
        if host.endswith("webcache.googleusercontent.com"):
            return True
        if host.endswith("google.com") and "url" in url:
            return True
        if "grounding-api-redirect" in url:
            return True
        # Common non-storefront destinations
        blocked_hosts = (
            "facebook.com",
            "instagram.com",
            "tiktok.com",
            "twitter.com",
            "x.com",
            "linkedin.com",
            "youtube.com",
            "pinterest.com",
            "wikipedia.org",
            "amazon.",
            "shopee.",
            "lazada.",
            "aliexpress.",
        )
        if any(part in host for part in blocked_hosts):
            return True
        return False

    def _looks_like_shopify_store(self, url: str) -> bool:
        """Best-effort Shopify fingerprinting to prevent wrong autosaves."""
        if not url:
            return False
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if host.endswith("myshopify.com"):
            return True

        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=min(max(self.timeout, 5), 15),
                allow_redirects=True,
            )
            text = (resp.text or "")[:200000].lower()
            # Common Shopify fingerprints
            if "cdn.shopify.com" in text:
                return True
            if 'meta name="generator" content="shopify"' in text:
                return True
            if "shopify.theme" in text or "shopify" in resp.headers.get("server", "").lower():
                return True
            # Shopify storefront often includes these assets/paths
            if "/cdn/shop/" in text or "/cart" in text and "shopify" in text:
                return True
        except Exception:
            return False
        return False

    @classmethod
    def _extract_urls_from_text(cls, text: str, limit: int = 5) -> List[str]:
        if not text:
            return []
        seen = set()
        out: List[str] = []

        # 1) Explicit URLs
        for m in re.finditer(r"https?://[^\s<>()\"']+", text, flags=re.IGNORECASE):
            u = cls._normalize_url(m.group(0))
            if u and not cls._is_ignored_url(u) and u not in seen:
                seen.add(u)
                out.append(u)
                if len(out) >= limit:
                    return out

        # 2) Bare domains
        for m in re.finditer(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", text, flags=re.IGNORECASE):
            u = cls._normalize_url(m.group(0))
            if u and not cls._is_ignored_url(u) and u not in seen:
                seen.add(u)
                out.append(u)
                if len(out) >= limit:
                    return out

        return out

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        """Try to parse JSON from model output."""
        if not text:
            return None
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        # Strip code fences
        fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
        if fenced != text:
            try:
                return json.loads(fenced)
            except Exception:
                pass

        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            return None

    @staticmethod
    def _normalize_candidates(candidates: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(candidates, list):
            return out
        seen = set()
        for c in candidates:
            if not isinstance(c, dict):
                continue
            url = (c.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "title": (c.get("title") or "").strip(),
                    "snippet": (c.get("snippet") or "").strip(),
                    "confidence": c.get("confidence"),
                }
            )
        return out

    def find_store_url(self, store_name: str, country: str = "", review_text: str = "") -> Dict[str, Any]:
        """
        Returns:
        {
          "query": str,
          "selected_url": str|None,
          "confidence": float|None,
          "reasoning": str,
          "results": [ {url,title,snippet,confidence}, ... ],
          "duration_ms": int
        }
        """
        if not self.api_key:
            raise ValueError("Gemini API key not configured")

        store_name = (store_name or "").strip()
        country = (country or "").strip()
        review_text = (review_text or "").strip()
        if not store_name:
            raise ValueError("store_name is required")

        query = f"{store_name} {country}".strip()
        normalized_query = " ".join(query.split()).lower()
        cache_key = f"{normalized_query}:{self.model}:{self.top_n}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Controlled JSON generation is not supported with the Search tool, so we ask
        # for a strict, line-based format that is easy to parse, and keep robust fallbacks.
        prompt = (
            "Find the official Shopify storefront homepage URL for the given store.\n"
            "Use Google Search to verify.\n"
            "Prefer the main homepage, not social profiles, directories, marketplaces, app stores, or review pages.\n\n"
            "Return EXACTLY this format (no extra lines):\n"
            "SELECTED_URL: <url_or_domain>\n"
            "CONFIDENCE: <number_0_to_1>\n"
            "REASONING: <one_sentence>\n"
            "CANDIDATES:\n"
            "- <url_or_domain>\n"
            "- <url_or_domain>\n\n"
            f"store_name: {store_name}\n"
            f"country: {country or 'unknown'}\n"
            f"{('review_context: ' + review_text[:400]) if review_text else ''}\n"
            f"Include up to {self.top_n} candidates.\n"
        )

        tools = [{"google_search": {}}]

        start = time.time()
        
        # Retry logic with exponential backoff for 429 errors
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=tools,
                        temperature=0.2,
                        max_output_tokens=900,
                    ),
                )
                break  # Success, exit retry loop
            except errors.ClientError as e:
                last_error = e
                # Check for 429 errors - try multiple ways to detect it
                error_code = getattr(e, 'code', None)
                error_message = str(e).upper()
                is_429 = (
                    error_code == 429 or 
                    '429' in str(e) or 
                    'RESOURCE_EXHAUSTED' in error_message or
                    'RATE_LIMIT' in error_message
                )
                
                # Only retry on 429 (RESOURCE_EXHAUSTED) errors
                if is_429 and attempt < self.max_retries:
                    delay = self.initial_retry_delay * (2 ** attempt)
                    # Cap delay at 30 seconds (truncated exponential backoff)
                    delay = min(delay, 30.0)
                    logger.warning(
                        f"Gemini 429 error (attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {delay:.1f}s... Error: {str(e)[:200]}"
                    )
                    time.sleep(delay)
                    continue
                # Not a 429 or max retries reached, re-raise
                raise
            except Exception as e:
                last_error = e
                error_str = str(e).upper()
                # Check if it's a 429 error wrapped in a different exception type
                is_429 = (
                    '429' in error_str or 
                    'RESOURCE_EXHAUSTED' in error_str or
                    'RATE_LIMIT' in error_str
                )
                if is_429 and attempt < self.max_retries:
                    delay = self.initial_retry_delay * (2 ** attempt)
                    delay = min(delay, 30.0)
                    logger.warning(
                        f"Gemini 429 error detected in exception (attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {delay:.1f}s... Error: {str(e)[:200]}"
                    )
                    time.sleep(delay)
                    continue
                # Non-429 errors or max retries: don't retry, re-raise immediately
                raise
        
        duration_ms = int((time.time() - start) * 1000)
        
        # If we exhausted retries, raise a clear error message
        if last_error:
            error_str = str(last_error)
            is_429 = (
                '429' in error_str or 
                'RESOURCE_EXHAUSTED' in error_str.upper() or
                'RATE_LIMIT' in error_str.upper()
            )
            if is_429:
                error_msg = getattr(last_error, 'message', None) or error_str
                raise ValueError(
                    f"Gemini API rate limit exceeded after {self.max_retries + 1} attempts. "
                    f"Please try again later or consider using a different provider. "
                    f"Error: {error_msg[:300]}"
                )

        text = getattr(response, "text", "") or ""

        # Attempt 1: JSON (best-effort)
        parsed = self._extract_json_object(text) or {}
        candidates = self._normalize_candidates(parsed.get("candidates"))
        selected_url = self._normalize_url((parsed.get("selected_url") or "").strip())
        confidence = parsed.get("confidence")
        reasoning = (parsed.get("reasoning") or "").strip()

        # Attempt 2: line-based format
        if not selected_url:
            m = re.search(r"^\s*SELECTED_URL:\s*(.+)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                selected_url = self._normalize_url(m.group(1))
        if confidence is None:
            m = re.search(r"^\s*CONFIDENCE:\s*([01](?:\.\d+)?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                try:
                    confidence = float(m.group(1))
                except Exception:
                    confidence = None
        if not reasoning:
            m = re.search(r"^\s*REASONING:\s*(.+)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                reasoning = m.group(1).strip()

        if not candidates:
            # Parse "- <candidate>" lines after CANDIDATES:
            block_match = re.search(r"CANDIDATES:\s*([\s\S]+)$", text, flags=re.IGNORECASE)
            block = block_match.group(1) if block_match else text
            urls = []
            for line in block.splitlines():
                line = line.strip()
                if not line.startswith("-"):
                    continue
                u = self._normalize_url(line.lstrip("-").strip())
                if u:
                    urls.append(u)
                if len(urls) >= self.top_n:
                    break
            if not urls:
                urls = self._extract_urls_from_text(text, limit=self.top_n)
            candidates = [{"url": u, "title": "", "snippet": "", "confidence": None} for u in urls]

        # Final cleanup: ignore redirect-like outputs
        if selected_url and self._is_ignored_url(selected_url):
            selected_url = ""
        if candidates:
            candidates = [c for c in candidates if not self._is_ignored_url(c.get("url", ""))]

        if not selected_url and candidates:
            selected_url = candidates[0]["url"]

        # Safety: only allow high-confidence autosave when storefront looks like Shopify.
        if self.verify_shopify and selected_url and isinstance(confidence, (int, float)):
            if not self._looks_like_shopify_store(selected_url):
                # Force manual selection by dropping confidence below typical threshold.
                confidence = min(float(confidence), 0.69)
                if reasoning:
                    reasoning += " (Shopify verification not detected; manual confirmation recommended.)"
                else:
                    reasoning = "Shopify verification not detected; manual confirmation recommended."

        result = {
            "query": query,
            "selected_url": selected_url or None,
            "confidence": confidence,
            "reasoning": reasoning,
            "results": candidates,
            "duration_ms": duration_ms,
        }

        logger.info(
            "Gemini query='%s' results=%s duration_ms=%s confidence=%s",
            query,
            len(candidates),
            duration_ms,
            confidence,
        )

        self._set_cached(cache_key, result)
        return result

