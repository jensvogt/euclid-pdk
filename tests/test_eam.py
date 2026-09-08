"""Login and EAM operations, end to end against a fake euclid server.

These are the tests that would catch a client that signs one thing and sends another: the gateway
verifies every signed request with the same rules euclid's ``HttpActionServer`` applies, so a
mismatch between the Host header a signature covers and the one that goes on the wire fails here
rather than in production.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from euclid import AUTH_BEARER, AUTH_SIGNATURE, Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid import credentials
from euclid.auth import SigningScheme

ACCESS_KEY_ID = "AKIAEXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"


def token(expires_in: int = 3600) -> str:
    def segment(payload: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'exp': int(time.time()) + expires_in})}.sig"


def login_response(the_token: str, *, with_key: bool = True) -> dict:
    return {
        "metadata": {"region": "eu-central-1", "accountId": "000000000000", "user": "jens"},
        "token": the_token,
        "accessKeyId": ACCESS_KEY_ID if with_key else "",
        "secretAccessKey": SECRET if with_key else "",
        "createdAt": "2026-09-08T10:00:00Z",
        "isAdmin": True,
    }


def prepared(gateway, *, with_key: bool = True, the_token: str | None = None):
    """A gateway that answers login and knows the credentials that login hands out."""
    the_token = the_token or token()
    gateway.answer("eam", "login", login_response(the_token, with_key=with_key))
    gateway.tokens[the_token] = "jens"
    if with_key:
        gateway.access_keys[ACCESS_KEY_ID] = (SECRET, "jens")
    return gateway


# -- login -----------------------------------------------------------------------------------


def test_login_sends_one_identifier_and_returns_a_session(gateway):
    prepared(gateway)

    session = Euclid.for_server(gateway.base_url).access().credentials("jens", "secret").login()

    assert gateway.last().json() == {"userId": "jens", "password": "secret", "email": ""}
    assert gateway.last().headers["x-euclid-target"] == "eam"
    assert gateway.last().headers["x-euclid-action"] == "login"
    assert (session.user_id, session.account_id, session.region) == ("jens", "000000000000", "eu-central-1")
    assert session.access_key_id == ACCESS_KEY_ID
    assert session.is_admin
    session.close()


def test_login_by_email_sends_the_email_instead(gateway):
    """The server resolves by user ID first, so sending both would silently ignore the email."""
    prepared(gateway)

    session = Euclid.for_server(gateway.base_url).access().email("jens@example.com").password("s").login()

    assert gateway.last().json() == {"userId": "", "password": "s", "email": "jens@example.com"}
    session.close()


def test_login_is_unauthenticated(gateway):
    """It has to be: it is where the credentials come from."""
    prepared(gateway)

    Euclid.for_server(gateway.base_url).login("jens", "secret").close()

    assert "authorization" not in gateway.last().headers
    assert "signature" not in gateway.last().headers


def test_a_refused_login_raises_with_the_server_reason(gateway):
    gateway.answer("eam", "login", {"error": "Invalid password"}, status=401)

    with pytest.raises(EuclidAuthenticationError) as raised:
        Euclid.for_server(gateway.base_url).login("jens", "wrong")

    assert raised.value.status == 401
    assert raised.value.reason == "Invalid password"


def test_login_without_credentials_says_so_before_any_request(gateway):
    with pytest.raises(ValueError, match="username or email"):
        Euclid.for_server(gateway.base_url).access().login()
    with pytest.raises(ValueError, match="password"):
        Euclid.for_server(gateway.base_url).access().username("jens").login()
    assert gateway.requests == []


# -- the credentials cache -------------------------------------------------------------------


def test_a_login_is_cached_and_reused(gateway, isolated_credentials):
    prepared(gateway)
    server = Euclid.for_server(gateway.base_url)

    server.access().credentials("jens", "secret").login().close()
    assert len(gateway.requests) == 1

    # No second login request: the cached token is still valid, so there is nothing to ask.
    second = server.access().login()
    assert len(gateway.requests) == 1
    assert second.user_id == "jens"
    assert second.access_key_id == ACCESS_KEY_ID
    second.close()

    assert json.loads(isolated_credentials.read_text())["baseUrl"] == gateway.base_url


def test_an_expired_cached_token_is_not_reused(gateway):
    prepared(gateway, the_token=token(expires_in=3600))
    server = Euclid.for_server(gateway.base_url)
    server.access().credentials("jens", "secret").login().close()

    credentials.save(credentials.CachedCredentials(token=token(expires_in=-10),
                                                   base_url=gateway.base_url))

    server.access().credentials("jens", "secret").login().close()
    assert len(gateway.requests) == 2


def test_a_cache_for_another_server_is_not_reused(gateway):
    prepared(gateway)
    credentials.save(credentials.CachedCredentials(token=token(), base_url="https://elsewhere.example.com"))

    Euclid.for_server(gateway.base_url).login("jens", "secret").close()
    assert len(gateway.requests) == 1


def test_use_cache_false_neither_reads_nor_writes(gateway, isolated_credentials):
    prepared(gateway)
    server = Euclid.for_server(gateway.base_url)

    server.access().credentials("jens", "secret").use_cache(False).login().close()
    assert not isolated_credentials.exists()

    server.access().credentials("jens", "secret").use_cache(False).login().close()
    assert len(gateway.requests) == 2


# -- authentication of session calls ----------------------------------------------------------


def test_session_calls_are_signed_with_sigv4_by_default(gateway):
    prepared(gateway)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        session.list_users()

    request = gateway.last()
    assert request.auth == "sigv4"
    assert request.subject == "jens"
    assert "authorization" in request.headers and request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")


def test_session_calls_can_be_signed_with_rfc_9421(gateway):
    prepared(gateway)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    session = (Euclid.for_server(gateway.base_url).access().credentials("jens", "secret")
               .signing_scheme(SigningScheme.RFC9421).login())
    with session:
        session.list_users()

    request = gateway.last()
    assert request.auth == "rfc9421"
    assert request.subject == "jens"
    # The two schemes do not collide: this one leaves Authorization alone.
    assert "authorization" not in request.headers
    assert "signature-input" in request.headers and "content-digest" in request.headers


def test_a_session_without_an_access_key_falls_back_to_the_bearer_token(gateway):
    """A login that returns no key still has to be able to make calls."""
    prepared(gateway, with_key=False)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        session.list_users()

    assert gateway.last().auth == "bearer"


def test_bearer_can_be_asked_for_even_when_a_key_is_available(gateway):
    prepared(gateway)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    session = Euclid.for_server(gateway.base_url).login("jens", "secret", auth=AUTH_BEARER)
    with session:
        session.list_users()

    assert gateway.last().auth == "bearer"


def test_asking_for_signatures_without_a_key_fails_loudly(gateway):
    """Rather than quietly sending a token, which is not what the caller asked for."""
    prepared(gateway, with_key=False)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    session = Euclid.for_server(gateway.base_url).login("jens", "secret", auth=AUTH_SIGNATURE)
    with session, pytest.raises(ValueError, match="no access key"):
        session.list_users()


def test_a_tampered_body_is_rejected_by_the_server(gateway):
    """Proof the signature covers the body: re-sending a signed request with a different body
    must not authenticate, or none of the rest of this means anything."""
    prepared(gateway)
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        session.list_users()
    signed = gateway.last()

    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(gateway.base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    body = b'{"prefix":"pwned","pageSize":10,"pageIndex":0,"sortColumn":"userId","sortDirection":"asc"}'
    headers = {name: value for name, value in signed.headers.items() if name != "content-length"}
    connection.request("POST", "/", body=body, headers=headers)
    assert connection.getresponse().status == 403
    connection.close()


# -- operations ------------------------------------------------------------------------------


def test_list_users_parses_the_response(gateway):
    prepared(gateway)
    gateway.answer("eam", "list-users", {"total": 2, "users": [
        {"userId": "jens", "ern": "ern:eam:user/jens", "email": "jens@example.com",
         "accountId": "000000000000", "region": "eu-central-1", "created": "2026-01-01",
         "accountGrants": [{"accountId": "000000000000", "namespaces": ["development"],
                            "isAdmin": True, "granted": "2026-01-01"}]},
        {"userId": "alice"},
    ]})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        result = session.list_users(prefix="j", page_size=25, page_index=1, sort_direction="desc")

    assert gateway.last().json() == {"prefix": "j", "pageSize": 25, "pageIndex": 1,
                                     "sortColumn": "userId", "sortDirection": "desc"}
    assert result.total == 2
    assert [user.user_id for user in result.users] == ["jens", "alice"]
    assert result.users[0].account_grants[0].namespaces == ["development"]
    assert result.users[0].account_grants[0].is_admin
    # A field the server did not send reads as empty rather than raising.
    assert result.users[1].email == ""
    assert result.users[1].account_grants == []


def test_accounts_groups_and_namespaces_round_trip(gateway):
    prepared(gateway)
    gateway.answer("eam", "create-account", {"account": {"accountId": "111", "name": "acme",
                                                         "ern": "ern:eam:account/111"}})
    gateway.answer("eam", "create-namespace", {"namespace": {"accountId": "111", "name": "prod"}})
    gateway.answer("eam", "create-user-group", {"userGroup": {"name": "ops", "userIds": ["jens"]}})
    gateway.answer("eam", "list-accounts", {"accounts": [{"accountId": "111"}], "total": 1})
    gateway.answer("eam", "list-namespaces", {"namespaces": [{"name": "prod"}], "total": 1})
    gateway.answer("eam", "list-user-groups", {"userGroups": [{"name": "ops"}], "total": 1})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        assert session.create_account("111", "acme", "an account").account_id == "111"
        assert session.create_namespace("111", "prod").name == "prod"
        assert session.create_user_group("ops", "operations").user_ids == ["jens"]
        assert session.list_accounts().total == 1
        assert session.list_namespaces("111").namespaces[0].name == "prod"
        assert session.list_user_groups().user_groups[0].name == "ops"


def test_access_keys(gateway):
    prepared(gateway)
    gateway.answer("eam", "create-access-key", {"accessKeyId": "AKIANEW", "secretAccessKey": "s3cret",
                                                "createdAt": "2026-09-08"})
    gateway.answer("eam", "list-access-keys", {"accessKeys": [{"accessKeyId": "AKIANEW", "active": True}]})
    gateway.answer("eam", "delete-access-key", {})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        created = session.create_access_key()
        assert (created.access_key_id, created.secret_access_key) == ("AKIANEW", "s3cret")
        assert [key.access_key_id for key in session.list_access_keys()] == ["AKIANEW"]
        session.delete_access_key("AKIANEW")

    assert gateway.last().json() == {"accessKeyId": "AKIANEW"}


def test_the_actions_that_return_nothing_still_check_the_status(gateway):
    prepared(gateway)
    gateway.answer("eam", "delete-user", {"error": "User not found"}, status=404)

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        with pytest.raises(EuclidServiceError) as raised:
            session.delete_user("nobody")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("eam", "delete-user", 404)
    assert raised.value.reason == "User not found"


# -- namespace scoping -------------------------------------------------------------------------


def test_change_namespace_scopes_later_calls_and_updates_the_cache(gateway, isolated_credentials):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("eam", "list-users", {"users": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        assert "x-euclid-namespace" not in gateway.last().headers

        session.change_namespace("development")
        session.list_users()

    assert gateway.last().headers["x-euclid-namespace"] == "development"
    assert json.loads(isolated_credentials.read_text())["namespace"] == "development"


def test_a_namespace_asked_for_at_login_is_applied_to_a_cached_session(gateway):
    """A cached session may predate the namespace being asked for, or be scoped to another."""
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    server = Euclid.for_server(gateway.base_url)

    server.access().credentials("jens", "secret").login().close()
    session = server.access().namespace("production").login()

    assert session.namespace == "production"
    assert gateway.last().action == "change-namespace"
    assert gateway.last().json() == {"namespace": "production"}
    session.close()


# -- credential refresh ------------------------------------------------------------------------


def test_an_expired_token_is_retried_once_with_a_fresh_one(gateway):
    """A long-lived process holds credentials that do not: one round trip turns a 401 that says
    "expired" into the answer the request would have got a moment earlier."""
    stale = token()
    prepared(gateway, with_key=False, the_token=stale)

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        gateway.answer("eam", "list-users", {"users": [], "total": 0})

        # The server forgets the token the session is holding, then a rotation hands out a new one.
        # A different lifetime, so the two tokens are distinguishable - two calls to token() in the
        # same second produce the same string, and the test would then pass on the first attempt.
        del gateway.tokens[stale]
        refreshed = token(expires_in=7200)
        gateway.tokens[refreshed] = "jens"

        # Stands in for a credentials file that gets rewritten between the two attempts, which is
        # what euclid actually does to an application's token.
        rotating = iter([stale, refreshed])
        session.token_provider = lambda: next(rotating)

        assert session.list_users().total == 0

    # Two attempts for the one call: the rejected one, then the one that carried the new token.
    list_attempts = [r for r in gateway.requests if r.action == "list-users"]
    assert len(list_attempts) == 2
    assert list_attempts[0].auth == ""
    assert list_attempts[1].auth == "bearer"


def test_a_rejection_that_is_not_an_expiry_is_not_retried(gateway):
    """Narrow on purpose: a wrong password or a missing permission is answered once, as before."""
    prepared(gateway)
    gateway.answer("eam", "list-users", {"error": "Not authorized for this namespace"}, status=401)

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        with pytest.raises(EuclidServiceError):
            session.list_users()

    assert len([r for r in gateway.requests if r.action == "list-users"]) == 1


# -- escape hatch ------------------------------------------------------------------------------


def test_call_reaches_an_action_this_sdk_does_not_wrap(gateway):
    prepared(gateway)
    gateway.answer("eam", "some-future-action", {"ok": True})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        assert session.call("some-future-action", {"x": 1}) == {"ok": True}

    assert gateway.last().json() == {"x": 1}
    assert gateway.last().auth == "sigv4"
