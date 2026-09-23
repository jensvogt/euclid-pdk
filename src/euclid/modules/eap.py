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

from ..dto.eap import Application, LogLevelResult, RestartResult
from .base import ModuleClient

__all__ = ["EuclidEap", "TARGET", "JAVA", "JAVA21", "JAVA25", "PYTHON", "NODEJS", "BINARY",
           "TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "FATAL", "OFF",
           "DEFAULT_MIN_INSTANCES", "DEFAULT_MAX_INSTANCES", "DEFAULT_READY_TIMEOUT_MS"]

TARGET = "eap"

#: What an artifact is handed to. Matched exactly, in upper case, and anything else is refused with
#: HTTP 400 - :data:`JAVA21` is a runtime, ``java21`` and ``JAVA 21`` are typos.
#:
#: :data:`JAVA` is whichever java the host calls java, which is what every application deployed
#: before the versioned ones said. :data:`JAVA21` and :data:`JAVA25` name a version and are started
#: with the executable that host has configured for it - a jar built for 25 does not start on 21,
#: and leaving it to whichever java resolved first made the version an accident of the manager's
#: PATH.
JAVA = "JAVA"
JAVA21 = "JAVA21"
JAVA25 = "JAVA25"
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

        :param runtime: :data:`JAVA`, :data:`JAVA21`, :data:`JAVA25`, :data:`PYTHON`,
            :data:`NODEJS` or :data:`BINARY`.
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

    def copy_application(self, application_id: str, target_namespace: str,
                         target_application_id: str | None = None) -> Application:
        """Defines the same application again in another namespace, leaving the original running.

        The sibling of moving one with ``update_application(namespace=...)``, and the difference is
        the point: a move takes the definition with it, so what ran in the old namespace stops
        running there. A copy is how a build is promoted - development to integration, integration
        to production - while the namespace it came from goes on serving.

        The copy runs the same artifact, down to the checksum, so it is the same bytes rather than
        a rebuild that happens to share a version. It is given its own runtime name and its own
        technical principal with its own access key, both being installation-wide and unshareable,
        so revoking the copy's credentials leaves the original running. An application told to run
        as a named user keeps that user.

        What it may reach is re-resolved rather than copied: a bucket or queue ERN carries the
        namespace it was resolved in, so copying the list would point the new application at the
        old namespace's data. The same names are looked up in the target namespace, and one with no
        counterpart there fails the copy with HTTP 404 rather than quietly leaving the application
        with less access than the original.

        The copy is created stopped whatever the original is doing - a copy that started itself
        would put a second consumer on the target namespace's queues the moment this returned.

        :param application_id: the application to copy, in the namespace this session works in
        :param target_namespace: the namespace to copy it into; it has to exist already
        :param target_application_id: the name the copy is defined under, or None for the
            original's. Naming it is how an application is copied beside itself in one namespace.
        """
        payload: dict[str, Any] = {"applicationId": application_id,
                                   "targetNamespace": target_namespace}
        if target_application_id is not None:
            payload["targetApplicationId"] = target_application_id
        return self._application("copy-application", payload)

    def scale_application(self, application_id: str, min_instances: int | None = None,
                          max_instances: int | None = None) -> Application:
        """Changes how many instances an application runs, without restarting the ones it has.

        :meth:`update_application` can set the same two fields, but it writes the whole definition
        and stamps the modification date - and the manager restarts a pool whose application
        changed since it started it. Scaling that way stops every running instance and starts it
        again, which is the opposite of what asking for capacity means and worst at the moment it
        is asked for.

        What is set is the range the autoscaler works within, not a count: the manager scales
        toward it on its next reconcile, adding instances one at a time and stopping idle ones as
        the load allows. Nothing is started or stopped by this call. Passing the same number for
        both pins the pool at that size and leaves the autoscaler nothing to decide.

        A bound left as None is left as it stands, so a ceiling can be raised without touching the
        floor. The two are checked against each other as they *will* stand rather than as they are,
        so raising only the floor is refused with HTTP 400 when it would pass the stored ceiling. A
        floor of zero is refused for its own reason: an application desired RUNNING with no
        instances reads everywhere as a pool that failed to start, and :meth:`stop_application` is
        how one is taken out of service.

        :param application_id: the application to scale
        :param min_instances: smallest number of instances to keep running, or None to leave it
        :param max_instances: largest number the autoscaler may run, or None to leave it
        """
        payload: dict[str, Any] = {"applicationId": application_id}
        if min_instances is not None:
            payload["minInstances"] = min_instances
        if max_instances is not None:
            payload["maxInstances"] = max_instances
        return self._application("scale-application", payload)

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

    def restart_application(self, application_id: str) -> RestartResult:
        """Asks for a running application's instances to be started again.

        The manager stops the whole pool on its next reconcile and starts it straight back up from
        the current definition - the same thing it does after a redeploy, with nothing new to pick
        up. The artifact, the environment and the credentials all come back as they were, so this
        is for an instance that has to do its startup again rather than a way to deploy anything.

        Deliberately not :meth:`stop_application` followed by :meth:`start_application`: between
        those two the desired state is ``STOPPED``, so a caller that fails in between leaves the
        application down. Here it stays ``RUNNING`` throughout, and one that is already stopped is
        refused with HTTP 400 rather than started.
        """
        return RestartResult.from_json(self._call("restart-application", {"applicationId": application_id}))

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
