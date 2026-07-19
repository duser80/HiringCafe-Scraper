"""searchState construction and paginated fetching for HiringCafe searches."""

from __future__ import annotations

import logging
from collections.abc import Callable

import requests

from .client import HiringCafeClient, HiringCafeError

logger = logging.getLogger(__name__)

# HiringCafe's search index (Elasticsearch-style) only serves roughly the first
# 10k results of any query via pagination. Beyond that, pages come back empty.
DEEP_PAGINATION_WINDOW = 10_000

WORKPLACE_TYPES = ["Remote", "Hybrid", "Onsite"]
COMMITMENT_TYPES = [
    "Full Time",
    "Part Time",
    "Contract",
    "Internship",
    "Temporary",
    "Seasonal",
    "Volunteer",
]
SENIORITY_LEVELS = [
    "No Prior Experience Required",
    "Entry Level",
    "Mid Level",
    "Senior Level",
]


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def build_location(name: str) -> dict:
    """Resolve a plain location name into HiringCafe's location object shape.

    HiringCafe's search backend requires Google-Places-style address components
    with ISO short codes (e.g. short_name "US" for United States); a bare
    formatted_address matches nothing. We geocode the name with Nominatim
    (OpenStreetMap, free, no API key) and construct the object from its result.
    """
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": name,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 1,
                "accept-language": "en",
            },
            headers={"User-Agent": "HiringCafeScraper/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HiringCafeError(f"Geocoding failed for location '{name}': {exc}") from exc

    if not results:
        raise HiringCafeError(
            f"Could not geocode location '{name}'. Try a simpler name, "
            'e.g. "United States", "California", or "Chicago".'
        )

    result = results[0]
    address = result.get("address", {})
    address_type = result.get("addresstype", "")

    country = address.get("country", "")
    country_code = (address.get("country_code") or "").upper()
    state = address.get("state", "")
    # e.g. "US-CA" -> "CA"; fall back to the full state name
    state_code = (address.get("ISO3166-2-lvl4") or "").split("-")[-1] or state
    city = address.get("city") or address.get("town") or address.get("village") or ""

    components: list[dict] = []
    formatted_parts: list[str] = []

    if address_type == "country":
        types = ["country"]
    elif address_type in ("state", "province", "region"):
        types = ["administrative_area_level_1"]
        components.append(
            {"long_name": state or name, "short_name": state_code or name,
             "types": ["administrative_area_level_1"]}
        )
        formatted_parts.append(state or name)
    else:
        # Treat everything else (city, town, county, ...) as a locality.
        types = ["locality"]
        locality = city or name
        components.append(
            {"long_name": locality, "short_name": locality, "types": ["locality"]}
        )
        formatted_parts.append(locality)
        if state:
            components.append(
                {"long_name": state, "short_name": state_code,
                 "types": ["administrative_area_level_1"]}
            )
            formatted_parts.append(state_code or state)

    if country:
        components.append(
            {"long_name": country, "short_name": country_code, "types": ["country"]}
        )
        formatted_parts.append(country)

    formatted_address = ", ".join(formatted_parts) if formatted_parts else name

    location = {
        "formatted_address": formatted_address,
        "types": types,
        "id": "user_defined",
        "address_components": components,
        "options": {},
    }

    lat, lon = result.get("lat"), result.get("lon")
    if lat and lon:
        location["geometry"] = {"location": {"lat": float(lat), "lon": float(lon)}}

    logger.info("Geocoded location '%s' -> %s (%s)", name, formatted_address, types[0])
    return location


def build_search_state(
    query: str = "",
    sort_by: str = "date",
    location: str | None = None,
    workplace_types: list[str] | None = None,
    commitment_types: list[str] | None = None,
    seniority_levels: list[str] | None = None,
    days_old: int | None = None,
) -> dict:
    """Build the searchState dict accepted by HiringCafe's data route.

    Only non-default filters are included, matching how the site itself
    serializes searchState into its URLs.
    """
    search_state: dict = {}

    if query:
        search_state["searchQuery"] = query
    if sort_by and sort_by != "default":
        search_state["sortBy"] = sort_by
    if location:
        search_state["locations"] = [build_location(location)]
    if workplace_types:
        search_state["workplaceTypes"] = workplace_types
    if commitment_types:
        search_state["commitmentTypes"] = commitment_types
    if seniority_levels:
        search_state["seniorityLevel"] = seniority_levels
    if days_old is not None:
        search_state["dateFetchedPastNDays"] = days_old

    return search_state


def fetch_all_jobs(
    client: HiringCafeClient,
    search_state: dict,
    max_pages: int = 50,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """Fetch all pages for a search, deduplicating hits on objectID.

    ``on_progress(page, new_hits, total_collected)`` is invoked after each page.
    Returns the list of unique raw hit records.
    """
    jobs: list[dict] = []
    seen_ids: set[str] = set()
    total_count: int | None = None

    for page in range(max_pages):
        page_props = client.search_page(search_state, page)

        ssr_error = page_props.get("ssrError")
        if ssr_error:
            logger.warning("HiringCafe reported ssrError on page %d: %s", page, ssr_error)

        hits = page_props.get("ssrHits") or []

        if total_count is None:
            total_count = page_props.get("ssrTotalCount") or 0
            logger.info("Total matching jobs reported by HiringCafe: %s", f"{total_count:,}")
            if total_count > DEEP_PAGINATION_WINDOW:
                logger.warning(
                    "This search matches %s jobs, but HiringCafe's pagination only "
                    "reaches roughly the first %s results. Narrow the search "
                    "(e.g. --days-old, --location, more specific --query) and run "
                    "multiple slices for full coverage.",
                    f"{total_count:,}",
                    f"{DEEP_PAGINATION_WINDOW:,}",
                )

        new_hits = 0
        for hit in hits:
            object_id = hit.get("objectID") or hit.get("id")
            if object_id and object_id in seen_ids:
                continue
            if object_id:
                seen_ids.add(object_id)
            jobs.append(hit)
            new_hits += 1

        if on_progress:
            on_progress(page, new_hits, len(jobs))

        is_last_page = page_props.get("ssrIsLastPage", False)
        if is_last_page or not hits:
            logger.info("Reached last page (%d).", page)
            break
    else:
        logger.info("Stopped at --max-pages limit (%d pages).", max_pages)

    return jobs
