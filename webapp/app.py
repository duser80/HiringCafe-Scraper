"""Simple local web UI for testing the HiringCafe scraper.

Run with:
    ./.venv/bin/python webapp/app.py
then open http://127.0.0.1:5001
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from pathlib import Path

# Allow running as `python webapp/app.py` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, render_template, request, send_file

from hiringcafe_scraper.client import HiringCafeClient, HiringCafeError
from hiringcafe_scraper.exporter import (
    attach_validation_columns,
    build_dataframe,
    export_jobs,
    export_validation_report,
)
from hiringcafe_scraper.search import (
    COMMITMENT_TYPES,
    SENIORITY_LEVELS,
    WORKPLACE_TYPES,
    build_search_state,
    fetch_all_jobs,
)
from hiringcafe_scraper.validator import criteria_from_params, validate_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("webapp")

app = Flask(__name__)

RESULTS_DIR = PROJECT_ROOT / "results"

# In-memory registry of scrape jobs for this single-user local tool.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _run_scrape(job_id: str, params: dict) -> None:
    job = JOBS[job_id]

    def on_progress(page: int, new_hits: int, total: int) -> None:
        with JOBS_LOCK:
            job["pages_fetched"] = page + 1
            job["jobs_collected"] = total

    try:
        search_state = build_search_state(
            query=params["query"],
            sort_by=params["sort"],
            location=params["location"] or None,
            workplace_types=params["workplace"] or None,
            commitment_types=params["commitment"] or None,
            seniority_levels=params["seniority"] or None,
            days_old=params["days_old"],
        )
        with JOBS_LOCK:
            job["search_state"] = search_state
            job["status"] = "scraping"

        client = HiringCafeClient(delay=1.0)

        # Capture the reported total on the first page via a wrapped client call.
        original_search_page = client.search_page

        def search_page_wrapper(state: dict, page: int) -> dict:
            page_props = original_search_page(state, page)
            if page == 0:
                with JOBS_LOCK:
                    job["total_count"] = page_props.get("ssrTotalCount") or 0
            return page_props

        client.search_page = search_page_wrapper

        jobs = fetch_all_jobs(
            client,
            search_state,
            max_pages=params["max_pages"],
            on_progress=on_progress,
        )

        # Data-quality validation: check every job against the requested filters.
        criteria = criteria_from_params(
            query=params["query"],
            workplace_types=params["workplace"],
            commitment_types=params["commitment"],
            seniority_levels=params["seniority"],
            days_old=params["days_old"],
        )
        validation = validate_jobs(jobs, criteria, search_state)

        base_name = job["base_name"]
        written = export_jobs(
            jobs, RESULTS_DIR, base_name, ["json", "xlsx", "csv"], validation=validation
        )
        written.append(export_validation_report(
            validation, RESULTS_DIR / f"{base_name}_validation.json"
        ))

        df = attach_validation_columns(build_dataframe(jobs), validation)
        preview_cols = [
            "title", "company", "location", "workplace_type", "seniority_level",
            "salary_min_yearly", "salary_max_yearly", "salary_currency",
            "published_at", "apply_url", "filter_match", "validation_issues",
        ]
        preview = df[preview_cols].fillna("").head(200).to_dict(orient="records")

        # Compact validation summary for the UI (exclude per-job details).
        validation_summary = {
            "exact_match_count": validation["exact_match_count"],
            "exact_match_rate": validation["exact_match_rate"],
            "total_jobs": validation["total_jobs"],
            "checks": validation["checks"],
            "mismatch_count": sum(1 for j in validation["job_issues"] if j["hard"]),
            "warning_count": sum(
                1 for j in validation["job_issues"] if j["soft"] and not j["hard"]
            ),
        }

        with JOBS_LOCK:
            job["status"] = "done"
            job["jobs_collected"] = len(jobs)
            job["files"] = [p.name for p in written]
            job["preview"] = preview
            job["validation"] = validation_summary
    except HiringCafeError as exc:
        logger.error("Scrape job %s failed: %s", job_id, exc)
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(exc)
    except Exception as exc:  # surface unexpected failures to the UI too
        logger.exception("Unexpected error in scrape job %s", job_id)
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = f"Unexpected error: {exc}"


@app.get("/")
def index():
    return render_template(
        "index.html",
        workplace_types=WORKPLACE_TYPES,
        commitment_types=COMMITMENT_TYPES,
        seniority_levels=SENIORITY_LEVELS,
    )


@app.post("/api/scrape")
def start_scrape():
    payload = request.get_json(force=True)

    query = (payload.get("query") or "").strip()
    location = (payload.get("location") or "").strip()

    params = {
        "query": query,
        "location": location,
        "workplace": [w for w in payload.get("workplace", []) if w in WORKPLACE_TYPES],
        "commitment": [c for c in payload.get("commitment", []) if c in COMMITMENT_TYPES],
        "seniority": [s for s in payload.get("seniority", []) if s in SENIORITY_LEVELS],
        "days_old": int(payload["days_old"]) if payload.get("days_old") else None,
        "sort": payload.get("sort") if payload.get("sort") in ("date", "default") else "date",
        "max_pages": max(1, min(int(payload.get("max_pages") or 3), 100)),
    }

    import re as _re
    raw_name = "_".join(p for p in [query or "all_jobs", location] if p).lower()
    base_name = _re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_") or "hiringcafe_jobs"

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "starting",
        "params": params,
        "base_name": base_name,
        "pages_fetched": 0,
        "jobs_collected": 0,
        "total_count": None,
        "files": [],
        "preview": [],
        "validation": None,
        "error": None,
    }

    thread = threading.Thread(target=_run_scrape, args=(job_id, params), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job id"}), 404
    with JOBS_LOCK:
        return jsonify({
            "status": job["status"],
            "pages_fetched": job["pages_fetched"],
            "jobs_collected": job["jobs_collected"],
            "total_count": job["total_count"],
            "files": job["files"],
            "preview": job["preview"] if job["status"] == "done" else [],
            "validation": job["validation"] if job["status"] == "done" else None,
            "error": job["error"],
        })


@app.get("/api/download/<path:filename>")
def download(filename: str):
    # Restrict downloads to files inside the results directory.
    file_path = (RESULTS_DIR / filename).resolve()
    if not str(file_path).startswith(str(RESULTS_DIR.resolve())) or not file_path.is_file():
        return jsonify({"error": "File not found"}), 404
    return send_file(file_path, as_attachment=True)


if __name__ == "__main__":
    RESULTS_DIR.mkdir(exist_ok=True)
    # Port 5001: macOS AirPlay occupies 5000.
    app.run(host="127.0.0.1", port=5001, debug=False)
