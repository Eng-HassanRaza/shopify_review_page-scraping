# Email Scraper Service

Cloud service for scraping emails from Shopify store URLs.

## Overview

This service handles:
- Email scraping from store URLs (using aiohttp)
- AI-powered email extraction and validation
- Continuous queue processing (always maintains 10 active jobs)
- Background worker for automatic job processing

## Requirements

- Python 3.11+
- PostgreSQL database (shared with URL Finder Service)
- OpenAI API key (for AI email extraction)

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables in `.env`:
```env
DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=shopify_processor
DB_USER=your_user
DB_PASSWORD=your_password
OPENAI_API_KEY=your_openai_key

# Service configuration
EMAIL_SERVICE_HOST=0.0.0.0  # Listen on all interfaces
EMAIL_SERVICE_PORT=5000  # Use 5000 for cloud/production, 5002 for local testing
DEBUG=false

# Email scraper settings (optional, defaults shown)
EMAIL_SCRAPER_MAX_PAGES=50
EMAIL_SCRAPER_DELAY=2.0
EMAIL_SCRAPER_TIMEOUT=30
EMAIL_SCRAPER_MAX_RETRIES=3
EMAIL_SCRAPER_SITEMAP_LIMIT=100
MAX_CONCURRENT_EMAIL_SCRAPING=10
```

## Running

### Development (Local Testing)
```bash
python app.py
```
Service will run on `http://localhost:5002` by default (to avoid conflict with macOS services on port 5000).

### Production (using systemd)

Create `/etc/systemd/system/email-scraper.service`:
```ini
[Unit]
Description=Email Scraper Service
After=network.target postgresql.service

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/email-scraper-service
Environment="DATABASE_URL=postgresql://user:pass@host:5432/dbname"
Environment="OPENAI_API_KEY=your_key"
Environment="EMAIL_SERVICE_PORT=5000"
ExecStart=/path/to/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable email-scraper
sudo systemctl start email-scraper
sudo systemctl status email-scraper
```

## API Endpoints

### Health Check
- `GET /` - Service health and status

### Email Scraping
- `POST /api/email-scraping/start-next-job` - Start next pending job
- `POST /api/email-scraping/batch/start` - Start batch processing
- `GET /api/email-scraping/batch/status` - Get scraping status

### Store Info
- `GET /api/stores/<id>` - Get store details

## Architecture

### Continuous Queue Processing

The service automatically processes stores in a continuous queue:
1. Background worker polls database every 5 seconds
2. Starts new jobs when capacity is available (max 10 concurrent)
3. When a job completes, automatically starts the next one
4. Maintains 10 active jobs at all times (if stores are available)

### Concurrency Model

- **Store Level**: Parallel (10 stores processed simultaneously)
- **Page Level**: Sequential (within each store, pages scraped one-by-one)
- Uses ThreadPoolExecutor for store-level parallelism
- Uses asyncio + aiohttp for efficient I/O within each store

### Rate Limiting

- Adaptive delay (starts at 2s, increases on 429 errors)
- Circuit breaker (stops after 5 consecutive 429s)
- Respects `Retry-After` headers
- Max delay: 60 seconds

## Database

Shares PostgreSQL database with URL Finder Service. Processes stores with:
- `status = 'url_verified'` (has URL, needs emails)
- Updates to `status = 'emails_found'` or `'no_emails_found'` when complete

## Monitoring

Check service status (local):
```bash
curl http://localhost:5002/
```

Check scraping status (local):
```bash
curl http://localhost:5002/api/email-scraping/batch/status
```

For cloud deployment (port 5000):
```bash
curl http://your-server:5000/
```

## Deployment

### Cloud Deployment (AWS, GCP, DigitalOcean)

1. Set up PostgreSQL database (can be on same server or managed service)
2. Configure environment variables
3. Install dependencies in virtual environment
4. Set up systemd service (see above)
5. Set `EMAIL_SERVICE_PORT=5000` in environment variables or `.env` file
6. Configure firewall to allow port 5000 (if needed)
7. Optionally set up reverse proxy (nginx) for HTTPS

### Docker (Optional)

Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "app.py"]
```

Build and run:
```bash
docker build -t email-scraper .
docker run -d --name email-scraper \
  -e DATABASE_URL=postgresql://... \
  -e OPENAI_API_KEY=... \
  -e EMAIL_SERVICE_PORT=5000 \
  -p 5000:5000 \
  email-scraper
```
