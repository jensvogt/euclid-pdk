# euclid-pdk

Python client library for the [euclid](https://github.com/jensvogt/euclid) server.

This first release covers the three things everything else needs: the connection, request signing,
and EAM - euclid's access management module. The remaining modules (EQS, ESM, ENS, EKM, ESS, EES,
EAP, ETS, EMO) speak the same protocol over the same client and will follow.

Requires Python 3.10 or newer, and **has no dependencies**. Installing this SDK does not bring a
TLS stack, an HTTP client and a JSON parser along with it: the wire protocol is JSON over HTTP and
the signatures are HMAC-SHA256, all of which the standard library already covers.

## Installation

```bash
pip install euclid-pdk
```

From a checkout:

```bash
pip install -e ".[dev]"
```

## Usage

Log in once and reuse the session:

```python
from euclid import Euclid

session = Euclid.for_server("https://euclid.example.com").login("jens", "secret")

for user in session.list_users(prefix="j", page_size=25).users:
    print(user.user_id, user.email)
```

The builder form takes the same options one at a time, which reads better when there are several:

```python
from euclid import Euclid, SigningScheme

session = (Euclid.for_server("https://euclid.example.com")
           .access()
           .credentials("jens", "secret")
           .namespace("development")
           .signing_scheme(SigningScheme.RFC9421)
           .ca_cert_path("/etc/euclid/euclid_cert.crt")
           .login())
```

A session holds a connection, so close it when you are done - or use it as a context manager:

```python
with Euclid.for_server(url).login("jens", "secret") as session:
    session.create_account("111", "acme", "an account")
```

### The credentials cache

`login()` writes `~/.euclid/credentials` and reads it back on the next call, so logging in twice
costs one round trip. It is the same file `euclid-cli` and `euclid-jdk` use, with the same field
names, so a login from any of the three is picked up by the others. `EUCLID_CREDENTIALS_FILE`
overrides the path, which is also how euclid hands a managed application its own credentials.

Pass `use_cache(False)` to force a fresh login and leave the file alone.

### Signing

A login returns two credentials: a bearer token, and - when the user has one - an access key and
secret. By default a session signs with the access key when it has one and presents the token
otherwise, which is what `AUTH_AUTO` means. euclid accepts either for every action.

```python
from euclid import AUTH_BEARER, AUTH_SIGNATURE, SigningScheme

session = Euclid.for_server(url).login("jens", "secret", auth=AUTH_BEARER)
```

Two signing schemes are implemented, both keyed by the same access key and secret:

| Scheme | Where the signature travels | Notes |
| --- | --- | --- |
| `SigningScheme.SIGV4` | `Authorization`, plus `x-amz-date` and `x-amz-content-sha256` | The default, and what euclid has understood from the start |
| `SigningScheme.RFC9421` | `Signature` and `Signature-Input`, plus `Content-Digest` | [RFC 9421](https://www.rfc-editor.org/rfc/rfc9421.html) HTTP Message Signatures, the standard scheme meant to replace it |

Both cover a **fixed** set of headers rather than a set the request declares: the method, path and
authority, the body digest, and the `x-euclid-account-id`, `x-euclid-action`, `x-euclid-region`,
`x-euclid-target` and `x-euclid-user-id` headers that carry what the request is asking for and on
whose behalf. euclid's server compares that list against its own for exact equality, so a signature
covering more, fewer, or the same components in another order is rejected. `x-euclid-namespace` is
*not* covered - a real gap rather than a simplification, and one that has to be closed on both
sides at once.

Verification is implemented too, not just signing, and is what the test suite's fake gateway uses:

```python
from euclid.auth import SignableRequest, SigningScheme

request = SignableRequest("POST", "/")
request.headers_from(incoming_headers).set_body(incoming_body).set_scheme("https")

scheme = SigningScheme.of(request)
key_id = scheme.verify(request, lookup_secret) if scheme else None
```

### TLS

`https://` URLs are verified against the system trust store. A euclid deployment usually presents
its own certificate, so `/etc/euclid/euclid_cert.crt` is trusted *alongside* the system store when
that file exists - the same default `euclid-cli --ca-cert` uses. Point `ca_cert_path` elsewhere, or
pass `verify(False)` for a development server whose certificate nothing vouches for.

### Errors

| Exception | Raised when |
| --- | --- |
| `EuclidAuthenticationError` | a login was refused |
| `EuclidServiceError` | a module refused or failed an action; carries `target`, `action`, `status` and `reason` |
| `EuclidError` | base class for both |

`reason` is the server's own message, pulled out of the `{"error": "..."}` body every euclid module
answers failures with.

### Retries

Two, both narrow on purpose:

* A request that failed because the connection was closed while it sat idle is sent again once, on
  a fresh connection. Only failures that produced no response at all qualify.
* A 401 whose body says the credentials had expired is sent again once with rebuilt headers, if
  rebuilding them produces something different. A wrong password or a missing permission is
  answered once, as before.

For a process that outlives its token, set `session.token_provider` to something that re-reads the
credentials file; the retry then has a fresh token to use.

## What EAM covers

| Method | Action |
| --- | --- |
| `list_users`, `register`, `delete_user` | users |
| `create_access_key`, `list_access_keys`, `delete_access_key` | the caller's own signing credentials |
| `create_user_group`, `list_user_groups`, `delete_user_group`, `add_user_to_user_group`, `remove_user_from_user_group` | groups |
| `create_account`, `list_accounts`, `delete_account` | accounts |
| `create_namespace`, `list_namespaces`, `delete_namespace`, `grant_namespace_access`, `revoke_namespace_access` | namespaces |
| `change_namespace` | which namespace this session is scoped to |
| `metrics` | EAM's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

Several of these are administrator-only server-side; `session.is_admin` says whether the logged-in
user is one, though the server enforces it regardless.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite runs against a fake euclid gateway (`tests/fake_gateway.py`) that authenticates requests
with the same rules `Core::HttpActionServer::Authenticate` applies, so a client that signs one
thing and sends another fails there rather than in production. The SigV4 canonicalisation is
additionally pinned by AWS's own published test vectors, which is what makes this SDK, euclid's C++
and euclid-jdk agree rather than merely each agree with itself.

## Licence

Apache License 2.0.
