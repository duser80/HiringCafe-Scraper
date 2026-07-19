"""Search-slice configuration for ingestion.

Each slice is one HiringCafe search kept under the ~10k deep-pagination window.
Slices are date-sorted so incremental runs can stop early once they reach
already-known jobs.

Override via the SLICES_JSON env var, e.g.:

    SLICES_JSON='[{"name":"us-eng","search_state":{"searchQuery":"engineer","sortBy":"date"},"max_pages":40}]'
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchSlice:
    name: str
    search_state: dict = field(default_factory=dict)
    max_pages: int = 60


# Default: one broad newest-first slice. Each ingest cycle picks up everything
# published since the previous cycle (~10k+ jobs/day at a 3h interval), because
# the early-stop kicks in once a full page of known jobs is reached.
DEFAULT_SLICES = [
    SearchSlice(name="latest-all", search_state={"sortBy": "date"}, max_pages=60),
]


def load_slices(slices_json: str, default_max_pages: int) -> list[SearchSlice]:
    if not slices_json.strip():
        return [
            SearchSlice(
                name=s.name, search_state=s.search_state, max_pages=default_max_pages
            )
            for s in DEFAULT_SLICES
        ]

    try:
        raw = json.loads(slices_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SLICES_JSON is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ValueError("SLICES_JSON must be a non-empty JSON array.")

    slices: list[SearchSlice] = []
    for i, entry in enumerate(raw):
        name = entry.get("name")
        search_state = entry.get("search_state")
        if not name or not isinstance(search_state, dict):
            raise ValueError(
                f"SLICES_JSON entry {i} must have 'name' and object 'search_state'."
            )
        # Date sort is required for the incremental early-stop to be correct.
        search_state.setdefault("sortBy", "date")
        slices.append(
            SearchSlice(
                name=name,
                search_state=search_state,
                max_pages=int(entry.get("max_pages", default_max_pages)),
            )
        )
    logger.info("Loaded %d ingest slices: %s", len(slices), [s.name for s in slices])
    return slices
