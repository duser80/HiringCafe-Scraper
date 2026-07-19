"""Data-quality validation: verify scraped jobs actually match the search criteria.

Each job is checked against the filters that were requested (workplace type,
seniority, commitment, location, query relevance). Checks are classified as:

- "hard" mismatches: the job violates an explicit filter (e.g. you asked for
  Remote and the job is Onsite).
- "soft" warnings: not a filter violation, but worth flagging (e.g. the query
  terms don't appear anywhere in the job's title/summary - HiringCafe's search
  relevance can include adjacent matches).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

# Words too generic to demand in query-relevance matching.
STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or",
    "the", "to", "with", "jobs", "job",
}

_token_re = re.compile(r"[a-z0-9+#.]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _token_re.findall((text or "").lower()) if t not in STOPWORDS]


def _extract_location_targets(search_state: dict) -> dict | None:
    """Pull the requested location's names/codes out of the searchState."""
    locations = search_state.get("locations") or []
    if not locations:
        return None
    loc = locations[0]
    targets = {"level": (loc.get("types") or ["country"])[0],
               "country_code": "", "country_name": "",
               "state_name": "", "state_code": "", "city_name": "",
               "formatted": loc.get("formatted_address", "")}
    for comp in loc.get("address_components", []):
        ctypes = comp.get("types", [])
        if "country" in ctypes:
            targets["country_name"] = comp.get("long_name", "")
            targets["country_code"] = comp.get("short_name", "")
        elif "administrative_area_level_1" in ctypes:
            targets["state_name"] = comp.get("long_name", "")
            targets["state_code"] = comp.get("short_name", "")
        elif "locality" in ctypes:
            targets["city_name"] = comp.get("long_name", "")
    return targets


def _location_matches(v5: dict, targets: dict) -> bool:
    """Check a job's workplace fields against the requested location.

    HiringCafe field formats (observed):
      workplace_countries: ISO codes, e.g. ["US"]
      workplace_states:    "California, US"
      workplace_cities:    "Mountain View, California, US"
    """
    if v5.get("is_workplace_worldwide_ok"):
        return True

    countries = [c.lower() for c in (v5.get("workplace_countries") or [])]
    states = [s.lower() for s in (v5.get("workplace_states") or [])]
    cities = [c.lower() for c in (v5.get("workplace_cities") or [])]
    formatted = (v5.get("formatted_workplace_location") or "").lower()

    level = targets["level"]
    country_code = targets["country_code"].lower()
    country_name = targets["country_name"].lower()
    state_name = targets["state_name"].lower()
    city_name = targets["city_name"].lower()

    if level == "country":
        return (
            (country_code and country_code in countries)
            or (country_name and country_name in formatted)
        )

    if level == "administrative_area_level_1":
        return (
            any(state_name and s.startswith(state_name) for s in states)
            or (state_name and state_name in formatted)
            or any(state_name and state_name in c for c in cities)
        )

    # locality (city)
    return (
        any(city_name and c.startswith(city_name) for c in cities)
        or (city_name and city_name in formatted)
    )


def validate_job(hit: dict, criteria: dict, location_targets: dict | None) -> dict:
    """Validate one raw hit. Returns {'hard': [...], 'soft': [...]} issue lists."""
    v5 = hit.get("v5_processed_job_data") or {}
    hard: list[str] = []
    soft: list[str] = []

    # Workplace type
    requested_workplace = criteria.get("workplace_types") or []
    if requested_workplace:
        actual = v5.get("workplace_type") or ""
        if actual not in requested_workplace:
            hard.append(f"workplace_type is '{actual or 'unspecified'}', "
                        f"requested {'/'.join(requested_workplace)}")

    # Seniority
    requested_seniority = criteria.get("seniority_levels") or []
    if requested_seniority:
        actual = v5.get("seniority_level")
        if actual is None:
            soft.append("seniority_level not specified on the job")
        elif actual not in requested_seniority:
            hard.append(f"seniority_level is '{actual}', "
                        f"requested {'/'.join(requested_seniority)}")

    # Commitment
    requested_commitment = criteria.get("commitment_types") or []
    if requested_commitment:
        actual_list = v5.get("commitment") or []
        if not set(actual_list) & set(requested_commitment):
            hard.append(f"commitment is {actual_list or 'unspecified'}, "
                        f"requested {'/'.join(requested_commitment)}")

    # Location
    if location_targets:
        if not _location_matches(v5, location_targets):
            hard.append(
                f"location '{v5.get('formatted_workplace_location') or 'unspecified'}' "
                f"does not include requested '{location_targets['formatted']}'"
            )

    # Query relevance (soft: HiringCafe relevance can include adjacent roles)
    query = (criteria.get("query") or "").strip()
    if query:
        query_tokens = _tokens(query)
        if query_tokens:
            blob_parts = [
                hit.get("job_information", {}).get("title", ""),
                v5.get("core_job_title", ""),
                v5.get("requirements_summary", ""),
                v5.get("job_category", ""),
                " ".join(v5.get("technical_tools") or []),
                " ".join(v5.get("role_activities") or []),
            ]
            blob = " ".join(blob_parts).lower()
            missing = [t for t in query_tokens if t not in blob]
            if missing:
                soft.append(f"query terms not found in job text: {', '.join(missing)}")

    # Freshness (informational): --days-old filters on HiringCafe's fetch date,
    # which is not exposed per job; we surface stale *publish* dates as soft info.
    days_old = criteria.get("days_old")
    if days_old:
        published = v5.get("estimated_publish_date_millis")
        if published:
            age_days = (datetime.now(UTC)
                        - datetime.fromtimestamp(published / 1000, tz=UTC)).days
            if age_days > max(days_old, 121):
                soft.append(f"published {age_days} days ago (freshness filter is on "
                            "fetch date, not publish date)")

    return {"hard": hard, "soft": soft}


def validate_jobs(jobs: list[dict], criteria: dict, search_state: dict) -> dict:
    """Validate all jobs. Returns a summary report plus per-job issues.

    Report shape:
    {
      "total_jobs": int,
      "exact_match_count": int,        # jobs with zero hard issues
      "exact_match_rate": float,       # 0-100
      "checks": {check_name: {"checked": bool, "passed": int, "failed": int}},
      "job_issues": [{"index", "object_id", "title", "company", "hard", "soft"}],
    }
    """
    location_targets = _extract_location_targets(search_state)

    check_names = ["workplace", "seniority", "commitment", "location", "query_relevance"]
    active = {
        "workplace": bool(criteria.get("workplace_types")),
        "seniority": bool(criteria.get("seniority_levels")),
        "commitment": bool(criteria.get("commitment_types")),
        "location": location_targets is not None,
        "query_relevance": bool((criteria.get("query") or "").strip()),
    }
    checks = {
        name: {"checked": active[name], "passed": 0, "failed": 0}
        for name in check_names
    }
    prefix_to_check = {
        "workplace_type": "workplace",
        "seniority_level": "seniority",
        "commitment": "commitment",
        "location": "location",
        "query terms": "query_relevance",
    }

    job_issues: list[dict] = []
    exact_match_count = 0

    for index, hit in enumerate(jobs):
        result = validate_job(hit, criteria, location_targets)
        failed_checks: set[str] = set()
        for issue in result["hard"] + result["soft"]:
            for prefix, check in prefix_to_check.items():
                if issue.startswith(prefix):
                    failed_checks.add(check)
                    break

        for name in check_names:
            if not active[name]:
                continue
            if name in failed_checks:
                checks[name]["failed"] += 1
            else:
                checks[name]["passed"] += 1

        if not result["hard"]:
            exact_match_count += 1

        if result["hard"] or result["soft"]:
            v5 = hit.get("v5_processed_job_data") or {}
            job_issues.append({
                "index": index,
                "object_id": hit.get("objectID") or hit.get("id") or "",
                "title": hit.get("job_information", {}).get("title")
                or v5.get("core_job_title") or "",
                "company": v5.get("company_name") or "",
                "hard": result["hard"],
                "soft": result["soft"],
            })

    total = len(jobs)
    return {
        "total_jobs": total,
        "exact_match_count": exact_match_count,
        "exact_match_rate": round(exact_match_count / total * 100, 1) if total else 100.0,
        "checks": checks,
        "job_issues": job_issues,
    }


def criteria_from_params(
    query: str = "",
    workplace_types: list[str] | None = None,
    commitment_types: list[str] | None = None,
    seniority_levels: list[str] | None = None,
    days_old: int | None = None,
) -> dict:
    return {
        "query": query,
        "workplace_types": workplace_types or [],
        "commitment_types": commitment_types or [],
        "seniority_levels": seniority_levels or [],
        "days_old": days_old,
    }
