"""EAG - euclid's API gateway: the paths it publishes, and the ports it publishes them on.

One object, :class:`EuclidEag`, built from a session that has already logged in::

    eag = Euclid.for_server(url).login("jens", "secret").eag()

    eag.create_route("orders", "/api/orders", application_id="order-service")
    eag.create_module_route("login", "/euclid/login", "eam", "login")

A route publishes a path prefix and says where everything beneath it goes: to an application euclid
runs, or to one action of a euclid module. It is one or the other, never both and never neither -
those are reached in entirely different ways, and a route that named both would leave which one wins
up to the proxy.

Module routes are the way in for something outside euclid that needs euclid itself - a browser that
has to log in before it can call anything. Without one, a front end would talk to the API gateway for
the application and to euclid's own gateway for its credentials: two ports, two origins, and CORS
between them.

Every action here is administrator-only, server-side. :attr:`~euclid.EuclidSession.is_admin` says
whether the logged-in user is one, though the server enforces it regardless.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..dto.eag import ListListenersResult, Route
from .base import ModuleClient

__all__ = ["EuclidEag", "TARGET", "NO_AUTH", "EUCLID_AUTH", "BASIC_AUTH", "HTTP", "HTTPS"]

TARGET = "eag"

#: Proxied as it arrives: whatever the application requires, it enforces itself.
NO_AUTH = "NONE"
#: A euclid credential is required and verified before anything is forwarded - whatever euclid's own
#: gateway accepts, which is a bearer token, an RFC 9421 signature or SigV4.
EUCLID_AUTH = "EUCLID"
#: HTTP Basic against a euclid user's password, for the callers a euclid credential does not suit: a
#: browser, which prompts when it is answered with ``WWW-Authenticate``, and a script with nothing
#: but curl.
BASIC_AUTH = "BASIC"

#: What a listener speaks, as :class:`~euclid.dto.eag.Listener` reports it.
HTTP = "http"
HTTPS = "https"


class EuclidEag(ModuleClient):
    """EAG's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.eag` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- routes ------------------------------------------------------------------------------

    def create_route(self, route_id: str, path: str, application_id: str = "",
                     module_target: str = "", module_action: str = "",
                     methods: Iterable[str] = (), authentication: str = NO_AUTH,
                     active: bool = True, namespace: str = "", region: str = "") -> Route:
        """Publishes a path, and sends everything beneath it to an application or to a module.

        Refused with HTTP 409 if the route ID is taken, or if another route already answers for this
        path and one of these methods. An application that does not exist is refused with 404 rather
        than becoming a route that answers 503 for every request - which looks like an application
        that is down rather than one that was never deployed.

        :param route_id: the name to manage this route under, unique within the account and the
            namespace it is published in - two namespaces may each have an "orders" route.
        :param path: the path prefix to publish, which has to start with ``/``.
        :param application_id: the application requests are sent to, which has to exist already.
        :param module_target: the euclid module to reach instead, e.g. ``"eam"`` - see
            :meth:`create_module_route`, which is this with the pair spelled out.
        :param module_action: the one action that module answers for on this route.
        :param methods: the HTTP methods this route answers for; none means every method.
        :param authentication: :data:`NO_AUTH`, :data:`EUCLID_AUTH` or :data:`BASIC_AUTH`.
        :param active: whether the gateway serves it from the start.
        :param namespace: the namespace requests carried by this route act in. Left empty, the
            session's own - which is almost always what is meant, and nameable because it is not
            always: a route published for one namespace should not act in another just because an
            administrator of the first happened to configure it.
        :param region: likewise, defaulting to the session's.
        :raises ValueError: if neither an application nor a module was named, or both were. The
            server refuses that too; this just says so before the round trip.
        """
        if bool(application_id) == bool(module_target):
            raise ValueError("name either an application_id or a module_target, not both and not neither")
        if module_target and not module_action:
            raise ValueError("module_action is required when a module_target is named")

        payload: dict[str, Any] = {
            "routeId": route_id, "path": path, "methods": list(methods),
            "authentication": authentication, "active": active}
        # Only when the caller named one: the server reads an empty string as the empty namespace
        # rather than as "unspecified", so sending one would scope the route to nothing.
        if application_id:
            payload["applicationId"] = application_id
        if module_target:
            payload["moduleTarget"] = module_target
            payload["moduleAction"] = module_action
        if namespace:
            payload["namespace"] = namespace
        if region:
            payload["region"] = region
        return Route.from_json(self._call("create-route", payload))

    def create_module_route(self, route_id: str, path: str, module_target: str, module_action: str,
                            methods: Iterable[str] = (), authentication: str = NO_AUTH,
                            active: bool = True, namespace: str = "", region: str = "") -> Route:
        """Publishes a path that reaches one action of a euclid module rather than an application.

        The same call as :meth:`create_route` with the module pair required rather than optional,
        because that pair is what makes a module route: a target with no action gives the gateway
        nothing to dispatch on, and would answer 400 for every request the route ever carries.
        """
        return self.create_route(route_id, path, module_target=module_target,
                                 module_action=module_action, methods=methods,
                                 authentication=authentication, active=active,
                                 namespace=namespace, region=region)

    def update_route(self, route_id: str, path: str | None = None, application_id: str | None = None,
                     module_target: str | None = None, module_action: str | None = None,
                     methods: Iterable[str] | None = None, authentication: str | None = None,
                     active: bool | None = None, namespace: str | None = None,
                     region: str | None = None) -> Route:
        """Changes a route that already exists. Only what this names changes.

        The distinction the server draws is between a field being sent and not being sent, rather
        than between its values - so leaving ``path`` as None leaves the stored path alone. Moving a
        route to an application clears its module target and the other way round, since leaving both
        set would make which one wins depend on the proxy.
        """
        payload: dict[str, Any] = {"routeId": route_id}
        if path is not None:
            payload["path"] = path
        if application_id is not None:
            payload["applicationId"] = application_id
        if module_target is not None:
            payload["moduleTarget"] = module_target
        if module_action is not None:
            payload["moduleAction"] = module_action
        if methods is not None:
            payload["methods"] = list(methods)
        if authentication is not None:
            payload["authentication"] = authentication
        if active is not None:
            payload["active"] = active
        if namespace is not None:
            payload["namespace"] = namespace
        if region is not None:
            payload["region"] = region
        return Route.from_json(self._call("update-route", payload))

    def set_route_active(self, route_id: str, active: bool) -> Route:
        """Takes a route out of service, or puts it back, without changing anything else about it.

        This is how something stops being exposed in a hurry: the route stays exactly as it was and
        comes back the same when it is reactivated, which deleting and recreating it would not
        guarantee.
        """
        return self.update_route(route_id, active=active)

    def list_routes(self, path_prefix: str = "") -> list[Route]:
        """The routes whose path starts with a prefix - what somebody asking "what is published
        under ``/api``" wants. An empty prefix lists them all.

        The prefix filters on the path rather than on the route ID, and is matched literally rather
        than as a pattern, so ``/api/v1.0`` does not also match ``/api/v1X0``.
        """
        routes = self._call("list-routes", {"prefix": path_prefix}).get("routes")
        return [Route.from_json(route) for route in routes] if isinstance(routes, list) else []

    def get_route(self, route_id: str) -> Route:
        """One route, by its ID."""
        return Route.from_json(self._call("get-route", {"routeId": route_id}))

    def delete_route(self, route_id: str) -> None:
        """Deletes a route, which stops the gateway serving its path.

        Deleting is not how something is taken out of service temporarily - see
        :meth:`set_route_active`, which leaves the route as it was so it returns exactly the same.
        """
        self._call("delete-route", {"routeId": route_id})

    # -- listeners ---------------------------------------------------------------------------

    def list_listeners(self) -> ListListenersResult:
        """The ports the gateway was configured to answer on, and whether it is answering.

        A listener whose port was taken, or whose certificate could not be loaded, is still listed -
        it is the one somebody is looking for - and
        :attr:`~euclid.dto.eag.ListListenersResult.serving` is what says whether anything is bound.
        For an HTTPS listener the certificate comes with it, including whether euclid minted it
        itself.
        """
        return ListListenersResult.from_json(self._call("list-listeners"))

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EAG's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EAG."""
        return self._call("get-metrics")
