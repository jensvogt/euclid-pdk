# euclid-pdk

Python client library for the [euclid](https://github.com/jensvogt/euclid) server.

It covers the three things everything else needs - the connection, request signing, and EAM,
euclid's access management module - and the five modules an application spends its time in: ESM
(storage), EQS (queues), ENS (topics), EKM (keys) and ESS (secrets). The remaining modules (EES,
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

Every other module hangs off that session, one client each and one connection each:

```python
esm, eqs, ess = session.esm(), session.eqs(), session.ess()

bucket = esm.create_bucket("reports")
esm.upload_file(bucket.ern, "2026/q3.pdf", "q3.pdf")

queue = eqs.create_queue("orders")
eqs.send_message(queue.ern, '{"order": 17}')

for message in eqs.receive_messages(queue.ern, wait_time=20).messages:
    handle(message.body)
    eqs.delete_message(message.receipt_handle)

connect(password=ess.get_secret("db-password").value)
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

## What ESM covers

`session.esm()` returns the storage client, on the session's credentials and namespace - and it
follows them, so a `change_namespace` between two calls scopes the second one. It is the same
client each time, which is what keeps an application that asks for it per operation to one
connection.

| Method | Action |
| --- | --- |
| `create_bucket`, `list_buckets`, `get_bucket_ern`, `get_bucket_size`, `rename_bucket`, `delete_bucket` | buckets |
| `add_bucket_tag`, `set_bucket_tag`, `delete_bucket_tag` | bucket tags |
| `set_bucket_internal` | whether a bucket is euclid's own plumbing, and so left out of listings |
| `enable_encryption`, `disable_encryption` | encryption at rest, under an EKM key |
| `put_object`, `get_object`, `upload_file`, `download_file` | an object's bytes |
| `list_objects`, `get_object_count`, `copy_object`, `move_object`, `rename_object` | objects |
| `delete_object`, `delete_objects`, `purge_bucket` | deleting them |
| `touch_object` | re-announcing objects to listeners that missed their creation |
| `add_object_attribute`, `set_object_attribute`, `list_object_attributes`, `delete_object_attribute` | user-defined attributes |
| `subscribe`, `unsubscribe`, `list_subscriptions`, `parse_bucket_event` | announcing a bucket's events to a queue or a topic |
| `metrics` | ESM's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

`upload_file` splits the file into 5 MiB parts and sends four at a time by default, reading it a
part at a time rather than into memory; `download_file` tries a single request first and falls back
to fetching parts when the server says the object is too large for one. Both retry a part that
failed transiently - a 5xx, or a request that got no answer - four times before giving up, because
a transfer is long and made of many steps and failing one of them throws away all the others.

Attributes belong on the write rather than on a call after it. Completing an upload is finished off
in the background on the server, and the object row it writes at the end carries what the upload
supplied, so an attribute added in between is overwritten and silently lost:

```python
from euclid import Variant

esm.upload_file(bucket_ern, "2026/q3.pdf", "q3.pdf",
                attributes={"tenant": "acme", "revision": 3, "checksum": Variant("binary", b"\x01\x02")},
                system_attributes={"priority": "LOW"})
```

The two maps are not the same one. `attributes` are the caller's own, listed back by
`list_object_attributes` and meaningless to euclid; `system_attributes` are euclid's envelope,
which travel with the object across every hop. The one euclid acts on is `priority`: it is what
carries a producer's decision through a bucket into the queue subscribed to it. Plain Python values
are tagged with the type euclid stores them under, so `Variant` is only needed for a tag other than
the obvious one - a 32-bit `int` rather than a `long`, say.

Four actions carry an object's bytes rather than JSON - `put-object`, `get-object`, `upload-part`
and `download-part` - and those present the session's bearer token rather than a signature, which is
what euclid-cli and euclid-jdk do for the same four, so all three clients write objects the same
way. A session created with `auth=AUTH_SIGNATURE` signs them anyway: it asked not to be handed a
token quietly, and hashing raw bytes is exact here in a way it is not in every language.

## What EQS covers

`session.eqs()` returns the queue client.

| Method | Action |
| --- | --- |
| `create_queue`, `list_queues`, `get_queue_ern`, `get_queue_metadata`, `delete_queue` | queues |
| `add_queue_tag`, `set_queue_tag`, `delete_queue_tag` | queue tags |
| `stop_queue`, `start_queue`, `set_queue_visibility`, `purge_queue`, `purge_all_queues` | what a queue does |
| `send_message`, `receive_messages`, `receive_all_messages`, `delete_message`, `delete_message_by_id` | messages |
| `list_messages`, `get_message_count`, `get_message_metadata`, `set_message_visibility` | inspecting them |
| `get_message_attribute`, `set_message_attribute` | message attributes |
| `redrive_dlq` | moving messages out of a dead letter queue |
| `metrics`, `as_internal` | EQS's own metrics, and marking a caller's traffic as euclid's own |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

Receiving is a lease, not a read. A message a consumer takes is invisible to every other consumer
until its visibility timeout expires, and `delete_message(receipt_handle)` is what says the work was
done - so the delete belongs after the work, since a consumer that dies instead simply stops holding
the lease and the message comes back. `list_messages` is the other thing: it reads a queue without
touching it, and nothing it returns can be deleted by receipt handle.

`receive_messages(ern, wait_time=20)` is a long poll, and the waiting is the server's: it holds the
request open until a message lands or the window runs out, so an idle queue costs one request rather
than one per poll tick and a message comes back the instant it is sent. That request gets its own
timeout - the session's is sized for an answer that comes straight back. Two cases the client
handles for you: with no wait asked for, an empty queue costs no receive at all (a receive is a
write); and a server with no long-poll slot free answers immediately rather than queueing behind the
waiters, which is a pause and another ask rather than an empty result.

`as_internal()` returns a second view whose requests carry `x-euclid-internal`. Some calls observe
the system rather than use it - the same `get_message_count` is a user's question one moment and a
metric collector's poll the next - and euclid scales a module on the traffic it sees, so
instrumentation that polls every few seconds would otherwise keep a pool permanently awake.

## What ENS covers

`session.ens()` returns the topic client.

| Method | Action |
| --- | --- |
| `create_topic`, `list_topics`, `get_topic_ern`, `get_topic_metadata`, `delete_topic` | topics |
| `add_topic_tag`, `set_topic_tag`, `delete_topic_tag` | topic tags |
| `publish_message`, `list_messages`, `get_message_count`, `purge_topic`, `purge_all_topics` | messages |
| `get_message_attribute`, `set_message_attribute` | message attributes |
| `subscribe`, `unsubscribe`, `list_subscriptions` | delivering a topic's messages to a queue |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

The difference from EQS is what happens to a message once it is there. A queue holds a message until
a consumer takes it; a topic hands each message to every subscriber and keeps it as a record of
having done so. So there is no receive and no receipt handle here - a subscriber consumes from its
own queue, which is where the message was delivered:

```python
ens.subscribe(topic_ern, eqs.get_queue_ern("orders"))
ens.publish_message(topic_ern, '{"order": 17}', priority="HIGH")
```

Two field names are the server's own asymmetry rather than a typo here: a message attribute travels
as `key` throughout ENS and as `name` in most of EQS, and this SDK reproduces both rather than
papering over either, so a request built from this documentation matches what euclid-cli sends.

## What EKM covers

`session.ekm()` returns the key client.

| Method | Action |
| --- | --- |
| `create_key`, `list_keys`, `set_key_description`, `add_key_tag`, `delete_key_tag` | keys |
| `revoke_key`, `delete_key` | ending one |
| `encrypt`, `decrypt` | using one |
| `import_certificate`, `create_certificate`, `get_certificate`, `list_certificates`, `delete_certificate` | certificates |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

Key material never leaves the server: `encrypt` and `decrypt` send the bytes to the key rather than
fetching the key to the bytes, and nothing in `euclid.dto.ekm` has a field for it. `encrypt` returns
`IV || ciphertext || tag`, which is exactly what `decrypt` takes back.

A key is named two ways and they are not interchangeable — the server's own split, which the SDK
reproduces. The ID (`Key.name`, what `create_key` returns) encrypts, decrypts and deletes; the ERN
revokes, describes and tags.

Ending a key has two forms, and the difference is what happens to what it wrote. `revoke_key` stops
it encrypting anything further and still decrypts, so it is what to reach for when the key should no
longer be used but the data under it is still wanted. `delete_key` schedules it — seven days by
default — and when that date passes, everything under it becomes unreadable: a bucket's objects, a
secret's value. That window is the only chance anybody gets to notice, which is why the key is not
deleted outright.

```python
key = ekm.create_key(description="customer exports")   # AES-256 unless told otherwise
sealed = ekm.encrypt(key.name, b"account 4711")
assert ekm.decrypt(key.name, sealed) == b"account 4711"
```

`create_certificate` generates a self-signed one, for an installation that has to serve HTTPS before
anybody has bought it a real one — `Certificate.generated` says nobody vouched for it.
`import_certificate` takes a real one plus the private key that proves it, and the server checks the
two against each other rather than letting a mismatch surface later as a handshake that fails for
every caller. The private key goes in and is never handed back.

## What ESS covers

`session.ess()` returns the secret client.

| Method | Action |
| --- | --- |
| `create_secret`, `get_secret`, `list_secrets`, `delete_secret` | secrets |
| `rotate_secret`, `update_secret` | changing one |
| `add_secret_tag`, `delete_secret_tag` | secret tags |
| `metrics` | ESS's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

A value is encrypted with EKM before it is stored, so a secret's life is tied to a key's: deleting
that key is what makes the value unrecoverable, whatever ESS still says about the secret.
`create_secret(..., key_ern=...)` is how a set of secrets is made revocable together, and
`update_secret(name, key_ern=...)` re-encrypts one onto another key — how a secret is moved off a key
that is being retired.

Only `get_secret` returns a value; everything else answers with metadata alone, so a listing, a
rotation or a tag change can be logged without being the thing that leaks it:

```python
password = ess.get_secret("db-password").value
ess.rotate_secret("db-password", new_password)         # bumps version, records `rotated`
```

`update_secret` distinguishes a field being sent from a field being empty, because the server does:
leaving `description` as `None` leaves the stored one alone, while passing `""` clears it - and the
same for `value`, since an empty string is a value somebody may legitimately store. An update that
names none of the three raises `ValueError` here rather than costing a round trip to be refused.

## Writing a module of your own

`euclid.ModuleClient` is what the three above are built on: it knows euclid's request shape, the
session's credentials and how to turn a refusal into a `EuclidServiceError`, and nothing else. A
module this SDK has not wrapped needs a target and its actions:

```python
from euclid import ModuleClient

class Ees(ModuleClient):
    target = "ees"

    def list_rules(self, prefix=""):
        return self.call("list-rules", {"prefix": prefix})

Ees(session).list_rules()
```

Set `byte_actions` on the subclass for any action of that module whose body is raw bytes rather than
JSON; `_post_bytes` sends one, and those actions then authenticate the way ESM's and EKM's do.

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

Behind that gateway sit two stand-ins that do the work rather than stub it. `tests/fake_storage.py`
assembles an upload's parts in part order and hands back the byte range a download asked for;
`tests/fake_queues.py` leases messages out with receipt handles and either honours a long poll or
declines it the way a server short of slots does. Both are deliberate: the part of a multipart
transfer a stubbed test cannot see is exactly the part that corrupts an object, and a queue whose
server always answers immediately cannot tell a client that abandons a long poll from one that
does not.

## Licence

Apache License 2.0.
