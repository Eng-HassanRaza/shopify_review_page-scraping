# Microservices Architecture Setup

The project has been split into two separate microservices that share a PostgreSQL database.

## Architecture Overview

```
┌─────────────────────────────────┐
│   URL Finder Service (Local)     │
│   Port: 5001                     │
│   - Review Scraping              │
│   - URL Finding (Chrome)         │
│   - AI URL Selection             │
│   - Frontend UI                  │
└──────────────┬──────────────────┘
               │
               │ Shared PostgreSQL Database
               │
┌──────────────▼──────────────────┐
│  Email Scraper Service (Cloud)  │
│  Port: 5002 (local), 5000 (cloud)│
│  - Email Scraping               │
│  - AI Email Extraction          │
│  - Continuous Queue Processing  │
└─────────────────────────────────┘
```

## Service 1: URL Finder Service (Local)

**Location:** `url-finder-service/`

**Purpose:** Handles review scraping, URL finding via Chrome extension, and AI URL selection.

**Why Local:** Requires Chrome browser and Chrome extension for Google searches.

**Features:**
- Review scraping from Shopify App Store
- Chrome extension integration for Google searches
- AI-powered URL selection
- Frontend UI for managing the process
- Manual URL selection interface

**Running:**
```bash
cd url-finder-service
pip install -r requirements.txt
python app.py
```

Access at: `http://127.0.0.1:5001`

## Service 2: Email Scraper Service (Cloud)

**Location:** `email-scraper-service/`

**Purpose:** Handles email scraping from store URLs and AI email extraction.

**Why Cloud:** Can run 24/7 on a server, no browser needed.

**Features:**
- Email scraping using aiohttp (no browser)
- AI-powered email extraction
- Continuous queue processing (maintains 10 active jobs)
- Background worker for automatic processing
- Health check endpoint

**Running Locally (for testing):**
```bash
cd email-scraper-service
pip install -r requirements.txt
python app.py
```

Access at: `http://localhost:5002` (local) or `http://your-server:5000` (cloud)

**Deploying to Cloud:**
1. Set up PostgreSQL database (can be on same server or managed service)
2. Configure environment variables in `.env`
3. Deploy code to server
4. Set up systemd service (see `email-scraper-service/README.md`)
5. Configure firewall to allow port 5000

## Database Configuration

Both services connect to the same PostgreSQL database. Configure in `.env`:

```env
DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=shopify_processor
DB_USER=your_user
DB_PASSWORD=your_password
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

## Frontend Configuration

The frontend (in URL Finder Service) needs to know the Email Scraper Service URL:

1. **In the UI:** There's a configuration section at the top where you can set the Email Scraper Service URL
2. **Default:** `http://localhost:5000` (for local testing)
3. **Production:** Set to your cloud server URL (e.g., `http://your-server-ip:5000`)

The URL is saved in browser localStorage and persists across sessions.

## Workflow

1. **Start URL Finder Service** (local)
   - Scrape reviews from Shopify App Store
   - Find store URLs using Chrome extension
   - AI selects best URL
   - Saves URLs to database with `status = 'url_verified'`

2. **Start Email Scraper Service** (cloud)
   - Automatically polls database for stores with `status = 'url_verified'`
   - Scrapes emails from store URLs
   - Uses AI to extract relevant emails
   - Updates database with `status = 'emails_found'` or `'no_emails_found'`

3. **View Results**
   - Frontend in URL Finder Service shows all data
   - Can export to JSON/CSV

## Testing Locally

1. **Start PostgreSQL** (if not already running)

2. **Start Email Scraper Service:**
   ```bash
   cd email-scraper-service
   python app.py
   ```

3. **Start URL Finder Service:**
   ```bash
   cd url-finder-service
   python app.py
   ```

4. **Configure Frontend:**
   - Open `http://127.0.0.1:5001`
   - In the "Email Scraper Service URL" field, enter: `http://localhost:5002` (default for local)
   - Click "Save"
   - For cloud deployment, use: `http://your-server:5000`

5. **Test Workflow:**
   - Paste a Shopify App Review URL
   - Start scraping reviews
   - Find URLs for stores
   - Email scraping will automatically start when URLs are found

## Production Deployment

### URL Finder Service
- Run on your local machine (requires Chrome)
- No special deployment needed
- Just run `python app.py`

### Email Scraper Service
- Deploy to cloud server (AWS, GCP, DigitalOcean, etc.)
- Set up systemd service for auto-start
- Configure environment variables
- Set up monitoring/logging

See `email-scraper-service/README.md` for detailed deployment instructions.

## API Communication

The frontend (URL Finder Service) makes API calls to the Email Scraper Service:

- `GET /api/email-scraping/batch/status` - Get scraping status
- `POST /api/email-scraping/batch/start` - Start batch processing
- `POST /api/email-scraping/start-next-job` - Start next job

All other API calls go to the local URL Finder Service.

## Troubleshooting

### Email Scraper Service not responding
- Check if service is running: `curl http://localhost:5002/` (local) or `curl http://your-server:5000/` (cloud)
- Check firewall settings
- Verify database connection
- Check logs for errors
- Verify correct port is configured (5002 for local, 5000 for cloud)

### CORS errors
- Both services have CORS enabled
- If issues persist, check that Email Scraper Service URL is correct in frontend

### Database connection errors
- Verify PostgreSQL is running
- Check database credentials in `.env`
- Ensure both services use the same database URL

## File Structure

```
project/
├── url-finder-service/
│   ├── app.py              # Flask app (URL finding)
│   ├── config.py           # Configuration
│   ├── database.py         # Database module (shared)
│   ├── modules/            # URL finding modules
│   ├── templates/          # Frontend HTML
│   ├── static/             # Frontend JS/CSS
│   └── requirements.txt    # Dependencies
│
├── email-scraper-service/
│   ├── app.py              # Flask app (Email scraping)
│   ├── config.py           # Configuration
│   ├── database.py         # Database module (shared)
│   ├── modules/            # Email scraping modules
│   └── requirements.txt    # Dependencies
│
└── .env                    # Shared environment variables
```

## Next Steps

1. Test both services locally
2. Deploy Email Scraper Service to cloud
3. Configure frontend with cloud service URL
4. Monitor both services for errors
5. Set up logging and monitoring for production
