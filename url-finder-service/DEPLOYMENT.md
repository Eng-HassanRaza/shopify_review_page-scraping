# EC2 Deployment Guide for URL Finder Service

## Current Implementation vs Celery

### Current Implementation (Recommended for Single EC2 Instance)

**Pros:**
- ✅ **Simpler**: No need for Redis/RabbitMQ broker
- ✅ **Lightweight**: Direct database polling, fewer dependencies
- ✅ **Self-contained**: Everything runs in one process
- ✅ **Easy to debug**: Single process, straightforward logging
- ✅ **Lower resource usage**: No broker overhead
- ✅ **Works well**: Perfect for single-instance deployment

**Cons:**
- ❌ **Single instance**: Can't easily scale across multiple servers
- ❌ **No task scheduling**: Can't schedule periodic tasks easily
- ❌ **Less visibility**: No built-in monitoring dashboard

**Best for:** Single EC2 instance, simple deployment, moderate load

### Celery (Better for Scaling)

**Pros:**
- ✅ **Distributed**: Can run workers on multiple servers
- ✅ **Task scheduling**: Built-in periodic task support (Celery Beat)
- ✅ **Better monitoring**: Flower dashboard for task visibility
- ✅ **Task retries**: Built-in retry mechanisms
- ✅ **Scalability**: Add more workers as needed

**Cons:**
- ❌ **More complex**: Requires Redis/RabbitMQ broker
- ❌ **More dependencies**: Additional services to manage
- ❌ **Overhead**: Broker adds latency and resource usage
- ❌ **More moving parts**: More things that can fail

**Best for:** Multiple instances, high load, need task scheduling, need monitoring dashboard

## Recommendation

**For your use case (single EC2 instance):** Use the **current implementation**. It's simpler, sufficient, and follows the same pattern as your email scraper service.

**Consider Celery if:**
- You need to scale to multiple EC2 instances
- You need periodic task scheduling
- You need better monitoring/visibility
- You're processing very high volumes

## EC2 Deployment Steps

### 1. Prepare EC2 Instance

```bash
# Connect to your EC2 instance
ssh -i your-key.pem ubuntu@your-ec2-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install -y python3 python3-pip python3-venv postgresql-client

# Install Git (if not already installed)
sudo apt install -y git
```

### 2. Clone and Setup Repository

```bash
# Navigate to home directory
cd ~

# Clone your repository (or upload files)
git clone your-repo-url shopify_review_page-scraping
cd shopify_review_page-scraping

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r url-finder-service/requirements.txt
```

### 3. Configure Environment Variables

```bash
# Create .env file in project root
cd ~/shopify_review_page-scraping
nano .env
```

Add your configuration:

```env
# Database (RDS PostgreSQL)
DB_HOST=your-rds-endpoint.region.rds.amazonaws.com
DB_PORT=5432
DB_NAME=shopify_processor
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DATABASE_URL=postgresql://your_db_user:your_db_password@your-rds-endpoint.region.rds.amazonaws.com:5432/shopify_processor

# API Keys
GEMINI_API_KEY=your_gemini_key
PERPLEXITY_API_KEY=your_perplexity_key
GOOGLE_CSE_API_KEY=your_cse_key
GOOGLE_CSE_CX=your_cse_cx
OPENAI_API_KEY=your_openai_key

# Worker Configuration
WORKER_ENABLED=true
WORKER_SLEEP_SECONDS=5
WORKER_BATCH_SIZE=10
WORKER_MAX_RETRIES=3
PROVIDER_PRIORITY=gemini,perplexity,cse
AUTO_SAVE_THRESHOLD=0.7
LOW_CONFIDENCE_THRESHOLD=0.5

# Server Configuration
HOST=0.0.0.0
PORT=5001
DEBUG=false
```

### 4. Test Database Connection

```bash
# Activate venv
source venv/bin/activate

# Test database connection
cd url-finder-service
python -c "from database import Database; from config import DATABASE_URL; db = Database(DATABASE_URL); print('Database connection successful')"
```

### 5. Update Systemd Service File

```bash
# Edit the service file with your actual paths
nano url-finder-service/url-finder-worker.service
```

Update paths in the service file:

```ini
[Unit]
Description=URL Finder Background Worker
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/shopify_review_page-scraping/url-finder-service
Environment="PATH=/home/ubuntu/shopify_review_page-scraping/venv/bin"
Environment="PYTHONPATH=/home/ubuntu/shopify_review_page-scraping/url-finder-service"
ExecStart=/home/ubuntu/shopify_review_page-scraping/venv/bin/python /home/ubuntu/shopify_review_page-scraping/url-finder-service/worker.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Resource limits
LimitNOFILE=65536
MemoryLimit=2G

[Install]
WantedBy=multi-user.target
```

### 6. Install and Start Systemd Service

```bash
# Copy service file to systemd directory
sudo cp url-finder-service/url-finder-worker.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable service (start on boot)
sudo systemctl enable url-finder-worker

# Start service
sudo systemctl start url-finder-worker

# Check status
sudo systemctl status url-finder-worker
```

### 7. Monitor Logs

```bash
# View logs in real-time
sudo journalctl -u url-finder-worker -f

# View recent logs
sudo journalctl -u url-finder-worker -n 100

# View logs since today
sudo journalctl -u url-finder-worker --since today
```

### 8. (Optional) Run Flask App as Service

If you also want to run the Flask API server:

```bash
# Create service file for Flask app
sudo nano /etc/systemd/system/url-finder-api.service
```

```ini
[Unit]
Description=URL Finder API Service
After=network.target postgresql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/shopify_review_page-scraping/url-finder-service
Environment="PATH=/home/ubuntu/shopify_review_page-scraping/venv/bin"
Environment="PYTHONPATH=/home/ubuntu/shopify_review_page-scraping/url-finder-service"
ExecStart=/home/ubuntu/shopify_review_page-scraping/venv/bin/python /home/ubuntu/shopify_review_page-scraping/url-finder-service/app.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable url-finder-api
sudo systemctl start url-finder-api
sudo systemctl status url-finder-api
```

### 9. Configure Security Group

In AWS EC2 Console:
1. Go to Security Groups
2. Edit inbound rules
3. Add rule:
   - Type: Custom TCP
   - Port: 5001 (or your PORT)
   - Source: Your IP or 0.0.0.0/0 (less secure, use specific IP if possible)

### 10. Test Deployment

```bash
# Test worker status (if Flask API is running)
curl http://localhost:5001/api/worker/status

# Test health endpoint
curl http://localhost:5001/api/health

# Test from outside (replace with your EC2 IP)
curl http://your-ec2-ip:5001/api/health
```

## Service Management Commands

```bash
# Start worker
sudo systemctl start url-finder-worker

# Stop worker
sudo systemctl stop url-finder-worker

# Restart worker
sudo systemctl restart url-finder-worker

# Check status
sudo systemctl status url-finder-worker

# View logs
sudo journalctl -u url-finder-worker -f

# Disable auto-start on boot
sudo systemctl disable url-finder-worker

# Enable auto-start on boot
sudo systemctl enable url-finder-worker
```

## Troubleshooting

### Worker not starting

```bash
# Check service status
sudo systemctl status url-finder-worker

# Check logs for errors
sudo journalctl -u url-finder-worker -n 50

# Test worker manually
cd ~/shopify_review_page-scraping
source venv/bin/activate
cd url-finder-service
python worker.py
```

### Database connection errors

```bash
# Test database connection
psql -h your-rds-endpoint -U your_user -d shopify_processor

# Check if RDS security group allows EC2 instance
# In AWS Console: RDS → Security Groups → Inbound Rules
```

### Permission errors

```bash
# Ensure user has correct permissions
sudo chown -R ubuntu:ubuntu ~/shopify_review_page-scraping

# Check file permissions
ls -la ~/shopify_review_page-scraping/url-finder-service/worker.py
```

## Monitoring

### Check Worker Metrics

```bash
# Via API (if Flask app is running)
curl http://localhost:5001/api/worker/status | jq

# Via logs
sudo journalctl -u url-finder-worker | grep "processed_count\|saved_count"
```

### Database Queries

```bash
# Connect to database
psql -h your-rds-endpoint -U your_user -d shopify_processor

# Check pending stores
SELECT COUNT(*) FROM stores WHERE status = 'pending_url';

# Check processing stores
SELECT COUNT(*) FROM stores WHERE status = 'processing';

# Check needs_review stores
SELECT COUNT(*) FROM stores WHERE status = 'needs_review';

# Check not_found stores
SELECT COUNT(*) FROM stores WHERE status = 'not_found';
```

## Updating Code

```bash
# Pull latest changes
cd ~/shopify_review_page-scraping
git pull

# Restart worker
sudo systemctl restart url-finder-worker

# Check logs
sudo journalctl -u url-finder-worker -f
```

## Performance Tuning

Edit `.env` to adjust worker settings:

```env
# Process more stores per batch
WORKER_BATCH_SIZE=20

# Faster processing (less sleep)
WORKER_SLEEP_SECONDS=2

# More retries before giving up
WORKER_MAX_RETRIES=5
```

Then restart:
```bash
sudo systemctl restart url-finder-worker
```

## Next Steps

1. ✅ Deploy worker to EC2
2. ✅ Monitor logs and metrics
3. ✅ Adjust batch size and sleep intervals based on load
4. ✅ Set up CloudWatch alarms (optional)
5. ✅ Configure log rotation (optional)

## Optional: CloudWatch Integration

For better monitoring, you can send logs to CloudWatch:

```bash
# Install CloudWatch agent
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i amazon-cloudwatch-agent.deb

# Configure CloudWatch agent
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s
```
