"""PostgreSQL schema + query helpers."""
import logging
import time
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                   SERIAL PRIMARY KEY,
    app_url              TEXT NOT NULL,
    app_name             TEXT,
    limit_count          INTEGER,
    status               TEXT NOT NULL DEFAULT 'idle',
    total_reviews_found  INTEGER NOT NULL DEFAULT 0,
    stores_processed     INTEGER NOT NULL DEFAULT 0,
    error                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stores (
    id               SERIAL PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    store_name       TEXT NOT NULL,
    country          TEXT,
    rating           NUMERIC,
    review_text      TEXT,
    review_date      TEXT,
    usage_duration   TEXT,

    store_url        TEXT,
    url_confidence   FLOAT,

    emails           TEXT[],

    status           TEXT NOT NULL DEFAULT 'pending',
    error            TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (job_id, store_name)
);

CREATE INDEX IF NOT EXISTS stores_job_status ON stores(job_id, status);
"""


def _connect(retries: int = 5) -> psycopg2.extensions.connection:
    last_err = None
    for i in range(retries):
        try:
            return psycopg2.connect(config.DATABASE_URL)
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(2 ** i)
    raise last_err


def init_db() -> None:
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
        logger.info("Database schema ready")
    finally:
        conn.close()


def _row(cur) -> Optional[Dict]:
    row = cur.fetchone()
    return dict(row) if row else None


def _rows(cur) -> List[Dict]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(app_url: str, app_name: str, limit_count: Optional[int]) -> Dict:
    conn = _connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (app_url, app_name, limit_count)
                    VALUES (%s, %s, %s)
                    RETURNING *
                    """,
                    (app_url, app_name, limit_count),
                )
                return dict(cur.fetchone())
    finally:
        conn.close()


def get_job(job_id: int) -> Optional[Dict]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            return _row(cur)
    finally:
        conn.close()


def list_jobs() -> List[Dict]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            return _rows(cur)
    finally:
        conn.close()


def update_job(job_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {"status", "total_reviews_found", "stores_processed", "app_name", "error"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE jobs SET {set_clause}, updated_at = NOW() WHERE id = %s",
                    (*fields.values(), job_id),
                )
    finally:
        conn.close()


def get_job_stats(job_id: int) -> Dict:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'pending')         AS pending,
                    COUNT(*) FILTER (WHERE status = 'url_found')        AS url_found,
                    COUNT(*) FILTER (WHERE status = 'url_not_found')    AS url_not_found,
                    COUNT(*) FILTER (WHERE status = 'emails_found')     AS emails_found,
                    COUNT(*) FILTER (WHERE status = 'no_emails')        AS no_emails,
                    COUNT(*) FILTER (WHERE status = 'failed')           AS failed,
                    COUNT(*)                                             AS total
                FROM stores WHERE job_id = %s
                """,
                (job_id,),
            )
            return dict(cur.fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

def upsert_store(job_id: int, data: Dict) -> Optional[int]:
    """Insert store; skip silently on duplicate (same job + store_name)."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO stores
                        (job_id, store_name, country, rating, review_text, review_date, usage_duration)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id, store_name) DO NOTHING
                    RETURNING id
                    """,
                    (
                        job_id,
                        data["store_name"],
                        data.get("country"),
                        data.get("rating"),
                        data.get("review_text"),
                        data.get("review_date"),
                        data.get("usage_duration"),
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None
    finally:
        conn.close()


def get_store(store_id: int) -> Optional[Dict]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stores WHERE id = %s", (store_id,))
            return _row(cur)
    finally:
        conn.close()


def get_next_pending_store(job_id: int) -> Optional[Dict]:
    conn = _connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM stores
                    WHERE job_id = %s AND status = 'pending'
                    ORDER BY id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                    (job_id,),
                )
                row = _row(cur)
                if row:
                    cur.execute(
                        "UPDATE stores SET status = 'processing', updated_at = NOW() WHERE id = %s",
                        (row["id"],),
                    )
                return row
    finally:
        conn.close()


def update_store(store_id: int, **fields) -> None:
    allowed = {"status", "store_url", "url_confidence", "emails", "error"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE stores SET {set_clause}, updated_at = NOW() WHERE id = %s",
                    (*fields.values(), store_id),
                )
    finally:
        conn.close()


def list_stores(job_id: int, page: int = 1, per_page: int = 50) -> List[Dict]:
    offset = (page - 1) * per_page
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, store_name, country, rating, review_date,
                       store_url, url_confidence, emails, status, error
                FROM stores
                WHERE job_id = %s
                ORDER BY id
                LIMIT %s OFFSET %s
                """,
                (job_id, per_page, offset),
            )
            return _rows(cur)
    finally:
        conn.close()


def count_stores(job_id: int) -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stores WHERE job_id = %s", (job_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()
