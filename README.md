# Shopify Lead Gen

Scrape Shopify App Store reviews → find store websites → extract contact emails → export CSV.

## How it works

1. Paste a Shopify App Store review page URL (e.g. `https://apps.shopify.com/klaviyo/reviews`)
2. Set an optional limit (e.g. 100 stores per run)
3. The pipeline runs in the background:
   - Scrapes reviewer store names from the review pages
   - Finds each store's website via Gemini + Google Search
   - Scrapes contact emails from the store website (falls back to Gemini search if none found)
4. Export results as CSV

Re-submitting the same URL resumes from where you left off — no duplicate processing.

---

## Requirements

- Python 3.11+
- PostgreSQL
- Google Gemini API key (required)
- OpenAI API key (optional — enables AI email filtering)

---

## Local setup

```bash
git clone https://github.com/Eng-HassanRaza/shopify_review_page-scraping.git
cd shopify_review_page-scraping

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys and DB credentials

python app.py
# Open http://localhost:5001
```

---

## EC2 deployment

```bash
# 1. Clone & install
git clone https://github.com/Eng-HassanRaza/shopify_review_page-scraping.git
cd shopify_review_page-scraping
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
nano .env   # set GEMINI_API_KEY, DATABASE_URL, HOST=0.0.0.0, DEBUG=false

# 3. Run with gunicorn (production)
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5001 app:app

# Or run as a systemd service (see below)
```

### systemd service

Create `/etc/systemd/system/shopify-lead-gen.service`:

```ini
[Unit]
Description=Shopify Lead Gen
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/shopify_review_page-scraping
EnvironmentFile=/home/ec2-user/shopify_review_page-scraping/.env
ExecStart=/home/ec2-user/shopify_review_page-scraping/venv/bin/gunicorn -w 2 -b 0.0.0.0:5001 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable shopify-lead-gen
sudo systemctl start shopify-lead-gen
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key |
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `OPENAI_API_KEY` | ❌ | — | Enables AI email filtering |
| `HOST` | ❌ | `127.0.0.1` | Set to `0.0.0.0` on EC2 |
| `PORT` | ❌ | `5001` | Server port |
| `DEBUG` | ❌ | `true` | Set to `false` in production |
| `URL_CONFIDENCE_THRESHOLD` | ❌ | `0.5` | Min Gemini confidence to accept a URL |
| `INTER_STORE_DELAY` | ❌ | `3.0` | Seconds between Gemini calls (rate limiting) |
| `STORE_MAX_ATTEMPTS` | ❌ | `3` | Retry attempts per store before marking failed |
