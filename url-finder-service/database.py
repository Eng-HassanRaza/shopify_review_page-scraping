"""Database management for Shopify Review Processor"""
import psycopg2
import psycopg2.extras
import json
import time
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
    
    def get_connection(self, retries: int = 3, retry_delay: float = 1.0):
        """Get database connection with retry logic"""
        for attempt in range(retries):
            try:
                conn = psycopg2.connect(self.database_url)
                return conn
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                if attempt < retries - 1:
                    logger.warning(f"Database connection failed (attempt {attempt + 1}/{retries}): {e}. Retrying...")
                    time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                else:
                    logger.error(f"Database connection failed after {retries} attempts: {e}")
                    raise
    
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
        
        # Helper function to check if column exists
        def column_exists(table_name, column_name):
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
            """, (table_name, column_name))
            return cursor.fetchone()[0] > 0
        
        # Migrate existing jobs table if needed
        if not column_exists('jobs', 'progress_message'):
            logger.info("Migrating jobs table...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN progress_message TEXT")
            cursor.execute("ALTER TABLE jobs ADD COLUMN current_page INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN total_pages INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN reviews_scraped INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Migration complete")
        
        # Add max_reviews_limit and max_pages_limit columns if they don't exist
        if not column_exists('jobs', 'max_reviews_limit'):
            logger.info("Adding limit columns to jobs table...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN max_reviews_limit INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN max_pages_limit INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Limit columns added")
        
        # Add URL finding tracking columns to stores table if they don't exist
        if not column_exists('stores', 'url_finding_attempts'):
            logger.info("Adding URL finding tracking columns to stores table...")
            cursor.execute("ALTER TABLE stores ADD COLUMN url_finding_attempts INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE stores ADD COLUMN url_finding_error TEXT")
            cursor.execute("ALTER TABLE stores ADD COLUMN url_finding_provider TEXT")
            cursor.execute("ALTER TABLE stores ADD COLUMN url_confidence FLOAT")
            conn.commit()
            logger.info("URL finding columns added")
        
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
    
    def get_pending_stores(self, limit: int = None, app_name: str = None) -> List[Dict]:
        """Get stores that need URL finding, optionally filtered by app_name"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE status = 'pending_url' OR status = 'url_found'
        """
        params = []
        if app_name:
            query += " AND app_name = %s"
            params.append(app_name)
        query += " ORDER BY id"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        if params:
            cursor.execute(query, params)
        else:
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
    
    def get_next_pending_store(self, app_name: str = None) -> Optional[Dict]:
        """Get the next pending store (one at a time), optionally for a specific app"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        if app_name:
            cursor.execute("""
                SELECT * FROM stores
                WHERE status = 'pending_url' AND app_name = %s
                ORDER BY id
                LIMIT 1
            """, (app_name,))
        else:
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
    
    def count_pending_url_stores(self, app_name: str = None) -> int:
        """Count stores that still need URLs, optionally for a specific app"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if app_name:
            cursor.execute("""
                SELECT COUNT(*) FROM stores
                WHERE status = 'pending_url' AND app_name = %s
            """, (app_name,))
        else:
            cursor.execute("""
                SELECT COUNT(*) FROM stores
                WHERE status = 'pending_url'
            """)
        
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def get_stores_with_urls_no_emails(self, limit: int = None) -> List[Dict]:
        """Get stores that have URLs but no emails yet (for batch email scraping)"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE base_url IS NOT NULL 
            AND base_url != ''
            AND (status = 'url_verified' OR status = 'url_found')
            AND (emails IS NULL OR emails = '' OR emails = '[]')
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
    
    def update_store_url(self, store_id: int, url: str, confidence: float = None, provider: str = None):
        """Update store with verified URL"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = [
            "base_url = %s",
            "url_verified = TRUE",
            "verified_at = CURRENT_TIMESTAMP",
            "status = 'url_verified'",
            "updated_at = CURRENT_TIMESTAMP",
            "url_finding_error = NULL"  # Clear any previous errors
        ]
        params = [url]
        
        if confidence is not None:
            updates.append("url_confidence = %s")
            params.append(confidence)
        
        if provider:
            updates.append("url_finding_provider = %s")
            params.append(provider)
        
        params.append(store_id)
        
        cursor.execute(f"""
            UPDATE stores
            SET {', '.join(updates)}
            WHERE id = %s
        """, params)
        
        conn.commit()
        conn.close()
    
    def update_store_emails(self, store_id: int, emails: List[str], raw_emails: Optional[List[str]] = None, scraping_error: Optional[str] = None):
        """Update store with scraped emails (both cleaned and raw)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
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
            logger.warning(f"Store {store_id} marked as {status} but scraping had errors: {scraping_error}")
    
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
    
    def lock_store_for_processing(self, store_id: int) -> bool:
        """Lock a store for processing using SELECT FOR UPDATE. Returns True if locked successfully."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Use SELECT FOR UPDATE NOWAIT to prevent blocking
            cursor.execute("""
                SELECT id FROM stores
                WHERE id = %s AND (status != 'processing' OR updated_at < NOW() - INTERVAL '30 minutes')
                FOR UPDATE NOWAIT
            """, (store_id,))
            
            if cursor.fetchone():
                # Lock acquired, mark as processing
                cursor.execute("""
                    UPDATE stores
                    SET status = 'processing', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (store_id,))
                conn.commit()
                conn.close()
                return True
            else:
                conn.rollback()
                conn.close()
                return False
        except psycopg2.OperationalError:
            # Lock failed (another process is processing this store)
            conn.rollback()
            conn.close()
            return False
    
    def unlock_store(self, store_id: int):
        """Remove processing lock from a store (revert to pending_url if no URL found)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET status = CASE 
                WHEN base_url IS NOT NULL AND base_url != '' THEN status
                ELSE 'pending_url'
            END,
            updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'processing'
        """, (store_id,))
        
        conn.commit()
        conn.close()
    
    def mark_store_needs_review(self, store_id: int, reason: str = None, provider: str = None, confidence: float = None):
        """Mark store as needing manual review (low confidence result)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = [
            "status = 'needs_review'",
            "updated_at = CURRENT_TIMESTAMP"
        ]
        params = []
        
        if reason:
            updates.append("url_finding_error = %s")
            params.append(reason)
        
        if provider:
            updates.append("url_finding_provider = %s")
            params.append(provider)
        
        if confidence is not None:
            updates.append("url_confidence = %s")
            params.append(confidence)
        
        params.append(store_id)
        
        cursor.execute(f"""
            UPDATE stores
            SET {', '.join(updates)}
            WHERE id = %s
        """, params)
        
        conn.commit()
        conn.close()
    
    def mark_store_not_found(self, store_id: int, reason: str = None, provider: str = None):
        """Mark store as not found (no results from any provider)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = [
            "status = 'not_found'",
            "updated_at = CURRENT_TIMESTAMP"
        ]
        params = []
        
        if reason:
            updates.append("url_finding_error = %s")
            params.append(reason)
        
        if provider:
            updates.append("url_finding_provider = %s")
            params.append(provider)
        
        params.append(store_id)
        
        cursor.execute(f"""
            UPDATE stores
            SET {', '.join(updates)}
            WHERE id = %s
        """, params)
        
        conn.commit()
        conn.close()
    
    def increment_url_finding_attempts(self, store_id: int):
        """Increment the retry attempt counter for a store"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET url_finding_attempts = url_finding_attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (store_id,))
        
        conn.commit()
        conn.close()
    
    def get_pending_stores_excluding_processing(self, limit: int = None, app_name: str = None) -> List[Dict]:
        """Get stores that need URL finding, excluding those currently being processed"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        query = """
            SELECT * FROM stores
            WHERE (status = 'pending_url' OR status = 'url_found')
            AND (status != 'processing' OR updated_at < NOW() - INTERVAL '30 minutes')
        """
        params = []
        
        if app_name:
            query += " AND app_name = %s"
            params.append(app_name)
        
        query += " ORDER BY id"
        
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        if params:
            cursor.execute(query, params)
        else:
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
    
    def unlock_stuck_stores(self, timeout_minutes: int = 30) -> int:
        """Unlock stores that have been stuck in processing status for too long"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE stores
            SET status = CASE 
                WHEN base_url IS NOT NULL AND base_url != '' THEN status
                ELSE 'pending_url'
            END,
            updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
            AND updated_at < NOW() - INTERVAL '%s minutes'
        """, (timeout_minutes,))
        
        unlocked_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if unlocked_count > 0:
            logger.info(f"Unlocked {unlocked_count} stuck stores")
        
        return unlocked_count
