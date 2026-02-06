#!/usr/bin/env python3
"""Standalone background worker for URL finding service"""
import sys
import os
import logging
import signal
from pathlib import Path

# Add the service directory to Python path
service_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(service_dir))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(service_dir / 'worker.log')
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Main entry point for standalone worker"""
    try:
        from config import WORKER_ENABLED, DATABASE_URL
        from database import Database
        from modules.background_worker import BackgroundWorker
        
        if not WORKER_ENABLED:
            logger.warning("WORKER_ENABLED is False. Set WORKER_ENABLED=true to run the worker.")
            sys.exit(1)
        
        logger.info("Initializing database connection...")
        db = Database(DATABASE_URL)
        
        logger.info("Initializing background worker...")
        worker = BackgroundWorker(db)
        
        logger.info("Starting background worker (press Ctrl+C to stop)...")
        
        # Run worker (will handle signals internally)
        try:
            worker.run_continuous()
        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt, shutting down...")
            worker.stop()
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            sys.exit(1)
        
        logger.info("Worker stopped gracefully")
        
    except Exception as e:
        logger.error(f"Failed to start worker: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
