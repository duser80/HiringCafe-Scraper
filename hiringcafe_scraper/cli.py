"""Command-line interface for the HiringCafe search scraper."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from .client import HiringCafeClient, HiringCafeError
from .exporter import export_jobs, export_validation_report
from .search import (
    COMMITMENT_TYPES,
    SENIORITY_LEVELS,
    WORKPLACE_TYPES,
    build_search_state,
    fetch_all_jobs,
)
from .validator import criteria_from_params, validate_jobs

logger = logging.getLogger("hiringcafe_scraper")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hiringcafe-scraper",
        description=(
            "Scrape structured job listings from HiringCafe via its Next.js "
            "SSR data route and export them to JSON / Excel / CSV."
        ),
    )
    parser.add_argument(
        "--query",
        default="",
        help='Keyword / job title search query, e.g. "software engineer". '
        "Empty means all jobs.",
    )
    parser.add_argument(
        "--location",
        default=None,
        help='Location name (country, state, or city), e.g. "India".',
    )
    parser.add_argument(
        "--workplace",
        nargs="+",
        choices=WORKPLACE_TYPES,
        default=None,
        help="Workplace type filter (one or more).",
    )
    parser.add_argument(
        "--commitment",
        nargs="+",
        choices=COMMITMENT_TYPES,
        default=None,
        help="Commitment type filter (one or more).",
    )
    parser.add_argument(
        "--seniority",
        nargs="+",
        choices=SENIORITY_LEVELS,
        default=None,
        help="Seniority level filter (one or more).",
    )
    parser.add_argument(
        "--days-old",
        type=int,
        default=None,
        help="Only include jobs HiringCafe fetched/re-verified in the past N days "
        "(freshness filter; the site default is 121).",
    )
    parser.add_argument(
        "--sort",
        choices=["date", "default"],
        default="date",
        help="Sort order: date (newest first) or default (relevance). Default: date.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Safety cap on the number of result pages fetched. Default: 50.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Base delay in seconds between requests. Default: 1.0.",
    )
    parser.add_argument(
        "--out",
        default="results",
        help="Output directory. Default: results/",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=["json", "xlsx", "csv"],
        default=["json", "xlsx"],
        help="Output formats. Default: json xlsx.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate scraped jobs against the search criteria and write a "
        "data-quality report ({name}_validation.json).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _base_name(query: str, location: str | None) -> str:
    parts = [query or "all_jobs"]
    if location:
        parts.append(location)
    name = "_".join(parts).lower()
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_") or "hiringcafe_jobs"


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        search_state = build_search_state(
            query=args.query,
            sort_by=args.sort,
            location=args.location,
            workplace_types=args.workplace,
            commitment_types=args.commitment,
            seniority_levels=args.seniority,
            days_old=args.days_old,
        )
    except HiringCafeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Search state: %s", search_state)

    client = HiringCafeClient(delay=args.delay)

    def on_progress(page: int, new_hits: int, total: int) -> None:
        logger.info("Page %d: +%d new jobs (total collected: %d)", page, new_hits, total)

    try:
        jobs = fetch_all_jobs(
            client,
            search_state,
            max_pages=args.max_pages,
            on_progress=on_progress,
        )
    except HiringCafeError as exc:
        logger.error("Scrape failed: %s", exc)
        return 1

    if not jobs:
        logger.warning("No jobs found for this search. Nothing to export.")
        return 0

    out_dir = Path(args.out)
    base_name = _base_name(args.query, args.location)

    validation = None
    if args.validate:
        criteria = criteria_from_params(
            query=args.query,
            workplace_types=args.workplace,
            commitment_types=args.commitment,
            seniority_levels=args.seniority,
            days_old=args.days_old,
        )
        validation = validate_jobs(jobs, criteria, search_state)
        logger.info(
            "Validation: %d/%d jobs (%.1f%%) exactly match all requested filters.",
            validation["exact_match_count"],
            validation["total_jobs"],
            validation["exact_match_rate"],
        )
        for name, check in validation["checks"].items():
            if not check["checked"]:
                continue
            total_checked = check["passed"] + check["failed"]
            rate = check["passed"] / total_checked * 100 if total_checked else 100.0
            logger.info("  %-16s %d/%d passed (%.1f%%)",
                        name + ":", check["passed"], total_checked, rate)
        hard_issue_jobs = [j for j in validation["job_issues"] if j["hard"]]
        for issue_job in hard_issue_jobs[:10]:
            logger.warning(
                "Mismatch: '%s' @ %s -> %s",
                issue_job["title"], issue_job["company"], "; ".join(issue_job["hard"]),
            )
        if len(hard_issue_jobs) > 10:
            logger.warning("...and %d more mismatched jobs (see the validation report).",
                           len(hard_issue_jobs) - 10)

    written = export_jobs(jobs, out_dir, base_name, args.formats, validation=validation)
    if validation is not None:
        written.append(
            export_validation_report(validation, out_dir / f"{base_name}_validation.json")
        )

    logger.info("Done. Collected %d unique jobs.", len(jobs))
    for path in written:
        logger.info("Output: %s", path.resolve())
    return 0


def main() -> None:
    sys.exit(run())
