"""PostgreSQL schema + query helpers."""
import logging
import time
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

import config

logger = logging.getLogger(__name__)



def _connect(retries: int = 5) -> psycopg2.extensions.connection:
    last_err = None
    for i in range(retries):
        try:
            return psycopg2.connect(config.DATABASE_URL)
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(2 ** i)
    raise last_err


def _col_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return cur.fetchone() is not None


def _parse_legacy_emails(raw: str) -> list:
    """Parse any format the v1 emails column might have used."""
    import json as _json
    if not raw or raw.strip() in ("", "null", "[]", "{}"):
        return []
    raw = raw.strip()
    # JSON array: ["a@b.com", ...]
    if raw.startswith("["):
        try:
            result = _json.loads(raw)
            if isinstance(result, list):
                return [e for e in result if isinstance(e, str) and e]
        except Exception:
            pass
    # PostgreSQL array literal: {a@b.com,c@d.com}
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1]
        return [e.strip().strip('"') for e in inner.split(",") if e.strip()]
    # Single email stored as plain string
    if "@" in raw:
        return [raw]
    return []


def _migrate(cur) -> None:
    """Bring an existing v1 schema up to v2 without dropping data."""

    # ---- jobs table --------------------------------------------------------
    cur.execute("CREATE TABLE IF NOT EXISTS jobs (id SERIAL PRIMARY KEY, app_url TEXT NOT NULL, app_name TEXT, limit_count INTEGER, status TEXT NOT NULL DEFAULT 'idle', total_reviews_found INTEGER NOT NULL DEFAULT 0, stores_processed INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

    for col, defn in [
        ("limit_count",         "INTEGER"),
        ("total_reviews_found", "INTEGER NOT NULL DEFAULT 0"),
        ("stores_processed",    "INTEGER NOT NULL DEFAULT 0"),
        ("error",               "TEXT"),
        ("updated_at",          "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
        ("scrape_cursor",       "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if not _col_exists(cur, "jobs", col):
            cur.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
            logger.info("jobs: added column %s", col)

    # ---- stores table -------------------------------------------------------
    # If the old table exists without job_id we need to add it.
    cur.execute("CREATE TABLE IF NOT EXISTS stores (id SERIAL PRIMARY KEY, store_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

    # Add job_id if missing (nullable first so existing rows don't violate NOT NULL)
    if not _col_exists(cur, "stores", "job_id"):
        # Create a default job to attach old rows to
        cur.execute(
            "INSERT INTO jobs (app_url, app_name, status) VALUES (%s, %s, %s) RETURNING id",
            ("migrated://legacy", "legacy_data", "completed"),
        )
        default_job_id = cur.fetchone()[0]
        cur.execute("ALTER TABLE stores ADD COLUMN job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE")
        cur.execute("UPDATE stores SET job_id = %s WHERE job_id IS NULL", (default_job_id,))
        cur.execute("ALTER TABLE stores ALTER COLUMN job_id SET NOT NULL")
        logger.info("stores: added job_id column, legacy rows assigned to job #%d", default_job_id)

    for col, defn in [
        ("country",        "TEXT"),
        ("rating",         "NUMERIC"),
        ("review_text",    "TEXT"),
        ("review_date",    "TEXT"),
        ("usage_duration", "TEXT"),
        ("store_url",      "TEXT"),
        ("url_confidence", "FLOAT"),
        ("status",         "TEXT NOT NULL DEFAULT 'pending'"),
        ("error",          "TEXT"),
        ("updated_at",     "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
        ("attempt_count",  "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if not _col_exists(cur, "stores", col):
            cur.execute(f"ALTER TABLE stores ADD COLUMN {col} {defn}")
            logger.info("stores: added column %s", col)

    # emails needs special handling: v1 stored it as TEXT with inconsistent formats
    # (JSON arrays, PostgreSQL array literals, plain strings, or NULL).
    # v2 needs TEXT[]. Convert in Python row-by-row so each format is handled safely.
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='stores' AND column_name='emails'"
    )
    emails_type_row = cur.fetchone()
    if not emails_type_row:
        cur.execute("ALTER TABLE stores ADD COLUMN emails TEXT[]")
        logger.info("stores: added column emails TEXT[]")
    elif emails_type_row[0] != "ARRAY":
        logger.info("stores: converting emails column from %s → TEXT[] (row-by-row)", emails_type_row[0])
        cur.execute("ALTER TABLE stores RENAME COLUMN emails TO emails_old")
        cur.execute("ALTER TABLE stores ADD COLUMN emails TEXT[]")
        cur.execute("SELECT id, emails_old FROM stores WHERE emails_old IS NOT NULL AND emails_old <> ''")
        rows = cur.fetchall()
        converted = 0
        for row_id, raw in rows:
            parsed = _parse_legacy_emails(raw)
            if parsed:
                cur.execute("UPDATE stores SET emails = %s WHERE id = %s", (parsed, row_id))
                converted += 1
        cur.execute("ALTER TABLE stores DROP COLUMN emails_old")
        logger.info("stores: emails converted — %d/%d rows had values", converted, len(rows))

    # Always ensure the status default is 'pending' (v1 used 'pending_url').
    # This matters even when the column already existed from v1.
    cur.execute("ALTER TABLE stores ALTER COLUMN status SET DEFAULT 'pending'")

    # Normalize v1 statuses → v2: any row without a URL is truly pending
    cur.execute("""
        UPDATE stores
        SET status = 'pending'
        WHERE status IN ('pending_url', 'processing')
          AND (store_url IS NULL OR store_url = '')
    """)
    normalized = cur.rowcount
    if normalized:
        logger.info("stores: normalized %d legacy 'pending_url' rows → 'pending'", normalized)

    # Migrate old JSON emails → TEXT[] if raw_emails / emails columns exist as text
    if _col_exists(cur, "stores", "raw_emails"):
        cur.execute("""
            UPDATE stores
            SET emails = ARRAY(
                SELECT jsonb_array_elements_text(raw_emails::jsonb)
            )
            WHERE raw_emails IS NOT NULL
              AND raw_emails <> ''
              AND raw_emails <> '[]'
              AND (emails IS NULL OR emails = '{}')
        """)
        logger.info("stores: migrated raw_emails JSON → emails TEXT[]")

    # Deduplicate before adding unique constraint — keep the highest id per (job_id, store_name)
    cur.execute("""
        DELETE FROM stores
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM stores
            GROUP BY job_id, store_name
        )
    """)
    deleted = cur.rowcount
    if deleted:
        logger.info("stores: removed %d duplicate rows before adding unique constraint", deleted)

    # Unique constraint
    cur.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'stores_job_id_store_name_key'
            ) THEN
                ALTER TABLE stores ADD CONSTRAINT stores_job_id_store_name_key UNIQUE (job_id, store_name);
            END IF;
        END $$
    """)

    # Index
    cur.execute("CREATE INDEX IF NOT EXISTS stores_job_status ON stores(job_id, status)")


def init_db() -> None:
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                _migrate(cur)
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


def delete_job(job_id: int) -> None:
    """Delete a job and all its stores (CASCADE)."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
    finally:
        conn.close()


def find_job_by_url(app_url: str) -> Optional[Dict]:
    """Return the most recent job for this app_url, or None."""
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE app_url = %s ORDER BY created_at DESC LIMIT 1",
                (app_url,),
            )
            return _row(cur)
    finally:
        conn.close()


def restart_job(job_id: int, limit_count: Optional[int]) -> Dict:
    """
    Prepare an existing job for a new run:
    - Reset counters and status
    - Update limit_count
    - Reset failed/processing stores back to pending so they get retried
    - Leave already-completed stores (emails_found, no_emails, url_not_found) untouched
    """
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'idle',
                        limit_count = %s,
                        stores_processed = 0,
                        error = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (limit_count, job_id),
                )
                # Reset failed and stuck-processing stores so they're retried
                cur.execute(
                    """
                    UPDATE stores
                    SET status = 'pending', error = NULL, attempt_count = 0, updated_at = NOW()
                    WHERE job_id = %s AND status IN ('failed', 'processing')
                    """,
                    (job_id,),
                )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
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


def update_scrape_cursor(job_id: int, page: int) -> None:
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET scrape_cursor = %s, updated_at = NOW() WHERE id = %s AND scrape_cursor < %s",
                    (page, job_id, page),
                )
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
    """
    Insert store; if a row with the same (job_id, store_name) already exists,
    backfill any blank review-metadata fields (country, rating, review_text,
    review_date, usage_duration) so re-runs enrich existing records.

    Returns the store id when the row is newly inserted, None when it already existed.
    The caller uses None to count the store as a duplicate (skipped).
    """
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                # Try fresh insert first
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
                if row:
                    return row[0]  # newly inserted

                # Row already exists — backfill blank fields from the incoming data
                # so that re-runs fix missing country / rating / review text.
                country      = data.get("country") or None
                rating       = data.get("rating")
                review_text  = data.get("review_text") or None
                review_date  = data.get("review_date") or None
                usage_dur    = data.get("usage_duration") or None

                if any(v is not None for v in (country, rating, review_text, review_date, usage_dur)):
                    cur.execute(
                        """
                        UPDATE stores SET
                            country        = COALESCE(NULLIF(country, ''),        %s),
                            rating         = COALESCE(rating,                     %s),
                            review_text    = COALESCE(NULLIF(review_text, ''),    %s),
                            review_date    = COALESCE(NULLIF(review_date, ''),    %s),
                            usage_duration = COALESCE(NULLIF(usage_duration, ''), %s)
                        WHERE job_id = %s AND store_name = %s
                        """,
                        (country, rating, review_text, review_date, usage_dur, job_id, data["store_name"]),
                    )

                return None  # existing store — caller counts as skipped
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


def increment_attempt_count(store_id: int) -> int:
    """Increment attempt_count and return the new value."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stores SET attempt_count = attempt_count + 1, updated_at = NOW() WHERE id = %s RETURNING attempt_count",
                    (store_id,),
                )
                row = cur.fetchone()
                return row[0] if row else 1
    finally:
        conn.close()


def reset_failed_stores(job_id: int) -> int:
    """Reset failed stores back to pending so they can be retried. Returns count reset."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stores SET status = 'pending', error = NULL, updated_at = NOW() WHERE job_id = %s AND status = 'failed'",
                    (job_id,),
                )
                return cur.rowcount
    finally:
        conn.close()


def update_store(store_id: int, **fields) -> None:
    allowed = {"status", "store_url", "url_confidence", "emails", "error", "attempt_count"}
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
    import json as _json
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
            rows = _rows(cur)
    finally:
        conn.close()

    # Defensive: ensure emails is always a list regardless of how it was stored
    for row in rows:
        e = row.get("emails")
        if e is None:
            row["emails"] = []
        elif isinstance(e, str):
            try:
                parsed = _json.loads(e)
                row["emails"] = parsed if isinstance(parsed, list) else []
            except Exception:
                row["emails"] = []
        elif not isinstance(e, list):
            row["emails"] = []
    return rows


def count_stores(job_id: int) -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stores WHERE job_id = %s", (job_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def count_pending(job_id: int) -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM stores WHERE job_id = %s AND status = 'pending'",
                (job_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def increment_stores_processed(job_id: int) -> None:
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET stores_processed = stores_processed + 1, updated_at = NOW() WHERE id = %s",
                    (job_id,),
                )
    finally:
        conn.close()
