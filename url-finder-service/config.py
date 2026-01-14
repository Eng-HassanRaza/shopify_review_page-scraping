"""Configuration settings for URL Finder Service"""
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
HOST = "127.0.0.1"
PORT = 5001  # Local service port
DEBUG = True

# Browser automation settings
BROWSER_HEADLESS = False  # Visible browser for manual search
BROWSER_SLOW_MO = 500  # Delay between actions (ms)
BROWSER_TIMEOUT = 30000  # Page load timeout (ms)

# User agent for browser
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
