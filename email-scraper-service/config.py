"""Configuration settings for Email Scraper Service"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Database - Shared PostgreSQL
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'shopify_processor')
DB_USER = os.getenv('DB_USER', 'hassanraza')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

# Database connection string (for psycopg2)
DATABASE_URL = os.getenv('DATABASE_URL', f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# Server settings
HOST = os.getenv('EMAIL_SERVICE_HOST', '0.0.0.0')  # Listen on all interfaces for cloud
# Default port 5002 for local testing, use 5000 for cloud/production (set via EMAIL_SERVICE_PORT env var)
PORT = int(os.getenv('EMAIL_SERVICE_PORT', '5002'))
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# Email scraper settings
EMAIL_SCRAPER_MAX_PAGES = int(os.getenv('EMAIL_SCRAPER_MAX_PAGES', '50'))
EMAIL_SCRAPER_DELAY = float(os.getenv('EMAIL_SCRAPER_DELAY', '2.0'))
EMAIL_SCRAPER_TIMEOUT = int(os.getenv('EMAIL_SCRAPER_TIMEOUT', '30'))
EMAIL_SCRAPER_MAX_RETRIES = int(os.getenv('EMAIL_SCRAPER_MAX_RETRIES', '3'))
EMAIL_SCRAPER_SITEMAP_LIMIT = int(os.getenv('EMAIL_SCRAPER_SITEMAP_LIMIT', '100'))

# Email processing settings
EMAIL_USE_AI_VALIDATION = os.getenv('EMAIL_USE_AI_VALIDATION', 'false').lower() == 'true'
EMAIL_AI_MIN_CONFIDENCE = float(os.getenv('EMAIL_AI_MIN_CONFIDENCE', '0.7'))

# Concurrency settings
MAX_CONCURRENT_EMAIL_SCRAPING = int(os.getenv('MAX_CONCURRENT_EMAIL_SCRAPING', '10'))

# User agent for browser
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
