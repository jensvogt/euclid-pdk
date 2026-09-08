"""The credentials cache, which euclid-cli and euclid-jdk read too."""

from __future__ import annotations

import base64
import json
import os
import stat
import time

from euclid import credentials


def make_token(expiry: float) -> str:
    """A JWT with just enough shape for the local expiry check - the signature is never verified
    here, because the client does not hold the server's secret."""
    def segment(payload: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'exp': int(expiry)})}.signature"


def test_round_trips_the_fields_the_cli_writes(isolated_credentials):
    saved = credentials.CachedCredentials(
        token="t", user_id="jens", account_id="000000000000", region="eu-central-1",
        access_key_id="AKIA1", secret_access_key="s", is_admin=True,
        base_url="https://euclid.example.com", namespace="development")
    credentials.save(saved)

    document = json.loads(isolated_credentials.read_text())
    # These names are a wire format shared with euclid-cli, not an internal choice.
    assert set(document) == {"token", "userId", "accountId", "region", "accessKeyId",
                             "secretAccessKey", "isAdmin", "baseUrl", "namespace"}
    assert document["namespace"] == "development"

    loaded = credentials.load()
    assert loaded is not None
    assert loaded.to_json() == saved.to_json()


def test_an_absent_namespace_is_written_as_an_empty_string(isolated_credentials):
    """Not null: the CLI reads this field as a string, and null is not one."""
    credentials.save(credentials.CachedCredentials(token="t", base_url="https://euclid.example.com"))
    assert json.loads(isolated_credentials.read_text())["namespace"] == ""


def test_the_file_is_readable_only_by_its_owner(isolated_credentials):
    credentials.save(credentials.CachedCredentials(token="t"))
    mode = stat.S_IMODE(os.stat(isolated_credentials).st_mode)
    assert mode == 0o600


def test_load_returns_none_when_there_is_nothing_usable(isolated_credentials):
    assert credentials.load() is None

    isolated_credentials.write_text("not json")
    assert credentials.load() is None

    isolated_credentials.write_text("[]")
    assert credentials.load() is None


def test_update_namespace_only_touches_the_matching_server(isolated_credentials):
    credentials.save(credentials.CachedCredentials(token="t", base_url="https://a.example.com",
                                                   namespace="one"))

    credentials.update_namespace("https://b.example.com", "two")
    assert credentials.load().namespace == "one"

    credentials.update_namespace("https://a.example.com", "two")
    assert credentials.load().namespace == "two"


def test_token_validity_is_read_from_the_expiry_claim():
    assert credentials.is_token_valid(make_token(time.time() + 3600))
    assert not credentials.is_token_valid(make_token(time.time() - 1))
    assert not credentials.is_token_valid("not-a-token")
    assert not credentials.is_token_valid("only.two")
    # A token with no exp claim is treated as unusable rather than as never expiring.
    payload = base64.urlsafe_b64encode(b'{"sub":"jens"}').decode().rstrip("=")
    assert not credentials.is_token_valid(f"h.{payload}.s")


def test_the_path_can_be_overridden_by_the_environment(isolated_credentials, monkeypatch, tmp_path):
    """How euclid hands an application its own credentials, and how these tests stay out of
    ``~/.euclid``."""
    elsewhere = tmp_path / "other"
    monkeypatch.setenv("EUCLID_CREDENTIALS_FILE", str(elsewhere))
    assert credentials.credentials_path() == elsewhere
