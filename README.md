# Shopify Review Processor

Microservices application for scraping Shopify app reviews, finding store URLs, and extracting emails.

## Architecture

- **URL Finder Service** (`url-finder-service/`) — Local. Review scraping, URL finding via Chrome extension, AI URL selection, frontend UI. Port 5001.
- **Email Scraper Service** (`email-scraper-service/`) — Cloud or local. Email scraping, AI extraction, queue processing. Port 5002 (local) / 5000 (cloud).
- **Chrome Extension** (`google_search_extension/`) — Load in Chrome for Google search automation.

Both services share a PostgreSQL database (e.g. AWS RDS).

## Quick Start

1. **Load Chrome extension:** `chrome://extensions/` → Load unpacked → select `google_search_extension/`

2. **Start Email Scraper Service:**
```bash
cd email-scraper-service
pip install -r requirements.txt
python app.py
```

3. **Start URL Finder Service:**
```bash
cd url-finder-service
pip install -r requirements.txt
playwright install chromium
python app.py
```

4. **Open UI:** `http://127.0.0.1:5001` — Set Email Scraper URL to `http://localhost:5002`

## Configuration

Create `.env` in project root or service directories. See `MICROSERVICES_SETUP.md` for full setup, deployment, and troubleshooting.
