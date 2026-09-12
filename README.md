# euclid-pdk

Python client library for the [euclid](https://github.com/jensvogt/euclid) server.

It covers the three things everything else needs - the connection, request signing, and EAM,
euclid's access management module - and the ten modules an application spends its time in: ESM
(storage), EQS (queues), ENS (topics), EES (events), EKM (keys), ESS (secrets), EKV (key-value
tables), EAG (the API gateway), EAP (the applications behind it) and ETS (FTP and SFTP onto a
bucket). The remaining modules (EMO, EMM, EMD and the rest) speak the same protocol over the same
client and will follow.

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

### Accounts and namespaces

Every named thing in euclid lives in an account and a namespace, and is unique only within that
pair: two namespaces may each have an `orders` queue, a `suppliers` table, a `billing` application
or an `orders` route, and they have nothing to do with each other. A bare name always means *yours* —
`get_queue_ern("orders")`, a bucket named in a deployment, a queue named in a grant — and the server
resolves it in the account the session logged into and the namespace it is scoped to. It cannot
reach into another namespace's resource of the same name.

That scope is the session's, and `change_namespace` moves it:

```python
session.change_namespace("development")
orders = session.eqs().get_queue_ern("orders")   # development's orders queue
```

Two consequences worth knowing. Resources that run as processes — EAP applications and ETS transfer
servers — also carry a `runtime_name`, because a process, its socket and its log channel have no
namespace to live in and so cannot be keyed by an ID two namespaces may share; it is issued once and
never changes, so moving an application does not orphan what is already running. And for EAP,
changing an application's namespace is a *move* rather than a field change: the buckets and queues it
may reach are re-resolved in the namespace it moves to, and the move is refused if an application of
that ID already lives there.

The two calls that empty a whole namespace — `eqs.purge_all_queues()` and `ens.purge_all_topics()` —
default to the session's own, which is the scope the caller can see. Emptying the field asks for
every namespace of the account, which is a much larger thing to ask for, so it has a name:

```python
from euclid.modules.eqs import EVERY_NAMESPACE

eqs.purge_all_queues()                          # this namespace
eqs.purge_all_queues(namespace=EVERY_NAMESPACE) # all of them, deliberately
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
| `set_queue_delay`, `set_queue_max_message_length` | how long a send is held back, and how large it may be |
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

**A queue's visibility, delay and size limit can be changed while it is in service**, and all three
apply to what is sent or received from here on:

```python
from euclid.modules.eqs import INSTALLATION_MAX_MESSAGE_LENGTH, MAX_DELAY, MAX_VISIBILITY

eqs.set_queue_visibility(queue_ern, 120)           # seconds, 0 to MAX_VISIBILITY (43200)
eqs.set_queue_delay(queue_ern, 30)                 # seconds, 0 to MAX_DELAY (900)

limits = eqs.set_queue_max_message_length(queue_ern, 262144)
limits.max_message_length                          # what the queue holds
limits.effective_max_message_length                # what a send is measured against
```

Only the queue's default visibility changes: messages already in flight keep the window they were
given when they were received, so this can neither expire a lease a consumer is still working on nor
hold back a message its consumer has already given up on. `MAX_VISIBILITY` is twelve hours, the
bound AWS SQS holds `VisibilityTimeout` to, and it is one range for both `set_queue_visibility` and
`set_message_visibility` — a queue default outside what a single message may be given would be a
figure no message could ever take.

A message already waiting was given its due time when it arrived, so lowering the delay does not
bring it forward and raising it does not push it back — which is what keeps this from disturbing work
already in the queue. `MAX_DELAY` is the bound AWS SQS holds `DelaySeconds` to and euclid keeps it: a
delay smooths a burst, and anything longer is a schedule rather than a queue. A delay outside that
range raises `ValueError` here rather than costing a round trip to be refused.

Two numbers come back from the size limit because they can differ.
`INSTALLATION_MAX_MESSAGE_LENGTH` (zero) stores nothing of the queue's own and follows the
installation's default as that changes, and the effective limit is what that default currently is —
reporting the stored zero alone would read as a queue that accepts nothing. A negative limit raises
`ValueError`. Messages already on the queue were accepted under the rule in force when they arrived,
and a lowered limit is not a reason to lose them.

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
| `stop_topic`, `start_topic` | holding delivery, and letting it go again |
| `set_topic_retention`, `set_topic_max_message_length` | how long a published message is kept, and how large it may be |
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

**Stopping a topic holds delivery without refusing publishers.** A stopped topic still accepts what
is published to it and keeps it; it simply does not fan it out — which is what a subscriber being
redeployed, or a downstream system taken down for the evening, actually wants. `start_topic` hands
over the whole backlog, oldest first, as part of the call, and says how much went:

```python
ens.stop_topic(topic_ern)                        # publishers carry on; delivery does not
...
released = ens.start_topic(topic_ern).released   # the backlog goes out here, oldest first
```

That backlog is real work: a topic that collected a fortnight of traffic is a fortnight of fan-out
in that one call. The server pages through it and marks each message as it goes, so a start that is
interrupted has delivered a prefix rather than nothing and running it again resumes.
`get_topic_metadata(...).held` is how much is waiting, which is what says whether starting it is a
moment's work.

**Retention is worth setting.** A topic is fanned out at publish time, so nothing ever consumes its
messages and nothing else removes them — without a period the collection only grows, and every topic
shares it:

```python
from euclid.modules.ens import INSTALLATION_RETENTION, RETENTION_FOREVER

ens.set_topic_retention(topic_ern, 7 * 24 * 3600)          # a week, in seconds
ens.set_topic_retention(topic_ern, INSTALLATION_RETENTION) # 0: follow the installation's own
ens.set_topic_retention(topic_ern, RETENTION_FOREVER)      # -1: keep everything
```

Zero is not "keep nothing" but "whatever `euclid.modules.ens.retention-period` says", followed as it
changes rather than frozen on the day the topic was made. `RETENTION_FOREVER` is not a very long
period either: the server stores such a message with no expiry at all, which is what its TTL index
ignores, so nothing ever removes it — the topic then grows without limit and only `purge_topic`
empties it. The change applies to messages published afterwards; the ones already stored keep the
expiry they were stamped with. A period below `-1` raises `ValueError` here rather than costing a
round trip to be refused.

**A topic's size limit is not a queue's rule.** `set_topic_max_message_length(topic_ern, 262144)`
sets the largest message the topic accepts, and one number comes back rather than two — because a
topic will not take a zero, so what it holds and what a publish is measured against cannot come
apart the way a queue's can. Zero here is not "follow the installation's default" but a topic that
accepts nothing, which the server refuses and this SDK refuses first; taking nothing for a while is
what `stop_topic` is for, and that says so reversibly and without losing what is published meanwhile.
As with retention, the change applies to what is published afterwards.

Two field names are the server's own asymmetry rather than a typo here: a message attribute travels
as `key` throughout ENS and as `name` in most of EQS, and this SDK reproduces both rather than
papering over either, so a request built from this documentation matches what euclid-cli sends.

## What EES covers

`session.ees()` returns the event client — euclid's event bus, for consumers that are not euclid
modules.

| Method | Action |
| --- | --- |
| `subscribe_events`, `unsubscribe_events`, `list_subscriptions` | what a name is interested in |
| `receive_events`, `ack_event`, `ack_events` | claiming events and deleting them |
| `metrics` | EES's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

A subscriber registers a durable *name* and pulls. Nothing is pushed and no queue has to be created:
`receive_events` claims what is waiting and `ack_events` deletes it, and an event claimed but never
acknowledged becomes claimable again when its visibility timeout runs out — so a consumer that dies
mid-work loses nothing, and the acknowledgement belongs after the work rather than before it.

The name decides fan-out. Two *instances* of one application share a name and compete for each
event; two *different* applications use different names and each get their own copy.

```python
from euclid.modules.ees import OBJECT_CREATED

ees.subscribe_events("invoice-indexer", [OBJECT_CREATED], {"prefix": "invoices/2026/"})

while True:
    for event in ees.receive_events("invoice-indexer", wait_time=20).events:
        index(event["bucketErn"], event["key"])       # an Event reads its payload like a mapping
        ees.ack_event("invoice-indexer", event.event_id)
```

The filter is evaluated where the event is published rather than where it is read, so a subscriber
accumulates what it asked for rather than everything of that type in the installation. ESM's object
events — `OBJECT_CREATED`, `OBJECT_UPDATED`, `OBJECT_DELETED` — carry a flat payload of strings,
numbers and booleans precisely so a filter can match it by equality: `{"bucketErn": ...}` for one
bucket, `{"prefix": "invoices/2026/"}` for one "directory" (a key is a path by convention only, and
`prefix` is that convention spelled out by the server rather than by every subscriber), and
`{"directory": False}` to skip directory markers.

An event's `payload` and a subscription's `filter` stay plain dictionaries. Their shape belongs to
the event type, so parsing them would mean this SDK knowing what every module publishes — and going
out of date the first time one of them adds a field.

`receive_events` long-polls, with a request timeout of its own; `wait_time` is clamped by the server
to 20 seconds, and a server with no long-poll slot free answers at once with whatever is there,
which a consumer loop simply asks about again.

This is the richer, server-side-filtered path. ESM's own `subscribe` puts a notification into a
queue or topic instead — see `parse_bucket_event` — and the `esm.subscription.*` events are the
plumbing behind it rather than something to subscribe to.

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

## What EKV covers

`session.ekv()` returns the key-value client.

| Method | Action |
| --- | --- |
| `create_table`, `describe_table`, `list_tables`, `delete_table` | tables |
| `put_item`, `get_item`, `find_item`, `delete_item` | items, one at a time |
| `query` | the items of one partition, in sort-key order |
| `scan` | a table's items without regard to their key |
| `metrics` | EKV's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

A table name is unique within an account and a namespace: two namespaces may each have a `suppliers`
table, and their items are nothing to do with each other.

A table is keyed on one attribute or on two: a partition key that identifies an item, and optionally
a sort key that orders the items sharing a partition key - which is what makes a partition readable
as a range. Both have declared types (`STRING`, `NUMBER`, `BINARY`), fixed at creation, and the type
is what a comparison is made under: a `NUMBER` sort key orders 2, 9, 10, 100 rather than putting
"10" before "9".

An item's other attributes are free-form documents - scalars, lists, nested maps - and are not
declared anywhere. They are also not the tagged `Variant` that EQS, ENS and ESM attributes use: EKV
stores what JSON can express.

```python
from euclid.modules.ekv import GE, NUMBER

ekv.create_table("sessions", "userId", sort_key="startedAt", sort_key_type=NUMBER)
ekv.put_item("sessions", {"userId": "jens", "startedAt": 1757462400, "host": "laptop"})

for item in ekv.query("sessions", "jens", GE, 1757462400).items:
    print(item["host"])                       # an Item reads like a mapping
```

Three things this SDK does rather than pass straight through:

* **`put_item` replaces rather than merges**, so changing one field means reading the item, changing
  it and writing the whole thing back. `Item` therefore keeps the server's `_created` and
  `_modified` out of `.attributes` and exposes them as `.created` and `.modified` - left in, they
  would be written back as two attributes of the caller's own, and stick.
* **`get_item` raises on a miss** (HTTP 404), because "there is no such item" and "here is an item
  with nothing in it" are different. `find_item` is its miss-tolerant twin, returning `None` — 
  `dict.get` to its `dict[...]`. Only a 404 becomes `None`; a malformed key still raises.
* **`query` always sends `forward`**, since the server reads an absent flag as descending rather
  than as unspecified. Asking for `BETWEEN` without both bounds raises `ValueError` here rather than
  costing a round trip to be refused.

`query` addresses a partition by key; `scan` reads the table. The second is fine for a small table
or an export and is the wrong tool for a lookup - `ScanResult.total` says how much there is to get
through.

## What EAP covers

`session.eap()` returns the application client. Administrator-only server-side, all of it.

| Method | Action |
| --- | --- |
| `create_application`, `update_application`, `redeploy_application`, `delete_application` | deploying |
| `start_application`, `stop_application` | running |
| `list_applications`, `get_application` | what is deployed, and what is answering |
| `set_log_level`, `reset_log_level` | what one application logs at |
| `metrics` | EAP's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

An application is deployed from an artifact already in a bucket — ESM puts it there, EAP names it —
and the deployment says which buckets and queues it may reach. euclid grants those to the identity
it runs as: a technical principal it creates for the application unless one is named, with no
password, no login and one access key, so nothing an application leaks is a person's credential.

```python
from euclid.modules.eap import JAVA

eap.create_application("order-service", JAVA, bucket="artifacts",
                       artifact="order-service-1.4.0.jar", queues=["orders"],
                       min_instances=2, max_instances=5)
eap.start_application("order-service")
```

A few things this SDK reproduces rather than smooths over:

* **Deploying names things; the answer describes ERNs.** A deployment takes a `bucket` name and an
  `artifact` key, and the `Application` that comes back has `bucket_ern` and `artifact_key`; the
  `buckets` and `queues` granted come back resolved into `resources`, in the namespace the
  application is deployed into. Names are what an operator has in hand, ERNs are what euclid stores.
* **An application is identified by its namespace as well as its ID.** `Application.namespace` says
  which one — two namespaces may each deploy a `billing` — and `runtime_name` is what the process,
  its socket and its log channel are named after, since none of those has a namespace to live in.
  Naming a `namespace` in `update_application` is a *move*: the grants are re-resolved there, and it
  is refused if that namespace already has an application of this ID.
* **`desired_state` is what was asked for and `state` is what is running.** `start_application`
  changes the first and the manager acts on it, so the application in the answer is usually still
  `STOPPED`. The two differing is an application starting up; the two differing for long is one that
  cannot.
* **`update_application` sends only what it names** — but `buckets` and `queues` are re-resolved
  together whenever *either* is named, so the SDK leaves both out unless you pass them. Passing
  either alone revokes what the other used to grant; passing both empty revokes everything,
  deliberately.

`redeploy_application` is what a new build of the same application usually wants: the artifact
defaults to the one already deployed and the version to whatever the artifact's name says. A
redeploy that would change neither the version nor the checksum is refused with HTTP 409 — it would
restart the instances for nothing, and usually means the new artifact never reached the bucket.

## What EAG covers

`session.eag()` returns the API gateway client. Every action here is administrator-only
server-side; `session.is_admin` says whether the logged-in user is one, though the server enforces
it regardless.

| Method | Action |
| --- | --- |
| `create_route`, `create_module_route`, `get_route`, `list_routes`, `delete_route` | routes |
| `update_route`, `set_route_active` | changing one |
| `list_listeners` | the ports the gateway answers on, and whether it is answering |
| `metrics` | EAG's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

A route ID is unique within an account and a namespace rather than across the installation, so two
namespaces may each publish an `orders` route - and `namespace` on a create says which one it
publishes in, defaulting to the session's.

A route publishes a path prefix and says where everything beneath it goes: to an application euclid
runs, or to one action of a euclid module. It is one or the other, never both and never neither -
those are reached in entirely different ways, and a route naming both would leave which one wins up
to the proxy. This SDK raises `ValueError` for that rather than costing a round trip to be refused.

```python
from euclid.modules.eag import EUCLID_AUTH

eag.create_route("orders", "/api/orders", "order-service",
                 methods=["GET", "POST"], authentication=EUCLID_AUTH)

# The way in for something outside euclid that needs euclid itself.
eag.create_module_route("login", "/euclid/login", "eam", "login")
```

Module routes are what a browser needs to log in before it can call anything: without one, a front
end talks to the API gateway for the application and to euclid's own gateway for its credentials -
two ports, two origins, and CORS between them.

`authentication` is `NO_AUTH` (proxied as it arrives, the application enforcing whatever it
requires), `EUCLID_AUTH` (a euclid credential — token, RFC 9421 signature or SigV4 — verified before
anything is forwarded), or `BASIC_AUTH` (HTTP Basic against a euclid user's password, for the
callers a euclid credential does not suit).

`set_route_active(route_id, False)` is how something stops being exposed in a hurry: the route stays
exactly as it was and comes back the same, which deleting and recreating it would not guarantee.
`update_route` changes only what it names, and `namespace`/`region` are left out of a create
entirely unless you name them — the server reads an empty string as the empty namespace rather than
as "unspecified", so sending one would scope the route to nothing.

`list_listeners` reports what the gateway was configured to serve and whether it is serving it. A
listener whose port was taken, or whose certificate could not be loaded, is still listed — it is the
one somebody is looking for — and `serving` is what says whether anything is bound. An HTTPS
listener's certificate arrives flat, as a dozen `certificate*` fields, which the SDK gathers into
`Listener.certificate` (or `None` where there is none to report):

```python
for listener in eag.list_listeners().listeners:
    seal = listener.certificate
    print(listener.port, listener.protocol,
          "self-signed" if seal and seal.generated else "issued" if seal else "no certificate")
```

## What ETS covers

`session.ets()` returns the transfer server client. Administrator-only server-side, all of it.

| Method | Action |
| --- | --- |
| `create_server`, `update_server`, `get_server`, `list_servers`, `delete_server` | definitions |
| `start_server`, `stop_server` | running |
| `metrics` | ETS's own metrics |
| `call(action, payload)` | anything the server gained that this SDK has not wrapped yet |

ETS never speaks FTP or SFTP itself. It owns the definitions — which protocol, which port, which
EAM users and groups may log in, which ESM bucket the files really live in — and starting one is
nothing more than writing a desired state onto a definition; euclid's manager turns that into a
running process, which reads its own definition back from here.

So a file uploaded over FTP is an object in a bucket, with the events and the lifecycle every other
object has. This is a protocol somebody's existing tooling already speaks, put in front of storage,
rather than a second place files live.

```python
from euclid.modules.ets import SFTP

ets.create_server("partner-drop", bucket="invoices", port=2222, protocol=SFTP,
                  user_groups=["partners"], home_directory="incoming/")
ets.start_server("partner-drop")
```

`home_directory` is the key prefix a logged-in user lands in, which is what lets one bucket serve
several servers without either seeing the other's files. `host_key` is SFTP's, generated on first
start when left empty — set it only to keep a key clients already trust. `pasv_min`/`pasv_max` are
FTP's passive range, which whatever sits in front of euclid has to let through as well as the
control port.

`TransferServer.namespace` says which namespace a server was defined in — a `server_id` is unique
within an account and a namespace, not across the installation — and `runtime_name` is what its
process, socket and log channel are named after, for the same reason EAP has one.

As in EAP, `desired_state` is what was asked for and `state` is what is observed, and
`update_server` sends only what it names — a named list replaces the stored one rather than adding
to it. The protocol is not updatable and so is not a parameter: which one a server speaks decides
which process runs it, so changing it would be a different server. A running server keeps running on
its old definition until it is restarted, since the process reads it once at startup.

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

### Releasing

Bump `version` in `pyproject.toml` and `__version__` in `src/euclid/__init__.py` - they have to
agree, and `.github/workflows/publish.yml` refuses to publish if they and the tag do not - then tag
and push:

```bash
git tag -a v0.3.0 -m "euclid-pdk 0.3.0"
git push origin main v0.3.0
```

The tag runs the tests again (the test workflow triggers on branches, so a tag push would otherwise
run nothing), builds the wheel and the sdist, checks them with `twine check --strict`, and uploads
to PyPI. `workflow_dispatch` does the same for whatever `main` says, which is what a version whose
tag predates this workflow needs.

Publishing uses PyPI's trusted publishing rather than an API token: PyPI verifies the workflow's
own OIDC identity, so there is no secret to rotate or leak. It has to be configured once, under the
project's *Publishing* settings on PyPI - owner `jensvogt`, repository `euclid-pdk`, workflow
`publish.yml`, environment `pypi` - and for a project that does not exist there yet, as a pending
publisher.

## Licence

Apache License 2.0.
