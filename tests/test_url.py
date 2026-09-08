"""The Host header and @authority a client signs over."""

from __future__ import annotations

import pytest

from euclid.url import authority_of, host_header_of, scheme_of, strip_trailing_slash


@pytest.mark.parametrize("url, expected", [
    ("https://euclid.example.com", "https://euclid.example.com"),
    ("https://euclid.example.com/", "https://euclid.example.com"),
    ("http://localhost:8080/", "http://localhost:8080"),
])
def test_strip_trailing_slash(url: str, expected: str):
    assert strip_trailing_slash(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://euclid.example.com", "https"),
    ("HTTP://localhost:8080", "http"),
    ("euclid.example.com", "https"),
])
def test_scheme_of(url: str, expected: str):
    assert scheme_of(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://Euclid.Example.com", "euclid.example.com"),
    # A port written out stays in the Host header verbatim, default or not: the header has to be
    # what goes on the wire, and the wire is what the signature covers.
    ("https://euclid.example.com:443", "euclid.example.com:443"),
    ("http://localhost:8080/x", "localhost:8080"),
    ("https://user:pass@euclid.example.com", "euclid.example.com"),
])
def test_host_header_of(url: str, expected: str):
    assert host_header_of(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://euclid.example.com", "euclid.example.com"),
    # The default port is dropped from @authority even though it stays in Host - RFC 9421 §2.2.3.
    ("https://euclid.example.com:443", "euclid.example.com"),
    ("http://localhost:80", "localhost"),
    ("http://localhost:8080", "localhost:8080"),
])
def test_authority_of(url: str, expected: str):
    assert authority_of(url) == expected
