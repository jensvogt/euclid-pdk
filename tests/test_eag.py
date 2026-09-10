"""EAG, end to end against a fake euclid server.

Every action here is one request, so what these check is that it carries the fields the server reads
and parses the ones it answers with. Two of those matter more than the rest: an optional field that
travels as an empty string is not "unspecified" to this server - it is the empty value - and a
listener's certificate arrives flat, as a dozen fields alongside the listener's own, which the
client has to gather back up.
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.modules.eag import BASIC_AUTH, EUCLID_AUTH, HTTPS, NO_AUTH
from test_eam import prepared

ROUTE = {"routeId": "orders", "ern": "ern:eag:route/orders", "accountId": "000000000000",
         "region": "eu-central-1", "namespace": "development", "path": "/api/orders",
         "applicationId": "order-service", "moduleTarget": "", "moduleAction": "",
         "methods": ["GET", "POST"], "authentication": "EUCLID", "active": True,
         "created": "2026-09-10", "modified": "2026-09-10"}

MODULE_ROUTE = dict(ROUTE, routeId="login", path="/euclid/login", applicationId="",
                    moduleTarget="eam", moduleAction="login", authentication="NONE")


@pytest.fixture
def eag(gateway):
    """An EAG client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.eag()


# -- routes ---------------------------------------------------------------------------------------


def test_publishing_a_path_to_an_application(gateway, eag):
    gateway.answer("eag", "create-route", ROUTE)

    route = eag.create_route("orders", "/api/orders", "order-service",
                             methods=["GET", "POST"], authentication=EUCLID_AUTH)

    assert gateway.last().json() == {"routeId": "orders", "path": "/api/orders",
                                     "methods": ["GET", "POST"], "authentication": "EUCLID",
                                     "active": True, "applicationId": "order-service"}
    assert (route.path, route.application_id, route.methods) == ("/api/orders", "order-service",
                                                                 ["GET", "POST"])
    assert route.authentication == "EUCLID" and route.active
    assert not route.is_module_route


def test_publishing_a_path_to_a_euclid_module(gateway, eag):
    """The way in for something outside euclid that needs euclid itself - a browser that has to log
    in before it can call anything."""
    gateway.answer("eag", "create-route", MODULE_ROUTE)

    route = eag.create_module_route("login", "/euclid/login", "eam", "login", methods=["POST"])

    assert gateway.last().json() == {"routeId": "login", "path": "/euclid/login",
                                     "methods": ["POST"], "authentication": "NONE", "active": True,
                                     "moduleTarget": "eam", "moduleAction": "login"}
    assert route.is_module_route
    assert (route.module_target, route.module_action) == ("eam", "login")


def test_a_route_goes_to_one_thing_or_the_other(gateway, eag):
    """Never both and never neither: those are reached in entirely different ways, and a route that
    named both would leave which one wins up to the proxy."""
    with pytest.raises(ValueError, match="not both and not neither"):
        eag.create_route("orders", "/api/orders")
    with pytest.raises(ValueError, match="not both and not neither"):
        eag.create_route("orders", "/api/orders", "order-service", module_target="eam",
                         module_action="login")
    with pytest.raises(ValueError, match="module_action is required"):
        eag.create_route("login", "/euclid/login", module_target="eam")

    assert [r for r in gateway.requests if r.target == "eag"] == []


def test_the_optional_scope_is_left_out_rather_than_sent_empty(gateway, eag):
    """The server reads an empty namespace as the empty namespace rather than as "unspecified", so
    sending one would scope the route to nothing instead of to the session."""
    gateway.answer("eag", "create-route", ROUTE)

    eag.create_route("orders", "/api/orders", "order-service")
    assert "namespace" not in gateway.last().json()
    assert "region" not in gateway.last().json()

    eag.create_route("orders", "/api/orders", "order-service", namespace="production",
                     region="eu-west-1")
    assert gateway.last().json()["namespace"] == "production"
    assert gateway.last().json()["region"] == "eu-west-1"


def test_no_methods_means_every_method(gateway, eag):
    gateway.answer("eag", "create-route", dict(ROUTE, methods=[]))

    route = eag.create_route("orders", "/api/orders", "order-service")

    assert gateway.last().json()["methods"] == []
    assert route.methods == []


def test_an_update_sends_only_what_it_was_given(gateway, eag):
    gateway.answer("eag", "update-route", ROUTE)

    eag.update_route("orders", path="/api/v2/orders")
    assert gateway.last().json() == {"routeId": "orders", "path": "/api/v2/orders"}

    eag.update_route("orders", authentication=BASIC_AUTH, methods=["GET"])
    assert gateway.last().json() == {"routeId": "orders", "authentication": "BASIC",
                                     "methods": ["GET"]}

    # Moving a route to an application clears its module target, which is the server's doing - the
    # client just has to be able to say "this one field".
    eag.update_route("orders", application_id="order-service-v2")
    assert gateway.last().json() == {"routeId": "orders", "applicationId": "order-service-v2"}

    # An empty string is a value here rather than "leave it alone", and has to be sendable.
    eag.update_route("orders", module_action="")
    assert gateway.last().json() == {"routeId": "orders", "moduleAction": ""}


def test_taking_a_route_out_of_service_changes_nothing_else(gateway, eag):
    """Which is what makes it different from deleting and recreating it."""
    gateway.answer("eag", "update-route", dict(ROUTE, active=False))

    route = eag.set_route_active("orders", False)

    assert gateway.last().json() == {"routeId": "orders", "active": False}
    assert route.active is False


def test_listing_getting_and_deleting_routes(gateway, eag):
    gateway.answer("eag", "list-routes", {"routes": [ROUTE, MODULE_ROUTE]})
    gateway.answer("eag", "get-route", ROUTE)
    gateway.answer("eag", "delete-route", {})

    routes = eag.list_routes("/api")
    assert gateway.last().json() == {"prefix": "/api"}
    assert [route.route_id for route in routes] == ["orders", "login"]
    assert [route.is_module_route for route in routes] == [False, True]

    assert eag.list_routes() == routes
    assert gateway.last().json() == {"prefix": ""}

    assert eag.get_route("orders").path == "/api/orders"
    assert gateway.last().json() == {"routeId": "orders"}

    eag.delete_route("orders")
    assert gateway.last().json() == {"routeId": "orders"}


def test_a_taken_path_is_refused_with_the_servers_reason(gateway, eag):
    gateway.answer("eag", "create-route",
                   {"error": "Path and method are already routed by routeId: orders"}, status=409)

    with pytest.raises(EuclidServiceError) as raised:
        eag.create_route("orders-2", "/api/orders", "order-service")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("eag", "create-route", 409)
    assert raised.value.reason.startswith("Path and method are already routed")


def test_a_route_the_server_described_sparsely_still_reads(gateway, eag):
    gateway.answer("eag", "get-route", {"routeId": "orders", "path": "/api/orders"})

    route = eag.get_route("orders")

    assert route.methods == [] and route.authentication == ""
    # Absent means serving, which is the server's default for a stored route.
    assert route.active is True


# -- listeners --------------------------------------------------------------------------------------


LISTENER = {"namespace": "development", "port": 8443, "protocol": "https", "serving": True,
            "certificate": "development-gateway", "certificateConfigured": "",
            "certificateFound": True, "certificateErn": "ern:ekm:certificate/development-gateway",
            "certificateSubject": "CN=euclid.example.com", "certificateIssuer": "CN=euclid.example.com",
            "certificateSerialNumber": "01", "certificateFingerprint": "ab:cd",
            "certificateSubjectAltNames": ["euclid.example.com", "localhost"],
            "certificateGenerated": True, "certificateNotBefore": "2026-01-01",
            "certificateNotAfter": "2028-04-05", "certificateExpired": False}

PLAIN_LISTENER = {"namespace": "", "port": 8080, "protocol": "http", "serving": True,
                  "certificate": "", "certificateConfigured": "", "certificateFound": False}


def test_listing_listeners_gathers_the_certificate_back_up(gateway, eag):
    gateway.answer("eag", "list-listeners", {"listeners": [LISTENER, PLAIN_LISTENER],
                                             "total": 2, "serving": True})

    result = eag.list_listeners()

    assert gateway.last().json() == {}
    assert (result.total, result.serving) == (2, True)

    https, plain = result.listeners
    assert (https.port, https.protocol, https.namespace) == (8443, HTTPS, "development")
    assert https.certificate is not None
    assert https.certificate.subject == "CN=euclid.example.com"
    assert https.certificate.subject_alt_names == ["euclid.example.com", "localhost"]
    # Whether euclid minted it itself, which is what decides whether the port works for anybody who
    # has not been told about it.
    assert https.certificate.generated and not https.certificate.expired

    # This listener took the conventional certificate for its namespace rather than naming one.
    assert https.certificate_name == "development-gateway"
    assert not https.names_certificate

    # A plain HTTP listener has no certificate to be missing, and reporting one as absent would read
    # as a fault rather than a setting.
    assert plain.certificate is None


def test_a_listener_that_named_its_certificate_says_so(gateway, eag):
    gateway.answer("eag", "list-listeners", {"listeners": [
        dict(LISTENER, certificateConfigured="wildcard-2026")], "total": 1, "serving": True})

    listener = eag.list_listeners().listeners[0]

    assert listener.names_certificate
    assert listener.certificate_configured == "wildcard-2026"


def test_a_port_that_never_came_up_is_still_listed(gateway, eag):
    """It is the one somebody is looking for - and an HTTPS listener with no certificate is what
    that looks like."""
    gateway.answer("eag", "list-listeners", {"listeners": [
        dict(LISTENER, serving=False, certificateFound=False)], "total": 1, "serving": False})

    result = eag.list_listeners()

    assert result.serving is False
    assert result.listeners[0].protocol == HTTPS
    assert result.listeners[0].certificate is None


# -- everything else ----------------------------------------------------------------------------------


def test_eag_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("eag", "list-routes", {"routes": []})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        eag = session.eag()
        eag.list_routes()
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "eag"

        session.change_namespace("development")
        eag.list_routes()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.eag() is eag


def test_an_administrator_only_action_says_who_refused_it(gateway, eag):
    """Every EAG action is administrator-only server-side, and the server enforces it whatever the
    session believes about itself."""
    gateway.answer("eag", "create-route", {"error": "Administrator rights required"}, status=403)

    with pytest.raises(EuclidServiceError) as raised:
        eag.create_route("orders", "/api/orders", "order-service", authentication=NO_AUTH)

    assert raised.value.status == 403
    assert raised.value.reason == "Administrator rights required"


def test_metrics_and_call(gateway, eag):
    gateway.answer("eag", "get-metrics", {"items": [{"name": "eag-requests", "value": 3}]})
    gateway.answer("eag", "some-future-action", {"ok": True})

    assert eag.metrics() == {"items": [{"name": "eag-requests", "value": 3}]}
    assert eag.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
