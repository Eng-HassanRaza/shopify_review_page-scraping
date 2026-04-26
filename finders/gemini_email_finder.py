"""Find contact emails for a store using Gemini + Google Search as a fallback."""
import logging
import re
import time
from typing import List

from google import genai
from google.genai import types, errors

import config
from utils.email_utils import is_valid_email, normalize_emails

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*')

_SKIP_DOMAINS = (
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "example.com", "test.com", "email.com",
    "sentry.io", "wixpress.com", "shopify.com",
)


def _is_business_email(email: str, store_url: str = "") -> bool:
    """Return True if the email looks like a business contact (not generic webmail)."""
    domain = email.split("@")[-1].lower()
    if any(domain == skip for skip in _SKIP_DOMAINS):
        return False
    # Prefer email matches store domain
    return True


class GeminiEmailFinder:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    def find(self, store_name: str, store_url: str = "") -> List[str]:
        """
        Search for contact/support emails using Gemini + Google Search.
        Returns a list of valid business email addresses (may be empty).
        """
        store_name = (store_name or "").strip()
        if not store_name:
            return []

        prompt = (
            "You are a research assistant finding contact emails for an e-commerce store.\n\n"
            f"Store Name: {store_name}\n"
            + (f"Store URL: {store_url}\n" if store_url else "")
            + "\nUse Google Search to find their official contact / support / owner email address.\n"
            "Look for:\n"
            "- Contact pages on their website\n"
            "- Support email addresses\n"
            "- 'mailto:' links on their site\n"
            "- Business registration / WHOIS data\n\n"
            "Return ONLY emails you found via search. Do NOT guess or make up emails.\n"
            "Format (one email per line, no markdown):\n"
            "EMAIL: contact@store.com\n"
            "EMAIL: support@store.com\n"
            "(or write NO_EMAIL_FOUND if nothing was found)\n"
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
                        max_output_tokens=400,
                    ),
                )
                break
            except errors.ClientError as e:
                last_err = e
                if self._is_rate_limit(e) and attempt < config.GEMINI_MAX_RETRIES:
                    self._backoff(attempt)
                    continue
                logger.warning("GeminiEmailFinder ClientError: %s", e)
                return []
            except Exception as e:
                last_err = e
                if self._is_rate_limit(e) and attempt < config.GEMINI_MAX_RETRIES:
                    self._backoff(attempt)
                    continue
                logger.warning("GeminiEmailFinder error: %s", e)
                return []
        else:
            logger.warning("GeminiEmailFinder rate limit exceeded after retries")
            return []

        text = getattr(response, "text", "") or ""
        if "NO_EMAIL_FOUND" in text.upper():
            return []

        # Extract explicit EMAIL: lines first
        emails: List[str] = []
        for line in text.splitlines():
            m = re.match(r"^\s*EMAIL:\s*(.+)\s*$", line, re.IGNORECASE)
            if m:
                addr = m.group(1).strip().lower()
                if is_valid_email(addr) and _is_business_email(addr, store_url):
                    emails.append(addr)

        # Fallback: regex scan the full response
        if not emails:
            for m in _EMAIL_RE.finditer(text):
                addr = m.group(0).lower()
                if is_valid_email(addr) and _is_business_email(addr, store_url):
                    emails.append(addr)

        result = normalize_emails(emails)
        logger.info("GeminiEmailFinder store=%r → %d email(s): %s", store_name, len(result), result)
        return result

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).upper()
        return "429" in s or "RESOURCE_EXHAUSTED" in s or "RATE_LIMIT" in s

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(config.GEMINI_RETRY_DELAY * (2 ** attempt), 60.0)
        logger.warning("GeminiEmailFinder 429 — retrying in %.1fs", delay)
        time.sleep(delay)
