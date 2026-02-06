"""Database management for Shopify Review Processor"""
import psycopg2
import psycopg2.extras
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import logging
try:
    from config import DATABASE_URL
except ImportError:
    import os
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://localhost/shopify_processor')

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, database_url: str = None):
        self.database_url = database_url or DATABASE_URL
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        conn = psycopg2.connect(self.database_url)
        return conn
    
    def init_database(self):
        """Initialize database schema"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Stores table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                id SERIAL PRIMARY KEY,
                store_name TEXT NOT NULL,
                country TEXT,
                review_date TEXT,
                review_text TEXT,
                usage_duration TEXT,
                rating INTEGER,
                base_url TEXT,
                url_verified BOOLEAN DEFAULT FALSE,
                verified_at TEXT,
                raw_emails TEXT,
                emails TEXT,
                emails_found INTEGER DEFAULT 0,
                emails_scraped_at TEXT,
                status TEXT DEFAULT 'pending_url',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                app_name TEXT
            )
        """)
        conn.commit()
        
        # Jobs table (for tracking scraping jobs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id SERIAL PRIMARY KEY,
                app_name TEXT NOT NULL,
                app_url TEXT NOT NULL,
                total_stores INTEGER DEFAULT 0,
                stores_processed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                progress_message TEXT,
                current_page INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                reviews_scraped INTEGER DEFAULT 0,
                max_reviews_limit INTEGER DEFAULT 0,
                max_pages_limit INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        
        # Migrate existing jobs table if needed
        try:
            cursor.execute("SELECT progress_message FROM jobs LIMIT 1")
        except psycopg2.errors.UndefinedColumn:
            logger.info("Migrating jobs table...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN progress_message TEXT")
            cursor.execute("ALTER TABLE jobs ADD COLUMN current_page INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN total_pages INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN reviews_scraped INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Migration complete")
        
        # Add max_reviews_limit and max_pages_limit columns if they don't exist
        try:
            cursor.execute("SELECT max_reviews_limit FROM jobs LIMIT 1")
        except psycopg2.errors.UndefinedColumn:
            logger.info("Adding limit columns to jobs table...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN max_reviews_limit INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN max_pages_limit INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Limit columns added")
        
        # Helper function to check if column exists
        def column_exists(table_name, column_name):
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
            """, (table_name, column_name))
            return cursor.fetchone()[0] > 0
        
        # Add email scraping retry tracking columns to stores table if they don't exist
        if not column_exists('stores', 'email_scraping_attempts'):
            logger.info("Adding email scraping retry tracking columns to stores table...")
            cursor.execute("ALTER TABLE stores ADD COLUMN email_scraping_attempts INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE stores ADD COLUMN email_scraping_last_error TEXT")
            cursor.execute("ALTER TABLE stores ADD COLUMN email_scraping_failed_at TIMESTAMP")
            conn.commit()
            logger.info("Email scraping retry columns added")
        
        conn.close()
        logger.info("Database initialized")
    
    def job_exists(self, app_url: str) -> bool:
        """Check if a job with this app_url already exists"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM jobs WHERE app_url = %s", (app_url,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def get_job_by_url(self, app_url: str) -> Optional[Dict]:
        """Get job by URL, returns job details if exists"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM jobs WHERE app_url = %s ORDER BY id DESC LIMIT 1", (app_url,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def is_job_complete(self, job_id: int) -> bool:
        """Check if a job is complete (status is 'completed' or 'finding_urls' or later)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            status = row[0]
            return status in ['finding_urls', 'scraping_emails', 'completed']
        return False
    
    def create_job(self, app_name: str, app_url: str, max_reviews_limit: int = 0, max_pages_limit: int = 0) -> int:
        """Create a new scraping job"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO jobs (app_name, app_url, status, max_reviews_limit, max_pages_limit)
            VALUES (%s, %s, 'scraping_reviews', %s, %s)
            RETURNING id
        """, (app_name, app_url, max_reviews_limit, max_pages_limit))
        
        job_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return job_id
    
    def update_job_status(self, job_id: int, status: str, total_stores: int = None, stores_processed: int = None, 
                         progress_message: str = None, current_page: int = None, total_pages: int = None,
                         reviews_scraped: int = None, max_reviews_limit: int = None, max_pages_limit: int = None):
        """Update job status"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params = [status]
        
        if total_stores is not None:
            updates.append("total_stores = %s")
            params.append(total_stores)
        
        if stores_processed is not None:
            updates.append("stores_processed = %s")
            params.append(stores_processed)
        
        if progress_message is not None:
            updates.append("progress_message = %s")
            params.append(progress_message)
        
        if current_page is not None:
            updates.append("current_page = %s")
            params.append(current_page)
        
        if total_pages is not None:
            updates.append("total_pages = %s")
            params.append(total_pages)
        
        if reviews_scraped is not None:
            updates.append("reviews_scraped = %s")
            params.append(reviews_scraped)
        
        if max_reviews_limit is not None:
            updates.append("max_reviews_limit = %s")
            params.append(max_reviews_limit)
        
        if max_pages_limit is not None:
            updates.append("max_pages_limit = %s")
            params.append(max_pages_limit)
        
        params.append(job_id)
        
        cursor.execute(f"""
            UPDATE jobs SET {', '.join(updates)}
            WHERE id = %s
        """, params)
        
        conn.commit()
        conn.close()
    
    def add_stores(self, stores: List[Dict], job_id: int, app_name: str):
        """Add stores from review scraping"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        for store in stores:
            cursor.execute("""
                INSERT INTO stores (
                    store_name, country, review_date, review_text, usage_duration, rating,
                    app_name, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending_url')
            """, (
                store.get('store_name'),
                store.get('country'),
                store.get('review_date'),
                store.get('review_text'),
                store.get('usage_duration'),
                store.get('rating'),
                app_name
            ))
        
        conn.commit()
        conn.close()
        logger.info(f"Added {len(stores)} stores to database")
    
    def get_pending_stores(self, limit: int = None) -> List[Dict]:
        """Get stores that need URL finding"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE status = 'pending_url' OR status = 'url_found'
            ORDER BY id
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        stores = []
        for row in rows:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            stores.append(store)
        
        return stores
    
    def get_next_pending_store(self) -> Optional[Dict]:
        """Get the next pending store (one at a time)"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT * FROM stores
            WHERE status = 'pending_url'
            ORDER BY id
            LIMIT 1
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            return store
        return None
    
    def count_pending_url_stores(self) -> int:
        """Count stores that still need URLs"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM stores
            WHERE status = 'pending_url'
        """)
        
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def get_stores_with_urls_no_emails(self, limit: int = None, app_name: str = None, cooldown_minutes: int = 60) -> List[Dict]:
        """Get stores that have URLs but no emails yet (for batch email scraping), optionally for a specific app.
        Excludes stores with email_scraping_failed status within cooldown period."""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE base_url IS NOT NULL 
            AND base_url != ''
            AND (status = 'url_verified' OR status = 'url_found')
            AND (emails IS NULL OR emails = '' OR emails = '[]')
            AND (
                status != 'email_scraping_failed' 
                OR email_scraping_failed_at IS NULL 
                OR email_scraping_failed_at < NOW() - INTERVAL '1 minute' * %s
            )
        """
        params = [cooldown_minutes]
        if app_name:
            query += " AND app_name = %s"
            params.append(app_name)
        query += " ORDER BY id"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        stores = []
        for row in rows:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            stores.append(store)
        
        return stores
    
    def skip_store(self, store_id: int):
        """Skip a store (mark as skipped)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET status = 'skipped', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (store_id,))
        
        conn.commit()
        conn.close()
    
    def update_store_url(self, store_id: int, url: str):
        """Update store with verified URL"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET base_url = %s, url_verified = TRUE, verified_at = CURRENT_TIMESTAMP,
                status = 'url_verified', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (url, store_id))
        
        conn.commit()
        conn.close()
    
    def update_store_emails(self, store_id: int, emails: List[str], raw_emails: Optional[List[str]] = None, scraping_error: Optional[str] = None):
        """Update store with scraped emails (both cleaned and raw).
        Note: If store is marked as email_scraping_failed or email_scraping_permanent_failure,
        that status is preserved and not overwritten here."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Check current status - don't overwrite failed/permanent_failure statuses
        cursor.execute("SELECT status FROM stores WHERE id = %s", (store_id,))
        current_status_row = cursor.fetchone()
        current_status = current_status_row[0] if current_status_row else None
        
        # If already marked as failed or permanent failure, preserve that status
        if current_status in ('email_scraping_failed', 'email_scraping_permanent_failure'):
            # Only update emails and timestamps, keep the failed status
            emails_json = json.dumps(emails)
            raw_emails_json = json.dumps(raw_emails) if raw_emails else None
            
            cursor.execute("""
                UPDATE stores
                SET emails = %s, raw_emails = %s, emails_found = %s, emails_scraped_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (emails_json, raw_emails_json, len(emails), store_id))
        else:
            # Normal update with status change
            emails_json = json.dumps(emails)
            raw_emails_json = json.dumps(raw_emails) if raw_emails else None
            
            if scraping_error:
                status = 'url_verified'
            elif len(emails) > 0:
                status = 'emails_found'
            else:
                status = 'no_emails_found'
            
            cursor.execute("""
                UPDATE stores
                SET emails = %s, raw_emails = %s, emails_found = %s, emails_scraped_at = CURRENT_TIMESTAMP,
                    status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (emails_json, raw_emails_json, len(emails), status, store_id))
        
        conn.commit()
        conn.close()
        
        if scraping_error:
            logger.warning(f"Store {store_id} marked as {current_status or 'url_verified'} but scraping had errors: {scraping_error}")
    
    def mark_email_scraping_failed(self, store_id: int, error_type: str, error_message: str):
        """Mark store as email scraping failed (will retry later after cooldown)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET status = 'email_scraping_failed',
                email_scraping_last_error = %s,
                email_scraping_failed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (f"{error_type}: {error_message}", store_id))
        
        conn.commit()
        conn.close()
        logger.info(f"Store {store_id} marked as email_scraping_failed: {error_type}")
    
    def mark_email_scraping_permanent_failure(self, store_id: int, error_message: str):
        """Mark store as permanent email scraping failure (after max retries)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET status = 'email_scraping_permanent_failure',
                email_scraping_last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (error_message, store_id))
        
        conn.commit()
        conn.close()
        logger.warning(f"Store {store_id} marked as email_scraping_permanent_failure: {error_message}")
    
    def increment_email_scraping_attempts(self, store_id: int):
        """Increment the retry attempt counter for email scraping"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET email_scraping_attempts = email_scraping_attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (store_id,))
        
        conn.commit()
        conn.close()
    
    def get_failed_stores_for_retry(self, cooldown_minutes: int = 60, limit: int = 10, app_name: str = None) -> List[Dict]:
        """Get stores with email_scraping_failed status that are ready for retry (after cooldown)"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE status = 'email_scraping_failed'
            AND email_scraping_failed_at IS NOT NULL
            AND email_scraping_failed_at < NOW() - INTERVAL '1 minute' * %s
        """
        params = [cooldown_minutes]
        
        if app_name:
            query += " AND app_name = %s"
            params.append(app_name)
        
        query += " ORDER BY email_scraping_failed_at"
        
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        stores = []
        for row in rows:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            stores.append(store)
        
        return stores
    
    def get_store(self, store_id: int) -> Optional[Dict]:
        """Get a single store by ID"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM stores WHERE id = %s", (store_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            return store
        return None
    
    def get_all_stores(self, app_name: str = None) -> List[Dict]:
        """Get all stores, optionally filtered by app name"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        if app_name:
            cursor.execute("SELECT * FROM stores WHERE app_name = %s ORDER BY id", (app_name,))
        else:
            cursor.execute("SELECT * FROM stores ORDER BY id")
        
        rows = cursor.fetchall()
        conn.close()
        
        stores = []
        for row in rows:
            store = dict(row)
            if store.get('emails'):
                try:
                    store['emails'] = json.loads(store['emails'])
                except:
                    store['emails'] = []
            else:
                store['emails'] = []
            if store.get('raw_emails'):
                try:
                    store['raw_emails'] = json.loads(store['raw_emails'])
                except:
                    store['raw_emails'] = []
            else:
                store['raw_emails'] = []
            stores.append(store)
        
        return stores
    
    def get_job(self, job_id: int) -> Optional[Dict]:
        """Get job by ID"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def get_all_jobs(self) -> List[Dict]:
        """Get all jobs ordered by creation date"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def delete_stores(self, store_ids: List[int]) -> Dict:
        """Delete stores by IDs and return info about deleted stores and jobs"""
        if not store_ids:
            return {'stores_deleted': 0, 'jobs_deleted': 0, 'app_urls': []}
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        placeholders = ','.join(['%s'] * len(store_ids))
        cursor.execute(f"SELECT DISTINCT app_name FROM stores WHERE id IN ({placeholders})", store_ids)
        app_names = [row[0] for row in cursor.fetchall() if row[0]]
        
        app_urls = []
        if app_names:
            app_placeholders = ','.join(['%s'] * len(app_names))
            cursor.execute(f"SELECT app_url FROM jobs WHERE app_name IN ({app_placeholders})", app_names)
            app_urls = [row[0] for row in cursor.fetchall()]
        
        cursor.execute(f"DELETE FROM stores WHERE id IN ({placeholders})", store_ids)
        stores_deleted = cursor.rowcount
        
        jobs_deleted = 0
        if app_names:
            cursor.execute(f"DELETE FROM jobs WHERE app_name IN ({app_placeholders})", app_names)
            jobs_deleted = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        logger.info(f"Deleted {stores_deleted} stores and {jobs_deleted} jobs")
        return {
            'stores_deleted': stores_deleted,
            'jobs_deleted': jobs_deleted,
            'app_urls': app_urls
        }
    
    def get_statistics(self, job_id: int = None) -> Dict:
        """Get processing statistics"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        if job_id:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending_url' THEN 1 ELSE 0 END) as pending_url,
                    SUM(CASE WHEN status = 'url_verified' THEN 1 ELSE 0 END) as url_verified,
                    SUM(CASE WHEN status = 'emails_found' THEN 1 ELSE 0 END) as emails_found,
                    COALESCE(SUM(emails_found), 0) as total_emails
                FROM stores
                WHERE app_name = (SELECT app_name FROM jobs WHERE id = %s)
            """, (job_id,))
        else:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending_url' THEN 1 ELSE 0 END) as pending_url,
                    SUM(CASE WHEN status = 'url_verified' THEN 1 ELSE 0 END) as url_verified,
                    SUM(CASE WHEN status = 'emails_found' THEN 1 ELSE 0 END) as emails_found,
                    COALESCE(SUM(emails_found), 0) as total_emails
                FROM stores
            """)
        
        row = cursor.fetchone()
        conn.close()
        
        stats = dict(row) if row else {}
        if stats.get('total_emails') is None:
            stats['total_emails'] = 0
        return stats
