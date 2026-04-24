"""GPT-based email filter — removes irrelevant emails from scraped list."""
import json
import logging
from typing import List, Optional
from urllib.parse import urlparse

from openai import OpenAI

import config
from utils.email_utils import is_valid_email, normalize_emails

logger = logging.getLogger(__name__)


class AIEmailFilter:
    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=config.OPENAI_API_KEY)

    def filter(
        self,
        raw_emails: List[str],
        store_url: str = "",
        store_name: str = "",
    ) -> List[str]:
        """
        Return a subset of raw_emails that are business-relevant.
        Falls back to the normalised raw list if AI fails.
        """
        normalized = normalize_emails(raw_emails)
        if not normalized:
            return []

        domain = ""
        try:
            domain = urlparse(
                store_url if store_url.startswith(("http://", "https://")) else f"https://{store_url}"
            ).netloc.replace("www.", "")
        except Exception:
            pass

        context = f"Store URL: {store_url}" + (f"\nDomain: {domain}" if domain else "") + (f"\nStore Name: {store_name}" if store_name else "")
        email_list = "\n".join(f"- {e}" for e in normalized)

        prompt = (
            "You are filtering a list of scraped emails. "
            "Return ONLY emails that appear in the list below. Do NOT add or invent any.\n\n"
            f"{context}\n\n"
            f"Emails to filter ({len(normalized)} total):\n{email_list}\n\n"
            "Include: valid business emails (domain emails, support, contact, info, etc.)\n"
            "Exclude: noreply, test/demo, obvious spam, malformed.\n\n"
            'Return JSON: {"relevant_emails": ["email1@...", ...]}'
        )

        try:
            resp = self._client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Filter emails from the provided list only. Return valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            returned = data.get("relevant_emails", [])

            # Strict: only accept emails that were in the original list
            original_set = {e.lower() for e in normalized}
            valid = [e.lower() for e in returned if e.lower() in original_set and is_valid_email(e)]
            logger.info(
                "AIEmailFilter: %d raw → %d normalized → %d returned by AI → %d accepted",
                len(raw_emails), len(normalized), len(returned), len(valid),
            )
            return valid
        except Exception as e:
            logger.warning("AIEmailFilter failed (%s), returning normalized emails", e)
            return normalized
