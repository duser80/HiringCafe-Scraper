"""Unit tests: slice loading and hit mapping."""

from __future__ import annotations

import pytest

from service.worker.ingest import map_hit
from service.worker.slices import DEFAULT_SLICES, load_slices


class TestSlices:
    def test_default_when_empty(self):
        assert load_slices("", 60) == DEFAULT_SLICES

    def test_custom_slices(self):
        raw = '[{"name":"eng","search_state":{"searchQuery":"engineer"},"max_pages":10}]'
        slices = load_slices(raw, 60)
        assert len(slices) == 1
        assert slices[0].name == "eng"
        assert slices[0].max_pages == 10
        # sortBy: date is forced for incremental correctness
        assert slices[0].search_state["sortBy"] == "date"

    def test_invalid_json_rejected(self):
        with pytest.raises(ValueError):
            load_slices("{not json", 60)

    def test_missing_name_rejected(self):
        with pytest.raises(ValueError):
            load_slices('[{"search_state":{}}]', 60)


class TestMapHit:
    def test_full_hit(self):
        hit = {
            "objectID": "greenhouse___acme___123",
            "apply_url": "https://acme.example/jobs/123",
            "source": "greenhouse",
            "board_token": "acme",
            "job_information": {"title": "Platform Engineer"},
            "v5_processed_job_data": {
                "company_name": "Acme",
                "formatted_workplace_location": "Austin, Texas, United States",
                "workplace_countries": ["US"],
                "workplace_states": ["Texas, US"],
                "workplace_cities": ["Austin, Texas, US"],
                "workplace_type": "Hybrid",
                "seniority_level": "Mid Level",
                "commitment": ["Full Time"],
                "job_category": "Engineering",
                "yearly_min_compensation": 120000,
                "yearly_max_compensation": 160000,
                "listed_compensation_currency": "USD",
                "listed_compensation_frequency": "Yearly",
                "min_industry_and_role_yoe": 3,
                "technical_tools": ["Kubernetes"],
                "estimated_publish_date": "2026-07-14T07:49:30.102Z",
            },
        }
        values = map_hit(hit)
        assert values is not None
        assert values["object_id"] == "greenhouse___acme___123"
        assert values["title"] == "Platform Engineer"
        assert values["company"] == "Acme"
        assert values["country"] == "US"
        assert values["workplace_type"] == "Hybrid"
        assert values["salary_min_yearly"] == 120000
        assert values["published_at"].year == 2026
        assert values["raw"] == hit

    def test_missing_object_id_returns_none(self):
        assert map_hit({"job_information": {"title": "X"}}) is None

    def test_missing_title_returns_none(self):
        assert map_hit({"objectID": "a___b___c", "v5_processed_job_data": {}}) is None

    def test_bad_publish_date_falls_back_to_now(self):
        hit = {
            "objectID": "x___y___z",
            "job_information": {"title": "T"},
            "v5_processed_job_data": {"estimated_publish_date": "not-a-date"},
        }
        values = map_hit(hit)
        assert values is not None
        assert values["published_at"] is not None

    def test_nul_characters_stripped(self):
        # Postgres rejects \u0000 in TEXT/JSONB; upstream data sometimes has it.
        hit = {
            "objectID": "n___u___l",
            "job_information": {"title": "Eng\x00ineer"},
            "v5_processed_job_data": {
                "company_name": "Acme\x00Corp",
                "technical_tools": ["Py\x00thon"],
            },
        }
        values = map_hit(hit)
        assert values is not None
        assert values["title"] == "Engineer"
        assert values["company"] == "AcmeCorp"
        assert values["technical_tools"] == ["Python"]
        assert "\x00" not in str(values["raw"])
