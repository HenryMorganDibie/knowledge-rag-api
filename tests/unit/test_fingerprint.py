"""Unit tests for S3 storage utilities."""

import pytest
from storage.s3_client import compute_fingerprint


def test_fingerprint_is_sha256():
    content = b"hello world"
    fp = compute_fingerprint(content)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_deterministic():
    content = b"same content every time"
    assert compute_fingerprint(content) == compute_fingerprint(content)


def test_fingerprint_changes_on_content_change():
    fp1 = compute_fingerprint(b"version one")
    fp2 = compute_fingerprint(b"version two")
    assert fp1 != fp2


def test_fingerprint_empty_bytes():
    fp = compute_fingerprint(b"")
    # SHA-256 of empty string is well-defined
    assert fp == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
