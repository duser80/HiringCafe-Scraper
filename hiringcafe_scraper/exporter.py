"""Export scraped HiringCafe jobs to raw JSON and flattened Excel/CSV tables."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

EXCEL_SHEET_CHUNK_SIZE = 100_000

BASE_URL = "https://hiringcafe.com"

FLAT_COLUMNS = [
    "title",
    "company",
    "location",
    "workplace_type",
    "commitment",
    "seniority_level",
    "job_category",
    "role_type",
    "min_yoe",
    "salary_min_yearly",
    "salary_max_yearly",
    "salary_min_hourly",
    "salary_max_hourly",
    "salary_currency",
    "salary_frequency",
    "compensation_transparent",
    "technical_tools",
    "requirements_summary",
    "visa_sponsorship",
    "published_at",
    "source_ats",
    "board_token",
    "apply_url",
    "job_page_url",
    "company_website",
    "company_industry",
    "company_size",
    "object_id",
]

_slug_strip_re = re.compile(r"[^a-z0-9]+")


def _slugify(*parts: str | None) -> str:
    """Best-effort recreation of HiringCafe's /job/ URL slug segments."""
    text = " ".join(p for p in parts if p)
    return _slug_strip_re.sub("-", text.lower()).strip("-")


def flatten_hit(hit: dict) -> dict:
    """Flatten one raw hit record into a spreadsheet-friendly row."""
    v5 = hit.get("v5_processed_job_data") or {}
    job_info = hit.get("job_information") or {}
    company = hit.get("enriched_company_data") or {}

    title = job_info.get("title") or v5.get("core_job_title") or ""
    company_name = v5.get("company_name") or company.get("name") or ""
    location = v5.get("formatted_workplace_location") or ""

    requisition_id = hit.get("requisition_id") or ""
    job_page_url = ""
    if requisition_id:
        slug = _slugify(title, company_name, location)
        job_page_url = f"{BASE_URL}/job/{slug}-{requisition_id}" if slug else ""

    commitment = v5.get("commitment") or []
    tools = v5.get("technical_tools") or []

    return {
        "title": title,
        "company": company_name,
        "location": location,
        "workplace_type": v5.get("workplace_type") or "",
        "commitment": ", ".join(commitment) if isinstance(commitment, list) else commitment,
        "seniority_level": v5.get("seniority_level") or "",
        "job_category": v5.get("job_category") or "",
        "role_type": v5.get("role_type") or "",
        "min_yoe": v5.get("min_industry_and_role_yoe"),
        "salary_min_yearly": v5.get("yearly_min_compensation"),
        "salary_max_yearly": v5.get("yearly_max_compensation"),
        "salary_min_hourly": v5.get("hourly_min_compensation"),
        "salary_max_hourly": v5.get("hourly_max_compensation"),
        "salary_currency": v5.get("listed_compensation_currency") or "",
        "salary_frequency": v5.get("listed_compensation_frequency") or "",
        "compensation_transparent": v5.get("is_compensation_transparent"),
        "technical_tools": ", ".join(tools) if isinstance(tools, list) else tools,
        "requirements_summary": v5.get("requirements_summary") or "",
        "visa_sponsorship": v5.get("visa_sponsorship"),
        "published_at": v5.get("estimated_publish_date") or "",
        "source_ats": hit.get("source") or "",
        "board_token": hit.get("board_token") or "",
        "apply_url": hit.get("apply_url") or "",
        "job_page_url": job_page_url,
        "company_website": v5.get("company_website") or company.get("homepage_uri") or "",
        "company_industry": v5.get("company_sector_and_industry") or "",
        "company_size": company.get("nb_employees"),
        "object_id": hit.get("objectID") or hit.get("id") or "",
    }


def build_dataframe(jobs: list[dict]) -> pd.DataFrame:
    rows = [flatten_hit(hit) for hit in jobs]
    return pd.DataFrame(rows, columns=FLAT_COLUMNS)


def export_raw_json(jobs: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(jobs, fh, ensure_ascii=False, indent=2)
    logger.info("Wrote %d raw job records to %s", len(jobs), path)
    return path


def export_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("Wrote %d rows to %s", len(df), path)
    return path


def export_excel(df: pd.DataFrame, path: Path) -> Path:
    """Write the flattened table to Excel, chunking into multiple sheets when large.

    Falls back to CSV alongside the intended path if the Excel write fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # openpyxl rejects illegal control characters; scrub string cells first.
    illegal_re = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    cleaned = df.copy()
    for column in cleaned.columns:
        if cleaned[column].dtype == object:
            cleaned[column] = cleaned[column].map(
                lambda v: illegal_re.sub("", v) if isinstance(v, str) else v
            )

    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            if len(cleaned) <= EXCEL_SHEET_CHUNK_SIZE:
                cleaned.to_excel(writer, sheet_name="jobs", index=False)
            else:
                for i, start in enumerate(range(0, len(cleaned), EXCEL_SHEET_CHUNK_SIZE), 1):
                    chunk = cleaned.iloc[start : start + EXCEL_SHEET_CHUNK_SIZE]
                    chunk.to_excel(writer, sheet_name=f"jobs_{i}", index=False)
        logger.info("Wrote %d rows to %s", len(cleaned), path)
        return path
    except Exception as exc:  # pragma: no cover - environment-dependent
        fallback = path.with_suffix(".csv")
        logger.warning("Excel export failed (%s); falling back to CSV at %s", exc, fallback)
        return export_csv(df, fallback)


def attach_validation_columns(df: pd.DataFrame, validation: dict) -> pd.DataFrame:
    """Add filter_match / validation_issues columns from a validation report."""
    issues_by_index = {j["index"]: j for j in validation.get("job_issues", [])}

    match_flags: list[bool] = []
    issue_texts: list[str] = []
    for i in range(len(df)):
        job_issue = issues_by_index.get(i)
        hard = job_issue["hard"] if job_issue else []
        soft = job_issue["soft"] if job_issue else []
        match_flags.append(not hard)
        parts = [f"[MISMATCH] {t}" for t in hard] + [f"[warn] {t}" for t in soft]
        issue_texts.append(" | ".join(parts))

    df = df.copy()
    df["filter_match"] = match_flags
    df["validation_issues"] = issue_texts
    return df


def export_validation_report(validation: dict, path: Path) -> Path:
    """Write the full validation report (summary + per-job issues) as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(validation, fh, ensure_ascii=False, indent=2)
    logger.info(
        "Wrote validation report (%.1f%% exact match) to %s",
        validation.get("exact_match_rate", 0.0), path,
    )
    return path


def export_jobs(
    jobs: list[dict],
    out_dir: Path,
    base_name: str,
    formats: list[str],
    validation: dict | None = None,
) -> list[Path]:
    """Export jobs in the requested formats. Returns the list of files written."""
    written: list[Path] = []

    if "json" in formats:
        written.append(export_raw_json(jobs, out_dir / f"{base_name}_raw.json"))

    needs_table = "xlsx" in formats or "csv" in formats
    if needs_table:
        df = build_dataframe(jobs)
        if validation is not None:
            df = attach_validation_columns(df, validation)
        if "xlsx" in formats:
            written.append(export_excel(df, out_dir / f"{base_name}.xlsx"))
        if "csv" in formats:
            written.append(export_csv(df, out_dir / f"{base_name}.csv"))

    return written
