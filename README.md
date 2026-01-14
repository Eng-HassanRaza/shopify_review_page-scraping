# Shopify Review Processor

Unified web application for scraping Shopify app reviews, finding store URLs, and extracting emails.

## Quick Start

1. **Install dependencies:**
```bash
pip install -r shopify_processor/requirements.txt
playwright install chromium
```

2. **Start the application:**
```bash
cd shopify_processor
python app.py
```

3. **Open your browser:**
Navigate to `http://127.0.0.1:5000`

## Features

- **Review Scraping**: Automatically scrape reviews from Shopify App Store pages
- **URL Finding**: Search Google and verify store URLs using browser automation
- **Email Extraction**: Scrape emails from verified store websites
- **Progress Tracking**: SQLite database tracks all progress
- **Web Interface**: Clean, modern web UI for managing the workflow
- **Export**: Export data as JSON or CSV

## Workflow

1. Enter a Shopify App Review URL (e.g., `https://apps.shopify.com/app-name/reviews`)
2. Click "Start Scraping" - reviews are automatically scraped
3. For each store, click "Find URL" to search Google
4. Select the correct store URL from search results
5. Emails are automatically scraped after URL verification
6. Export your data when complete

## Architecture

- **Backend**: Flask web server
- **Database**: SQLite for persistent storage
- **Browser Automation**: Playwright for Google search
- **Email Scraping**: Async aiohttp for efficient scraping

## Configuration

Edit `shopify_processor/config.py` to adjust server settings, browser behavior, and scraping parameters.

## Backup

The `find_store_url_v1/` directory contains the previous Chrome extension implementation as a backup.
