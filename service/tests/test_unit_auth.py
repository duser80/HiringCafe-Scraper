"""Unit tests: API key generation/hashing and cursor encoding."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from service.app.auth import KEY_PREFIX, generate_api_key, hash_api_key
from service.app.errors import ApiError
from service.app.repositories.jobs_repo import decode_cursor, encode_cursor


class TestApiKeys:
    def test_key_format(self):
        key = generate_api_key()
        assert key.startswith(KEY_PREFIX)
        assert len(key) == len(KEY_PREFIX) + 43
        assert key[len(KEY_PREFIX):].isalnum()

    def test_keys_are_unique(self):
        keys = {generate_api_key() for _ in range(200)}
        assert len(keys) == 200

    def test_hash_is_deterministic_and_hex(self):
        key = generate_api_key()
        h1, h2 = hash_api_key(key), hash_api_key(key)
        assert h1 == h2
        assert len(h1) == 64
        int(h1, 16)  # valid hex

    def test_different_keys_different_hashes(self):
        assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())


class TestCursor:
    def test_roundtrip(self):
        published = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
        job_id = uuid.uuid4()
        cursor = encode_cursor(published, job_id)
        decoded_published, decoded_id = decode_cursor(cursor)
        assert decoded_published == published
        assert decoded_id == job_id

    def test_cursor_is_urlsafe(self):
        cursor = encode_cursor(datetime.now(UTC), uuid.uuid4())
        assert all(c.isalnum() or c in "-_=" for c in cursor)

    @pytest.mark.parametrize("bad", ["garbage", "aGVsbG8=", "", "a|b|c"])
    def test_malformed_cursor_raises_api_error(self, bad):
        with pytest.raises(ApiError) as exc_info:
            decode_cursor(bad)
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "invalid_cursor"
