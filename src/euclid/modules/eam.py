"""EAM - euclid's access management module: login, users, groups, accounts, namespaces, keys.

Two objects. :class:`EuclidEam` is the login builder: it collects the server, the credentials and
the options, and hands back a session. :class:`EuclidSession` is that session - the authenticated
client every other call goes through, and the thing that holds the token and the access key the
login produced.

The split exists because logging in and being logged in need different arguments. A builder that
also carried the operations would let a caller write ``.list_users()`` on an object that has not
authenticated yet, and a session that also carried the login options would have a namespace field
that means one thing before login and another after.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..auth import SignableRequest, SigningScheme
from ..credentials import CachedCredentials, is_token_valid
from ..credentials import load as load_credentials
from ..credentials import save as save_credentials
from ..credentials import update_namespace as update_cached_namespace
from ..dto.eam import (AccessKey, Account, CreateAccessKeyResult, ListAccountsResult, ListNamespacesResult,
                       ListUserGroupsResult, ListUsersResult, LoginResult, Namespace, User, UserGroup)
from ..exceptions import EuclidAuthenticationError, EuclidServiceError
from ..http.client import DEFAULT_CA_CERT_PATH, DEFAULT_TIMEOUT, EuclidHttpClient
from ..url import authority_of, host_header_of, scheme_of, strip_trailing_slash

if TYPE_CHECKING:  # pragma: no cover - the runtime imports are inside the methods that need them
    from .ekm import EuclidEkm
    from .ens import EuclidEns
    from .eqs import EuclidEqs
    from .esm import EuclidEsm
    from .ess import EuclidEss

__all__ = ["EuclidEam", "EuclidSession", "TARGET"]

TARGET = "eam"

#: Authenticate with a signature when there is an access key to sign with, and with the bearer
#: token otherwise. euclid accepts either for every action (``HttpActionServer::Authenticate``).
AUTH_AUTO = "auto"
#: Always sign. Fails loudly if the session has no access key, rather than quietly falling back to
#: a token - which is what a caller who asked for signatures wants to know about.
AUTH_SIGNATURE = "signature"
#: Always present the bearer token, even when an access key is available.
AUTH_BEARER = "bearer"


class EuclidEam:
    """Builder for authenticating against a euclid server.

    >>> session = EuclidEam.for_server("https://euclid.example.com").credentials("jens", "secret").login()
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = strip_trailing_slash(base_url)
        self._login_path = "/"
        self._username: str | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._namespace: str | None = None
        # Applied only when the file is actually there, so this default is a no-op on a machine
        # with no euclid deployment and the right thing on one that has.
        self._ca_cert_path: str | None = DEFAULT_CA_CERT_PATH
        self._verify = True
        self._timeout = DEFAULT_TIMEOUT
        self._signing_scheme = SigningScheme.SIGV4
        self._auth = AUTH_AUTO
        self._use_cache = True

    @staticmethod
    def for_server(base_url: str) -> "EuclidEam":
        """Targets a euclid server, e.g. ``https://euclid.example.com``."""
        if not base_url:
            raise ValueError("base_url must not be empty")
        return EuclidEam(base_url)

    # -- builder -----------------------------------------------------------------------------

    def username(self, username: str) -> "EuclidEam":
        """The user ID to log in as."""
        self._username = username
        return self

    def email(self, email: str) -> "EuclidEam":
        """The email address to log in with, when there is no user ID.

        The server resolves the user by ID first and only falls back to the email, so setting both
        silently ignores the email; :meth:`login` sends whichever one identifies the account.
        """
        self._email = email
        return self

    def password(self, password: str) -> "EuclidEam":
        """The password."""
        self._password = password
        return self

    def credentials(self, username: str, password: str) -> "EuclidEam":
        """User ID and password together."""
        return self.username(username).password(password)

    def namespace(self, namespace: str) -> "EuclidEam":
        """The namespace to make active once login succeeds.

        Applied with a follow-up ``change-namespace`` call, mirroring euclid-cli's
        ``eam login --namespace``, and applied whether the login was fresh or served from the
        cache - a cached session may have been established before this was ever asked for, or
        scoped to a different namespace.
        """
        self._namespace = namespace
        return self

    def login_path(self, path: str) -> "EuclidEam":
        """The path to post the login to. ``/`` unless a deployment puts the gateway elsewhere."""
        self._login_path = path if path.startswith("/") else "/" + path
        return self

    def ca_cert_path(self, path: str | None) -> "EuclidEam":
        """A PEM CA certificate to trust alongside the system store, or None for the store alone."""
        self._ca_cert_path = path
        return self

    def verify(self, verify: bool) -> "EuclidEam":
        """Whether to verify the server's certificate. Turn it off for development servers only."""
        self._verify = verify
        return self

    def timeout(self, seconds: float) -> "EuclidEam":
        """How long to wait for a response, in seconds."""
        self._timeout = seconds
        return self

    def signing_scheme(self, scheme: SigningScheme) -> "EuclidEam":
        """Which signing scheme the session signs with. SigV4 unless told otherwise, as euclid's
        server has understood that one from the start."""
        self._signing_scheme = scheme
        return self

    def auth(self, mode: str) -> "EuclidEam":
        """How the session authenticates: :data:`AUTH_AUTO`, :data:`AUTH_SIGNATURE` or
        :data:`AUTH_BEARER`."""
        if mode not in (AUTH_AUTO, AUTH_SIGNATURE, AUTH_BEARER):
            raise ValueError(f"auth must be one of {AUTH_AUTO!r}, {AUTH_SIGNATURE!r}, {AUTH_BEARER!r}")
        self._auth = mode
        return self

    def use_cache(self, use_cache: bool) -> "EuclidEam":
        """Whether ``~/.euclid/credentials`` may be read and written. On by default, which is what
        makes a login shared with euclid-cli and euclid-jdk."""
        self._use_cache = use_cache
        return self

    # -- login -------------------------------------------------------------------------------

    def login(self) -> "EuclidSession":
        """Authenticates, and returns the session every other call goes through.

        A still-valid cached session for this server is reused rather than re-authenticating, so
        calling this repeatedly costs nothing after the first time. Pass ``use_cache(False)`` to
        force a fresh login.

        :raises EuclidAuthenticationError: if the server refuses the credentials.
        :raises ValueError: if no password, or neither a username nor an email, was set and there
            is no cached session to fall back on.
        """
        cached = self._cached_session()
        if cached is not None:
            if self._namespace is not None and self._namespace != cached.namespace:
                cached.change_namespace(self._namespace)
            return cached

        if not self._username and not self._email:
            raise ValueError("username or email must be set before calling login()")
        if self._password is None:
            raise ValueError("password must be set before calling login()")

        # Only one identifier goes out: the server takes the user ID when it is present and only
        # falls back to the email, so sending both would silently ignore the email.
        request = {
            "userId": self._username or "",
            "password": self._password,
            "email": "" if self._username else (self._email or ""),
        }
        body = json.dumps(request)

        client = self._new_client()
        try:
            response = client.post(self._base_url + self._login_path, body, TARGET, "login",
                                   {"Content-Type": "application/json"})
        finally:
            client.close()

        if not response.ok:
            raise EuclidAuthenticationError(response.status, response.text)

        result = LoginResult.from_json(response.json())
        session = EuclidSession(
            base_url=self._base_url, token=result.token, user_id=result.metadata.user,
            account_id=result.metadata.account_id, region=result.metadata.region,
            access_key_id=result.access_key_id, secret_access_key=result.secret_access_key,
            is_admin=result.is_admin, namespace="", raw=result.raw, ca_cert_path=self._ca_cert_path,
            verify=self._verify, timeout=self._timeout, signing_scheme=self._signing_scheme,
            auth=self._auth, cache=self._use_cache)

        if self._namespace:
            session.change_namespace(self._namespace)
        if self._use_cache:
            save_credentials(session.to_cached_credentials())
        return session

    def _cached_session(self) -> "EuclidSession | None":
        """A session rebuilt from ``~/.euclid/credentials``, if one is cached for this server and
        its token has not expired."""
        if not self._use_cache:
            return None
        cached = load_credentials()
        if cached is None or not cached.token or cached.base_url != self._base_url:
            return None
        if not is_token_valid(cached.token):
            return None
        return EuclidSession(
            base_url=self._base_url, token=cached.token, user_id=cached.user_id,
            account_id=cached.account_id, region=cached.region, access_key_id=cached.access_key_id,
            secret_access_key=cached.secret_access_key, is_admin=cached.is_admin,
            namespace=cached.namespace, raw=cached.raw, ca_cert_path=self._ca_cert_path,
            verify=self._verify, timeout=self._timeout, signing_scheme=self._signing_scheme,
            auth=self._auth, cache=self._use_cache)

    def _new_client(self) -> EuclidHttpClient:
        return EuclidHttpClient(self._ca_cert_path, self._timeout, self._verify)


class EuclidSession:
    """An authenticated session, and every EAM operation that needs one.

    Holds two credentials, because the server accepts two. The bearer token is what a login always
    produces; the access key and secret are what it produces when the user has one, and they are
    what a signature is made with. Which of the two a request presents is decided per session by
    ``auth`` - see :data:`AUTH_AUTO`.

    Sessions are mutable: :meth:`change_namespace` changes this session rather than returning a new
    one, since the namespace is a property of what the caller is doing next, not of the login.
    """

    def __init__(self, *, base_url: str, token: str, user_id: str, account_id: str, region: str,
                 access_key_id: str, secret_access_key: str, is_admin: bool, namespace: str,
                 raw: dict[str, Any], ca_cert_path: str | None, verify: bool, timeout: float,
                 signing_scheme: SigningScheme, auth: str, cache: bool) -> None:
        self.base_url = base_url
        self.token = token
        self.user_id = user_id
        self.account_id = account_id
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.is_admin = is_admin
        self.namespace = namespace
        self.raw = raw
        self.signing_scheme = signing_scheme
        self.auth = auth

        #: Where the bearer token comes from, when it does not simply come from :attr:`token`.
        #:
        #: A process that runs for days holds a token that does not last that long. euclid rewrites
        #: an application's credentials file before the token in it expires, so an application that
        #: cached the first token it saw would start collecting 401s about an hour in. Setting this
        #: to something that re-reads that file makes the session follow the rotation, and is what
        #: the retry on "credentials expired" then has something new to retry with.
        self.token_provider: Callable[[], str] | None = None

        self._cache = cache
        self._host_header = host_header_of(base_url)
        self._scheme = scheme_of(base_url)
        # Kept so that a module client this session hands out - see :meth:`esm` - reaches the same
        # server on the same terms, rather than having to be told the connection settings again.
        self._ca_cert_path = ca_cert_path
        self._timeout = timeout
        self._verify = verify
        self._modules: dict[str, Any] = {}
        self._client = self.new_client(lambda action, body: self.auth_headers(TARGET, action, body))

    # -- identity ----------------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"EuclidSession(user_id={self.user_id!r}, account_id={self.account_id!r}, "
                f"region={self.region!r}, namespace={self.namespace!r}, is_admin={self.is_admin!r})")

    def to_cached_credentials(self) -> CachedCredentials:
        """This session in the shape ``~/.euclid/credentials`` holds it."""
        return CachedCredentials(
            token=self.token, user_id=self.user_id, account_id=self.account_id, region=self.region,
            access_key_id=self.access_key_id, secret_access_key=self.secret_access_key,
            is_admin=self.is_admin, base_url=self.base_url, namespace=self.namespace or "")

    def close(self) -> None:
        """Releases the connections this session was holding, its module clients' included."""
        self._client.close()
        for module in self._modules.values():
            module.close()

    def __enter__(self) -> "EuclidSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- users -------------------------------------------------------------------------------

    def list_users(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                   sort_column: str = "userId", sort_direction: str = "asc") -> ListUsersResult:
        """One page of users, and how many exist in total."""
        return ListUsersResult.from_json(self._call("list-users", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def register(self, user_id: str, password: str, email: str = "", account_id: str = "",
                 region: str = "", is_admin: bool = False) -> User:
        """Creates a user. ``account_id`` and ``region`` default to this session's own."""
        response = self._call("register", {
            "userId": user_id, "password": password, "email": email,
            "accountId": account_id or self.account_id, "region": region or self.region,
            "isAdmin": is_admin})
        return User.from_json(response.get("user"))

    def delete_user(self, user_id: str) -> None:
        """Deletes a user."""
        self._call("delete-user", {"userId": user_id})

    # -- namespace scoping -------------------------------------------------------------------

    def change_namespace(self, namespace: str) -> "EuclidSession":
        """Switches the namespace every namespace-scoped call is restricted to, until changed again.

        The server validates it against the current account and the caller's grants, so this is a
        round trip rather than a local assignment. An empty string clears the scope.

        Returns self, so it can be chained onto a login.
        """
        self._call("change-namespace", {"namespace": namespace})
        self.namespace = namespace
        if self._cache:
            update_cached_namespace(self.base_url, namespace)
        return self

    # -- access keys -------------------------------------------------------------------------

    def create_access_key(self) -> CreateAccessKeyResult:
        """Creates an access key for this session's user.

        The secret comes back here and nowhere else - :meth:`list_access_keys` will never show it
        again - so a caller that does not store it has to create another key.
        """
        return CreateAccessKeyResult.from_json(self._call("create-access-key"))

    def list_access_keys(self) -> list[AccessKey]:
        """This user's own access keys, without their secrets."""
        keys = self._call("list-access-keys").get("accessKeys")
        return [AccessKey.from_json(k) for k in keys] if isinstance(keys, list) else []

    def delete_access_key(self, access_key_id: str) -> None:
        """Deletes one of this user's own access keys."""
        self._call("delete-access-key", {"accessKeyId": access_key_id})

    # -- user groups -------------------------------------------------------------------------

    def create_user_group(self, name: str, description: str = "") -> UserGroup:
        """Creates an empty user group. Administrator only."""
        return UserGroup.from_json(self._call("create-user-group", {
            "name": name, "description": description}).get("userGroup"))

    def list_user_groups(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                         sort_column: str = "name", sort_direction: str = "asc") -> ListUserGroupsResult:
        """One page of user groups, and how many exist in total."""
        return ListUserGroupsResult.from_json(self._call("list-user-groups", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def add_user_to_user_group(self, user_group: str, user: str) -> None:
        """Adds a user to a group. Both are ERNs."""
        self._call("user-group-add-user", {"userGroup": user_group, "user": user})

    def remove_user_from_user_group(self, user_group: str, user: str) -> None:
        """Removes a user from a group. Both are ERNs."""
        self._call("user-group-remove-user", {"userGroup": user_group, "user": user})

    def delete_user_group(self, name: str) -> None:
        """Deletes a user group. Administrator only."""
        self._call("delete-user-group", {"name": name})

    # -- accounts ----------------------------------------------------------------------------

    def create_account(self, account_id: str, name: str, description: str = "") -> Account:
        """Creates an account. Administrator only - account creation is platform-level and is not
        delegated to account owners."""
        return Account.from_json(self._call("create-account", {
            "accountId": account_id, "name": name, "description": description}).get("account"))

    def list_accounts(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                      sort_column: str = "accountId", sort_direction: str = "asc") -> ListAccountsResult:
        """One page of accounts, and how many exist in total."""
        return ListAccountsResult.from_json(self._call("list-accounts", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_account(self, account_id: str) -> None:
        """Deletes an account. Administrator only, and it must have no namespaces or grants left."""
        self._call("delete-account", {"accountId": account_id})

    # -- namespaces --------------------------------------------------------------------------

    def create_namespace(self, account_id: str, name: str, description: str = "") -> Namespace:
        """Creates a namespace under an account. Requires admin rights on that account."""
        return Namespace.from_json(self._call("create-namespace", {
            "accountId": account_id, "name": name, "description": description}).get("namespace"))

    def list_namespaces(self, account_id: str, prefix: str = "", page_size: int = 10, page_index: int = 0,
                        sort_column: str = "name", sort_direction: str = "asc") -> ListNamespacesResult:
        """One page of an account's namespaces, and how many exist in total."""
        return ListNamespacesResult.from_json(self._call("list-namespaces", {
            "accountId": account_id, "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_namespace(self, account_id: str, name: str) -> None:
        """Deletes a namespace. Requires admin rights on the account, and no grants may remain."""
        self._call("delete-namespace", {"accountId": account_id, "name": name})

    def grant_namespace_access(self, user: str, account_id: str, namespace: str) -> None:
        """Grants a user access to a namespace. Requires admin rights on the account."""
        self._call("grant-namespace-access", {"user": user, "accountId": account_id, "namespace": namespace})

    def revoke_namespace_access(self, user: str, account_id: str, namespace: str) -> None:
        """Revokes a user's access to a namespace. Requires admin rights on the account."""
        self._call("revoke-namespace-access", {"user": user, "accountId": account_id, "namespace": namespace})

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EAM's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EAM."""
        return self._call("get-metrics")

    # -- transport ---------------------------------------------------------------------------

    def call(self, action: str, payload: Mapping[str, Any] | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        """Sends any EAM action, for one this SDK does not wrap yet.

        Public on purpose: a server that gains an action should be reachable without waiting for a
        release here.
        """
        return self._call(action, payload, timeout)

    def _call(self, action: str, payload: Mapping[str, Any] | None = None,
              timeout: float | None = None) -> dict[str, Any]:
        body = json.dumps(dict(payload) if payload else {}).encode("utf-8")
        response = self._client.post(self.base_url + "/", body, TARGET, action,
                                     self.request_headers(TARGET, action, body), timeout)
        if not response.ok:
            raise EuclidServiceError(TARGET, action, response.status, response.text)
        result = response.json()
        return result if isinstance(result, dict) else {"result": result}

    def request_headers(self, target: str, action: str, body: bytes) -> dict[str, str]:
        """Every header a request to one of this session's modules goes out with, signature included.

        Takes the target rather than assuming EAM because the target is signed: a client for
        another module - :meth:`esm` - has to sign for *its* module, and signing here rather than
        in each module client is what keeps one implementation of how this session authenticates.
        """
        headers = self.routing_headers()
        headers.update(self.auth_headers(target, action, body))
        return headers

    def routing_headers(self) -> dict[str, str]:
        """Who is asking and what they are scoped to. Signed, apart from the namespace."""
        headers = {"Content-Type": "application/json", "Host": self._host_header}
        if self.region:
            headers["x-euclid-region"] = self.region
        if self.account_id:
            headers["x-euclid-account-id"] = self.account_id
        if self.user_id:
            headers["x-euclid-user-id"] = self.user_id
        # Not covered by either signature scheme - see euclid.auth.rfc9421.
        if self.namespace:
            headers["x-euclid-namespace"] = self.namespace
        return headers

    def auth_headers(self, target: str, action: str, body: bytes) -> dict[str, str]:
        """The authentication headers alone, rebuilt per request.

        Rebuilt rather than cached because a signature is only valid around the moment it was made,
        and because this is what the client calls again when the server says the credentials it
        just presented had expired.
        """
        if self._should_sign():
            request = SignableRequest("POST", "/")
            request.headers_from(self.routing_headers())
            request.header("x-euclid-target", target)
            request.header("x-euclid-action", action)
            request.set_body(body)
            request.set_scheme(self._scheme)
            self.signing_scheme.sign(request, self.access_key_id, self.secret_access_key,
                                     self.region, target)
            return {name: request.get(name) for name in self.signing_scheme.signature_header_names()}
        return self.bearer_headers()

    def bearer_headers(self) -> dict[str, str]:
        """The bearer token, presented as an ``Authorization`` header.

        Its own method because a request is not always free to sign: ESM's byte-carrying actions
        present the token whatever this session would otherwise do - see
        :class:`euclid.modules.esm.EuclidEsm`.
        """
        return {"Authorization": "Bearer " + self.current_token()}

    def new_client(self, header_factory: Callable[[str, bytes], dict[str, str]]) -> EuclidHttpClient:
        """A connection to this session's server, on the TLS and timeout settings it logged in with.

        The factory is what a request whose credentials expired in flight is rebuilt with, so a
        module client passes the one that rebuilds *its* headers - see :meth:`auth_headers`.
        """
        client = EuclidHttpClient(self._ca_cert_path, self._timeout, self._verify)
        return client.header_factory(header_factory)

    def esm(self) -> "EuclidEsm":
        """ESM - euclid's storage module - on this session's credentials."""
        from .esm import EuclidEsm

        return self._module("esm", EuclidEsm)

    def eqs(self) -> "EuclidEqs":
        """EQS - euclid's queue module - on this session's credentials."""
        from .eqs import EuclidEqs

        return self._module("eqs", EuclidEqs)

    def ens(self) -> "EuclidEns":
        """ENS - euclid's notification module - on this session's credentials."""
        from .ens import EuclidEns

        return self._module("ens", EuclidEns)

    def ekm(self) -> "EuclidEkm":
        """EKM - euclid's key management module - on this session's credentials."""
        from .ekm import EuclidEkm

        return self._module("ekm", EuclidEkm)

    def ess(self) -> "EuclidEss":
        """ESS - euclid's secret store - on this session's credentials."""
        from .ess import EuclidEss

        return self._module("ess", EuclidEss)

    def _module(self, name: str, factory: Callable[["EuclidSession"], Any]) -> Any:
        """The one client this session has for a module, built the first time it is asked for.

        One per module rather than one per call, so an application that reaches for
        ``session.eqs()`` inside a loop pays for one connection rather than one per iteration. Each
        follows this session: a :meth:`change_namespace` between two calls scopes the second one,
        and a :attr:`token_provider` set here is the token they present.

        The imports are inside the methods above rather than at module scope because each module
        client needs this module for the session it is built from, and one side of the cycle has to
        be the late one.
        """
        client = self._modules.get(name)
        if client is None:
            client = self._modules[name] = factory(self)
        return client

    def current_token(self) -> str:
        """The bearer token to present now - :attr:`token_provider`'s, or :attr:`token`."""
        return self.token_provider() if self.token_provider is not None else self.token

    def _should_sign(self) -> bool:
        has_key = bool(self.access_key_id and self.secret_access_key)
        if self.auth == AUTH_BEARER:
            return False
        if self.auth == AUTH_SIGNATURE:
            if not has_key:
                raise ValueError("auth='signature' was requested but this session has no access key - "
                                 "the login returned none, so there is nothing to sign with")
            return True
        return has_key

    @property
    def authority(self) -> str:
        """The ``@authority`` this session's signatures are made over, for diagnostics."""
        return authority_of(self.base_url)
