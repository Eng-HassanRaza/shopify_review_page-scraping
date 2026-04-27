"""Single source of truth for email validation and normalisation."""
import re
from typing import List

_EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*')

_SKIP = (
    "noreply@", "no-reply@", "donotreply@",
    "@example.com", "@test.com", "@localhost",
    "@127.", "@192.", "@10.",
    ".png@", ".jpg@", ".gif@", ".css@", ".js@",
)

# HTML / JS entity prefixes that appear when the email scraper picks up
# text from JSON blobs or mis-encoded HTML (e.g. \u003e → "u003e" literal).
# These are NOT valid local-part prefixes.
_ENTITY_PREFIXES = (
    "u003e", "u003c", "u003e", "u0026", "u002f",
    "u0022", "u0027", "amp;", "gt;", "lt;",
    "&gt;", "&lt;", "&amp;",
)


def is_valid_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    if not _EMAIL_RE.fullmatch(email):
        return False

    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64:
        return False
    if not domain or len(domain) > 255:
        return False

    parts = domain.split(".")
    if len(parts) < 2:
        return False

    tld = parts[-1].lower()
    if not tld.isalpha() or len(tld) < 2 or len(tld) > 6:
        return False

    # Reject version-number domains (e.g. @2.3.44)
    if any(p.isdigit() for p in parts):
        return False

    email_lower = email.lower()
    if any(s in email_lower for s in _SKIP):
        return False

    # Reject emails whose local part starts with an HTML / JS entity artifact
    # (e.g. "u003esupport@..." scraped from a JSON blob containing \u003e)
    local_lower = local.lower()
    if any(local_lower.startswith(ent) for ent in _ENTITY_PREFIXES):
        return False

    return True


def normalize_emails(emails: List[str]) -> List[str]:
    """Deduplicate and lowercase; drop invalids."""
    seen, out = set(), []
    for e in emails:
        e = e.strip().lower()
        if e and e not in seen and is_valid_email(e):
            seen.add(e)
            out.append(e)
    return out
