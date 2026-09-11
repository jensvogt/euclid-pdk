"""EAP - euclid's application platform: what euclid runs, from what, and as whom.

One object, :class:`EuclidEap`, built from a session that has already logged in::

    eap = Euclid.for_server(url).login("jens", "secret").eap()

    eap.create_application("order-service", JAVA, bucket="artifacts",
                           artifact="order-service-1.4.0.jar", queues=["orders"])
    eap.start_application("order-service")

An application is deployed from an artifact already in a bucket - ESM puts it there, and EAP names
it. The deployment says which buckets and queues it may reach, and euclid grants those to the
identity it runs as: a technical principal it creates for the application unless one is named, with
no password, no login and one access key. Nothing an application leaks is then a person's credential.

Two names for the same things, and the asymmetry is the server's: a deployment names a ``bucket``
and an ``artifact``, and the application that comes back describes a ``bucket_ern`` and an
``artifact_key``. Likewise the ``buckets`` and ``queues`` it is granted come back resolved into
``resources``.

Every action here is administrator-only, server-side. :attr:`~euclid.EuclidSession.is_admin` says
whether the logged-in user is one, though the server enforces it regardless.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..dto.eap import Application, LogLevelResult
from .base import ModuleClient

__all__ = ["EuclidEap", "TARGET", "JAVA", "PYTHON", "NODEJS", "BINARY",
           "TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "FATAL", "OFF",
           "DEFAULT_MIN_INSTANCES", "DEFAULT_MAX_INSTANCES", "DEFAULT_READY_TIMEOUT_MS"]

TARGET = "eap"

#: What an artifact is handed to. Matched exactly, in upper case, and anything else is refused with
#: HTTP 400 - a runtime is a category rather than a version, so a JDK 17 and a JDK 25 application
#: are both :data:`JAVA` and it is the command or the PATH that decides which one runs.
JAVA = "JAVA"
PYTHON = "PYTHON"
NODEJS = "NODEJS"
#: Anything already executable, which is where C++ and Rust applications land.
BINARY = "BINARY"

#: The levels :meth:`EuclidEap.set_log_level` accepts. An unrecognised one is refused rather than
#: defaulted: "warnign" quietly meaning "info" is an application logging more than somebody asked
#: for, and quietly meaning "off" is silence nobody asked for at all.
TRACE = "trace"
DEBUG = "debug"
INFO = "info"
WARNING = "warning"
ERROR = "error"
FATAL = "fatal"
OFF = "off"

#: What a pool is sized at unless the deployment says otherwise.
DEFAULT_MIN_INSTANCES = 1
DEFAULT_MAX_INSTANCES = 1

#: How long an instance has to become ready before the manager gives up on it, in milliseconds.
DEFAULT_READY_TIMEOUT_MS = 30000


class EuclidEap(ModuleClient):
    """EAP's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.eap` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- deploying ---------------------------------------------------------------------------

    def create_application(self, application_id: str, runtime: str, bucket: str, artifact: str,
                           version: str = "", command: str = "", arguments: Iterable[str] = (),
                           environment: Mapping[str, str] | None = None,
                           buckets: Iterable[str] = (), queues: Iterable[str] = (), user: str = "",
                           min_instances: int = DEFAULT_MIN_INSTANCES,
                           max_instances: int = DEFAULT_MAX_INSTANCES,
                           ready_timeout_ms: int = DEFAULT_READY_TIMEOUT_MS) -> Application:
        """Deploys an application, stopped, and returns it as it was stored.

        Nothing runs yet: a new application's desired state is ``STOPPED``, so
        :meth:`start_application` is what puts it in service. Refused with HTTP 409 if the ID is
        taken, and with 404 if the bucket, the artifact, a named resource or a named user is not
        there - a deployment pointing at nothing would otherwise become an application that fails
        to start for a reason nobody can see.

        The application ID is unique within the account and the namespace it is deployed into,
        rather than across the installation: two namespaces may each deploy a "billing".

        :param runtime: :data:`JAVA`, :data:`PYTHON`, :data:`NODEJS` or :data:`BINARY`.
        :param bucket: the name of the bucket holding the artifact - a name, not an ERN.
        :param artifact: the artifact's object key within that bucket.
        :param version: what to record as the deployed version. Left empty, the server reads it out
            of the artifact's name, and refuses the deployment if it cannot.
        :param command: what to run, when the runtime's own interpreter is not it. Empty means the
            runtime decides, resolved through PATH.
        :param arguments: what follows the command.
        :param environment: the environment the process is given.
        :param buckets: the buckets this application may reach, by name; euclid resolves them and
            grants them to the identity it runs as.
        :param queues: likewise for queues.
        :param user: an existing user to run as. Left empty, euclid creates a technical principal
            for the application - which is the better answer, and why this is not required.
        :param min_instances: the smallest the pool goes; at least 1.
        :param max_instances: the largest it goes; never below ``min_instances``.
        :param ready_timeout_ms: how long an instance has to become ready; at least 1000.
        """
        return self._application("create-application", {
            "applicationId": application_id, "runtime": runtime, "bucket": bucket,
            "artifact": artifact, "version": version, "command": command,
            "arguments": list(arguments), "environment": dict(environment or {}),
            "buckets": list(buckets), "queues": list(queues), "user": user,
            "minInstances": min_instances, "maxInstances": max_instances,
            "readyTimeoutMs": ready_timeout_ms})

    def update_application(self, application_id: str, runtime: str | None = None,
                           artifact: str | None = None, version: str | None = None,
                           command: str | None = None, arguments: Iterable[str] | None = None,
                           environment: Mapping[str, str] | None = None,
                           buckets: Iterable[str] | None = None, queues: Iterable[str] | None = None,
                           min_instances: int | None = None, max_instances: int | None = None,
                           ready_timeout_ms: int | None = None,
                           namespace: str | None = None) -> Application:
        """Changes a deployed application. Only what this names changes.

        The distinction the server draws is between a field being sent and not being sent, rather
        than between its values - so leaving ``command`` as None leaves the stored command alone,
        while passing ``""`` clears it and hands the artifact back to the runtime's own interpreter.

        ``buckets`` and ``queues`` are re-resolved together whenever either is named, so naming one
        and not the other revokes what the other used to grant. Pass both, or neither.

        Naming a ``namespace`` is a move rather than a field change: the namespace is part of what
        identifies an application, and it is where the buckets and queues it may reach are resolved.
        So the resources are re-resolved in the namespace it is moving *to*, and the move is refused
        with HTTP 409 if an application of this ID already lives there.

        Changing the artifact is a change of what will run next; :meth:`redeploy_application` is
        what a new build of the same application usually wants.
        """
        payload: dict[str, Any] = {"applicationId": application_id}
        if runtime is not None:
            payload["runtime"] = runtime
        if artifact is not None:
            payload["artifact"] = artifact
        if version is not None:
            payload["version"] = version
        if command is not None:
            payload["command"] = command
        if arguments is not None:
            payload["arguments"] = list(arguments)
        if environment is not None:
            payload["environment"] = dict(environment)
        if buckets is not None:
            payload["buckets"] = list(buckets)
        if queues is not None:
            payload["queues"] = list(queues)
        if min_instances is not None:
            payload["minInstances"] = min_instances
        if max_instances is not None:
            payload["maxInstances"] = max_instances
        if ready_timeout_ms is not None:
            payload["readyTimeoutMs"] = ready_timeout_ms
        if namespace is not None:
            payload["namespace"] = namespace
        return self._application("update-application", payload)

    def redeploy_application(self, application_id: str, artifact: str = "",
                             version: str = "") -> Application:
        """Points an application at a new build of itself.

        The artifact defaults to the one already deployed - which is what a rebuilt artifact stored
        under the same key wants - and the version to whatever the artifact's name says. A redeploy
        that would change neither the version nor the checksum is refused with HTTP 409: it would
        restart the instances for nothing, and usually means the new artifact never reached the
        bucket.
        """
        payload: dict[str, Any] = {"applicationId": application_id}
        if artifact:
            payload["artifact"] = artifact
        if version:
            payload["version"] = version
        return self._application("redeploy-application", payload)

    def delete_application(self, application_id: str) -> None:
        """Removes an application. Stop it first - this does not."""
        self._call("delete-application", {"applicationId": application_id})

    # -- running -----------------------------------------------------------------------------

    def start_application(self, application_id: str) -> Application:
        """Asks for an application to run, and returns it as it stands.

        Asking is all this does: the desired state changes here and the manager acts on it, so the
        application in the answer is usually still ``STOPPED`` - it says what was asked for, not
        what has happened yet.
        """
        return self._application("start-application", {"applicationId": application_id})

    def stop_application(self, application_id: str) -> Application:
        """Asks for an application to stop, and returns it as it stands."""
        return self._application("stop-application", {"applicationId": application_id})

    def list_applications(self, prefix: str = "") -> list[Application]:
        """The applications whose ID starts with a prefix; an empty prefix lists them all."""
        applications = self._call("list-applications", {"prefix": prefix}).get("applications")
        return [Application.from_json(a) for a in applications] if isinstance(applications, list) else []

    def get_application(self, application_id: str) -> Application:
        """One application, by its ID, with the instances that are answering for it."""
        return self._application("get-application", {"applicationId": application_id})

    # -- logging -----------------------------------------------------------------------------

    def set_log_level(self, application_id: str, level: str) -> LogLevelResult:
        """Sets what one application logs at, without restarting or redeploying it.

        :param level: :data:`TRACE`, :data:`DEBUG`, :data:`INFO`, :data:`WARNING`, :data:`ERROR`,
            :data:`FATAL` or :data:`OFF`. An empty one takes the setting back - see
            :meth:`reset_log_level`, which says that in a word.
        """
        return LogLevelResult.from_json(self._call("set-log-level", {
            "applicationId": application_id, "level": level}))

    def reset_log_level(self, application_id: str) -> LogLevelResult:
        """Puts an application back under the installation's own logging configuration.

        Which is not the same as setting it to whatever that configuration says: this removes the
        override, so the application follows the configuration as it changes from here on.
        """
        return self.set_log_level(application_id, "")

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EAP's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EAP."""
        return self._call("get-metrics")

    def _application(self, action: str, payload: Mapping[str, Any]) -> Application:
        """The actions that answer with one application, which is most of them."""
        return Application.from_json(self._call(action, payload))
