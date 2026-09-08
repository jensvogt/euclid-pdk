"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fake_gateway import FakeGateway  # noqa: E402  (needs the path above)


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Points the credentials cache at a temporary file, for every test without exception.

    Autouse rather than opt-in because logging in writes the cache as a side effect: a test that
    forgot this fixture would overwrite the credentials of whoever ran the suite, and would only
    do so on the machine of the person least expecting it.
    """
    path = tmp_path / "credentials"
    monkeypatch.setenv("EUCLID_CREDENTIALS_FILE", str(path))
    return path


@pytest.fixture
def gateway():
    """A running fake euclid server, shut down when the test ends."""
    with FakeGateway() as running:
        yield running
