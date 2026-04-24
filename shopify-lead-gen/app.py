"""Flask application — all API routes."""
import csv
import io
import logging

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

import config
import database as db
import pipeline
from scrapers.review_scraper import extract_app_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

db.init_db()

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.post("/api/jobs")
def create_job():
    body = request.get_json(silent=True) or {}
    app_url = (body.get("app_url") or "").strip()
    if not app_url:
        return jsonify({"error": "app_url is required"}), 400

    limit_count = body.get("limit_count")
    if limit_count is not None:
        try:
            limit_count = int(limit_count)
            if limit_count <= 0:
                limit_count = None
        except (TypeError, ValueError):
            limit_count = None

    app_name = extract_app_name(app_url)
    job = db.create_job(app_url=app_url, app_name=app_name, limit_count=limit_count)
    pipeline.start_job(job["id"])
    return jsonify(job), 201


@app.get("/api/jobs")
def list_jobs():
    return jsonify(db.list_jobs())


@app.get("/api/jobs/<int:job_id>")
def get_job(job_id: int):
    status = pipeline.get_status(job_id)
    if not status:
        return jsonify({"error": "not found"}), 404
    return jsonify(status)


@app.post("/api/jobs/<int:job_id>/pause")
def pause_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    pipeline.stop_job(job_id)
    db.update_job(job_id, status="paused")
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/resume")
def resume_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    if pipeline.is_running(job_id):
        return jsonify({"error": "already running"}), 409

    # Reset any stores stuck in 'processing' back to 'pending'
    import psycopg2
    try:
        conn = db._connect()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stores SET status='pending', updated_at=NOW() "
                    "WHERE job_id=%s AND status='processing'",
                    (job_id,)
                )
        conn.close()
    except Exception as e:
        logger.warning("Could not reset processing stores: %s", e)

    pipeline.start_job(job_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

@app.get("/api/jobs/<int:job_id>/stores")
def list_stores(job_id: int):
    if not db.get_job(job_id):
        return jsonify({"error": "not found"}), 404
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(10, int(request.args.get("per_page", 50))))
    stores = db.list_stores(job_id, page=page, per_page=per_page)
    total = db.count_stores(job_id)
    return jsonify({"stores": stores, "total": total, "page": page, "per_page": per_page})


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

@app.get("/api/jobs/<int:job_id>/export")
def export_csv(job_id: int):
    if not db.get_job(job_id):
        return jsonify({"error": "not found"}), 404

    stores = db.list_stores(job_id, page=1, per_page=10_000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["store_name", "country", "store_url", "emails", "rating", "review_date", "status"])
    for s in stores:
        emails = s.get("emails") or []
        writer.writerow([
            s.get("store_name", ""),
            s.get("country", ""),
            s.get("store_url", ""),
            "; ".join(emails),
            s.get("rating", ""),
            s.get("review_date", ""),
            s.get("status", ""),
        ])

    buf.seek(0)
    job = db.get_job(job_id)
    filename = f"{job.get('app_name','job')}_{job_id}_leads.csv"
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    from flask import render_template
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
