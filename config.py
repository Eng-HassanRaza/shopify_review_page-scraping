"""Configuration — single source of truth for all env vars."""
import os
from pathlib import Path
from dotenv import load_dotenv

_here = Path(__file__).resolve().parent
for _p in [_here / ".env", _here.parent / ".env"]:
    if _p.exists():
        load_dotenv(dotenv_path=_p, override=True)
        break
else:
    load_dotenv(override=True)

# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER','hassanraza')}:{os.getenv('DB_PASSWORD','')}@"
    f"{os.getenv('DB_HOST','localhost')}:{os.getenv('DB_PORT','5432')}/"
    f"{os.getenv('DB_NAME','shopify_leads')}"
)

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5001"))
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# Gemini
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MAX_RETRIES   = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
GEMINI_RETRY_DELAY   = float(os.getenv("GEMINI_RETRY_DELAY", "15.0"))
GEMINI_TIMEOUT   = int(os.getenv("GEMINI_TIMEOUT", "30"))
URL_CONFIDENCE_THRESHOLD = float(os.getenv("URL_CONFIDENCE_THRESHOLD", "0.5"))

# Pipeline pacing
INTER_STORE_DELAY = float(os.getenv("INTER_STORE_DELAY", "3.0"))
STORE_MAX_ATTEMPTS = int(os.getenv("STORE_MAX_ATTEMPTS", "3"))

# Serper.dev (geo-accurate Google Search)
# Comma-separated list of API keys — rotated automatically when one is exhausted
SERPER_API_KEYS: list = [
    k.strip() for k in os.getenv("SERPER_API_KEYS", "").split(",") if k.strip()
]
SERPER_BASE_URL = "https://google.serper.dev/search"
SERPER_RESULTS  = int(os.getenv("SERPER_RESULTS", "10"))   # results per query

# OpenAI (email filtering)
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Email scraper
EMAIL_MAX_PAGES    = int(os.getenv("EMAIL_MAX_PAGES", "40"))
EMAIL_DELAY        = float(os.getenv("EMAIL_DELAY", "0.5"))
EMAIL_TIMEOUT      = int(os.getenv("EMAIL_TIMEOUT", "20"))
EMAIL_SITEMAP_LIMIT = int(os.getenv("EMAIL_SITEMAP_LIMIT", "80"))
