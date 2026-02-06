"""Configuration settings for URL Finder Service"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env files.
# Important: load BOTH root and service .env files if present.
# - Root .env provides shared config (e.g., PERPLEXITY_API_KEY).
# - Service .env can override root values for local dev.
service_dir = Path(__file__).resolve().parent  # url-finder-service/
project_root = service_dir.parent              # shopify_review_page-scraping/

env_paths_in_order = [
    project_root / ".env",        # root .env
    service_dir / ".env",         # service-specific .env (override root)
    project_root.parent / ".env", # parent of project root (optional compatibility)
]

any_env_loaded = False
for env_path in env_paths_in_order:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        any_env_loaded = True

# Also try loading from current directory (for compatibility)
if not any_env_loaded:
    load_dotenv(override=True)

# Database - Shared PostgreSQL
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'shopify_processor')
DB_USER = os.getenv('DB_USER', 'hassanraza')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

# Database connection string (for psycopg2)
DATABASE_URL = os.getenv('DATABASE_URL', f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# Server settings
HOST = "127.0.0.1"
PORT = 5001  # Local service port
DEBUG = True

# Browser automation settings
BROWSER_HEADLESS = False  # Visible browser for manual search
BROWSER_SLOW_MO = 500  # Delay between actions (ms)
BROWSER_TIMEOUT = 30000  # Page load timeout (ms)

# User agent for browser
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# URL finder provider: "gemini" (default), "perplexity", or "extension"
URL_FINDER_PROVIDER = os.getenv('URL_FINDER_PROVIDER', 'gemini').lower()

# Perplexity settings (optional URL finding provider)
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY', '')
PERPLEXITY_MODEL = os.getenv('PERPLEXITY_MODEL', 'sonar-pro')
PERPLEXITY_TIMEOUT = int(os.getenv('PERPLEXITY_TIMEOUT', '20'))
PERPLEXITY_TOP_N = int(os.getenv('PERPLEXITY_TOP_N', '5'))
PERPLEXITY_AUTOSAVE_THRESHOLD = float(os.getenv('PERPLEXITY_AUTOSAVE_THRESHOLD', '0.7'))
PERPLEXITY_CACHE_TTL_SECONDS = int(os.getenv('PERPLEXITY_CACHE_TTL_SECONDS', '86400'))

# Gemini settings (optional URL finding provider)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '') or os.getenv('GOOGLE_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
GEMINI_TIMEOUT = int(os.getenv('GEMINI_TIMEOUT', '20'))
GEMINI_TOP_N = int(os.getenv('GEMINI_TOP_N', '5'))
GEMINI_AUTOSAVE_THRESHOLD = float(os.getenv('GEMINI_AUTOSAVE_THRESHOLD', '0.7'))
GEMINI_CACHE_TTL_SECONDS = int(os.getenv('GEMINI_CACHE_TTL_SECONDS', '86400'))
GEMINI_VERIFY_SHOPIFY = os.getenv('GEMINI_VERIFY_SHOPIFY', 'true').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
GEMINI_MAX_RETRIES = int(os.getenv('GEMINI_MAX_RETRIES', '3'))
GEMINI_RETRY_DELAY = float(os.getenv('GEMINI_RETRY_DELAY', '1.0'))

# Background worker settings
WORKER_ENABLED = os.getenv('WORKER_ENABLED', 'false').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
WORKER_SLEEP_SECONDS = int(os.getenv('WORKER_SLEEP_SECONDS', '5'))
WORKER_BATCH_SIZE = int(os.getenv('WORKER_BATCH_SIZE', '10'))
WORKER_MAX_RETRIES = int(os.getenv('WORKER_MAX_RETRIES', '3'))
PROVIDER_PRIORITY = [p.strip() for p in os.getenv('PROVIDER_PRIORITY', 'gemini,perplexity').split(',') if p.strip()]
AUTO_SAVE_THRESHOLD = float(os.getenv('AUTO_SAVE_THRESHOLD', '0.7'))
LOW_CONFIDENCE_THRESHOLD = float(os.getenv('LOW_CONFIDENCE_THRESHOLD', '0.5'))

# URL validation settings
URL_VALIDATION_ENABLED = os.getenv('URL_VALIDATION_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
URL_VALIDATION_TIMEOUT = int(os.getenv('URL_VALIDATION_TIMEOUT', '10'))
URL_VALIDATION_FOLLOW_REDIRECTS = os.getenv('URL_VALIDATION_FOLLOW_REDIRECTS', 'true').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
