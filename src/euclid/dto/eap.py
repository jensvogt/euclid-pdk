"""The shapes EAP sends back.

Parsed the same defensive way as every other module's. Field names are the server's, converted to
snake_case - and on this module they are worth reading twice, because a request and the response to
it call some of the same things by different names: an application is deployed from a ``bucket`` and
an ``artifact``, and comes back describing a :attr:`Application.bucket_ern` and an
:attr:`Application.artifact_key`. Names are what an operator has in hand; ERNs are what euclid
stores, and the server resolves the one into the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["Application", "Endpoint", "LogLevelResult"]


@dataclass
class Endpoint:
    """One running instance of an application, and where it can be reached.

    A pool's instances are started and stopped as it grows and shrinks, and each is given a port of
    its own when it starts - so this is a snapshot rather than a setting.
    """

    instance_id: str = ""
    pid: int = 0
    http_port: int = 0

    @staticmethod
    def from_json(document: Any) -> "Endpoint":
        return Endpoint(_json.text(document, "instanceId"), _json.number(document, "pid"),
                        _json.number(document, "httpPort"))


@dataclass
class Application:
    """A deployed application: what euclid runs, as what, and how much of it.

    ``desired_state`` is what somebody asked for - see
    :meth:`~euclid.modules.eap.EuclidEap.start_application` - and ``state`` is what is actually
    running, which is ``RUNNING`` exactly when at least one instance answers. The two differing is
    the ordinary picture of an application starting up, and the lasting picture of one that cannot.

    ``user_id`` is the identity the application runs as. Unless one was named at deployment it is a
    technical principal euclid made for it - no password, no login, one access key - so that nothing
    an application leaks is a person's credential.
    """

    application_id: str = ""
    ern: str = ""
    account_id: str = ""
    region: str = ""
    #: The namespace this application was deployed into, and the one its queue, topic and bucket
    #: names are resolved in. Part of what identifies it: an ``application_id`` is unique within
    #: an account and a namespace, so the ID alone does not say which application this is.
    namespace: str = ""
    #: What the process, its socket and its log channel are named after.
    #:
    #: None of those has an account or a namespace to live in, so they cannot be keyed by an
    #: application ID that two namespaces may each have. Issued once when the application is created
    #: and never touched afterwards - deriving it from the account, namespace and ID instead would
    #: make it change whenever those did, and moving an application would orphan its processes.
    runtime_name: str = ""
    #: ``JAVA``, ``PYTHON``, ``NODEJS`` or ``BINARY`` - see :mod:`euclid.modules.eap`.
    runtime: str = ""
    #: The bucket the artifact was deployed from, as an ERN. Deployed by name.
    bucket_ern: str = ""
    #: The object key of the artifact within that bucket.
    artifact_key: str = ""
    version: str = ""
    #: ESM's checksum of the artifact - the same hash the manager compares the copy on the host
    #: against, and the one a redeploy has to differ from.
    md5_sum: str = ""
    command: str = ""
    arguments: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    #: The ERNs of the buckets and queues this application was granted, resolved from the names it
    #: was deployed with.
    resources: list[str] = field(default_factory=list)
    user_id: str = ""
    #: The level this application logs at, or empty when it is under the configured default.
    log_level: str = ""
    min_instances: int = 0
    max_instances: int = 0
    ready_timeout_ms: int = 0
    #: ``RUNNING`` or ``STOPPED``, as asked for.
    desired_state: str = ""
    #: ``RUNNING`` or ``STOPPED``, as observed.
    state: str = ""
    #: How many instances are running - the length of :attr:`endpoints`.
    instances: int = 0
    endpoints: list[Endpoint] = field(default_factory=list)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Application":
        environment = document.get("environment") if isinstance(document, dict) else None
        return Application(
            _json.text(document, "applicationId"), _json.text(document, "ern"),
            _json.text(document, "accountId"), _json.text(document, "region"),
            _json.text(document, "namespace"), _json.text(document, "runtimeName"),
            _json.text(document, "runtime"), _json.text(document, "bucketErn"),
            _json.text(document, "artifactKey"), _json.text(document, "version"),
            _json.text(document, "md5Sum"), _json.text(document, "command"),
            _json.strings(document, "arguments"),
            {name: value for name, value in environment.items() if isinstance(value, str)}
            if isinstance(environment, dict) else {},
            _json.strings(document, "resources"), _json.text(document, "userId"),
            _json.text(document, "logLevel"), _json.number(document, "minInstances"),
            _json.number(document, "maxInstances"), _json.number(document, "readyTimeoutMs"),
            _json.text(document, "desiredState"), _json.text(document, "state"),
            _json.number(document, "instances"),
            [Endpoint.from_json(e) for e in _json.documents(document, "endpoints")],
            _json.text(document, "created"), _json.text(document, "modified"))

    @property
    def is_running(self) -> bool:
        """Whether anything is actually answering, which is not the same as having been started."""
        return self.state == "RUNNING"


@dataclass
class LogLevelResult:
    """What an application logs at now, and the channel it logs on.

    ``log_level`` empty means the level was taken back rather than changed: the application is under
    whatever the installation's logging configuration says again.
    """

    application_id: str = ""
    log_level: str = ""
    channel: str = ""

    @staticmethod
    def from_json(document: Any) -> "LogLevelResult":
        return LogLevelResult(_json.text(document, "applicationId"), _json.text(document, "logLevel"),
                              _json.text(document, "channel"))
