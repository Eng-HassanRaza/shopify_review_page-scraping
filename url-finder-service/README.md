# URL Finder Service

Local service for finding Shopify store URLs using Chrome extension and AI selection.

## Overview

This service handles:
- Review scraping from Shopify App Store
- URL finding via Chrome extension (Google search)
- AI-powered URL selection
- Frontend UI for managing the process

## Requirements

- Python 3.11+
- PostgreSQL database (shared with Email Scraper Service)
- Chrome browser with the Google Search Extension installed
- OpenAI API key (for AI URL selection)

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Install Playwright browsers (if using Playwright):
```bash
playwright install chromium
```

3. Configure environment variables in `.env`:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=shopify_processor
DB_USER=your_user
DB_PASSWORD=your_password
OPENAI_API_KEY=your_openai_key
```

4. Install Chrome Extension:
   - Open Chrome and go to `chrome://extensions/`
   - Enable "Developer mode"
   - Click "Load unpacked"
   - Select the `google_search_extension` folder from the parent directory

## Running

```bash
python app.py
```

The service will start on `http://127.0.0.1:5001`

## API Endpoints

### Review Scraping
- `POST /api/jobs` - Create or resume scraping job
- `GET /api/jobs` - Get all jobs
- `GET /api/jobs/<id>` - Get job status

### URL Finding
- `GET /api/stores/next` - Get next pending store
- `PUT /api/stores/<id>/url` - Save found URL
- `GET /api/stores/url-finding-status` - Get URL finding progress
- `POST /api/stores/<id>/skip` - Skip a store

### Chrome Extension
- `POST /api/search/request` - Request Google search
- `GET /api/search/poll/<search_id>` - Poll for search results
- `POST /api/search/extension/submit` - Extension submits results
- `GET /api/search/extension/pending` - Extension polls for pending searches

### AI URL Selection
- `POST /api/ai/select-url` - AI selects best URL from results

## Architecture

This service runs locally because it requires:
- Chrome browser for Google searches
- Chrome extension integration
- User interaction for URL selection

The Email Scraper Service runs separately (on cloud) and processes stores after URLs are found.

## Database

Shares PostgreSQL database with Email Scraper Service. Stores table status flow:
- `pending_url` → URL finding in progress
- `url_verified` → URL found, ready for email scraping
- `emails_found` → Email scraping completed (by Email Scraper Service)
