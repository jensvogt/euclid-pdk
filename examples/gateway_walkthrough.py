#!/usr/bin/env python3
"""EAG end to end: what the gateway publishes, and the ports it publishes it on.

    python examples/gateway_walkthrough.py https://euclid.example.com jens secret

Administrator-only, all of it - the server refuses every action here to anybody else. Publishes one
module route of its own, named after the moment it started, and deletes it again at the end, so it
is safe to point at a running server. The application route is only described, not created: that
would need an application to point at.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid.modules.eag import EUCLID_AUTH


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    base_url, username, password = argv[1], argv[2], argv[3]

    try:
        session = (Euclid.for_server(base_url)
                   .access()
                   .credentials(username, password)
                   # A development server's certificate is usually its own; drop this line, or
                   # point ca_cert_path() at the real CA, anywhere it matters.
                   .verify(False)
                   .login())
    except EuclidAuthenticationError as error:
        print(f"login refused: {error.reason or error}")
        return 1

    with session:
        if not session.is_admin:
            print(f"{session.user_id} is not an administrator - every EAG action would be refused")
            return 1

        eag = session.eag()
        listeners(eag)

        route_id = f"pdk-walkthrough-{int(time.time())}"
        try:
            walk(eag, route_id)
        finally:
            try:
                eag.delete_route(route_id)
                print(f"\ndeleted route {route_id}")
            except EuclidServiceError as error:
                print(f"\ncould not delete route {route_id}: {error.reason or error}")

    return 0


def listeners(eag) -> None:
    """What the gateway was configured to serve, and whether it is serving it."""
    result = eag.list_listeners()
    print(f"{result.total} listener(s), gateway serving: {result.serving}")

    for listener in result.listeners:
        scope = listener.namespace or "(every namespace)"
        print(f"  {listener.protocol}:{listener.port:<6} {scope}")

        if listener.protocol != "https":
            continue
        named = "named in the configuration" if listener.names_certificate else "the conventional one"
        print(f"      certificate {listener.certificate_name!r} ({named})")

        seal = listener.certificate
        if seal is None:
            # For an HTTPS listener this is what a port that never came up looks like: the
            # certificate is generated when it starts.
            print("      no certificate found - this port is not serving anything")
            continue
        origin = "self-signed by euclid" if seal.generated else "issued by " + seal.issuer
        print(f"      {seal.subject}, {origin}")
        print(f"      valid {seal.not_before} to {seal.not_after}"
              f"{'  (EXPIRED)' if seal.expired else ''}")
        if seal.subject_alt_names:
            print(f"      also valid for {seal.subject_alt_names}")


def walk(eag, route_id: str) -> None:
    """Publishing a path, changing it, and taking it out of service."""
    # A module route rather than an application route: it needs nothing deployed to point at.
    # This is what a browser uses to log in before it can call anything else.
    route = eag.create_module_route(route_id, f"/{route_id}/login", "eam", "login",
                                    methods=["POST"])
    print(f"\npublished {route.path} -> {route.module_target}/{route.module_action}")
    print(f"  ern: {route.ern}")
    print(f"  scope: {route.namespace or '(none)'}/{route.region}, "
          f"authentication: {route.authentication}, methods: {route.methods or 'every method'}")

    # Only what is named changes: the path, the module and the methods stay as they are.
    protected = eag.update_route(route_id, authentication=EUCLID_AUTH)
    print(f"\nrequired a euclid credential: authentication is now {protected.authentication}")

    # How something stops being exposed in a hurry - the route stays exactly as it was.
    stopped = eag.set_route_active(route_id, False)
    print(f"took it out of service: active={stopped.active}, and the path is still {stopped.path}")
    print(f"put it back: active={eag.set_route_active(route_id, True).active}")

    print(f"\nread back by ID: {eag.get_route(route_id).path}")

    published = eag.list_routes()
    print(f"\n{len(published)} route(s) published:")
    for listed in published:
        where = (f"{listed.module_target}/{listed.module_action}" if listed.is_module_route
                 else listed.application_id)
        methods = ",".join(listed.methods) if listed.methods else "ANY"
        print(f"  {listed.path:<40} {methods:<20} -> {where:<24} "
              f"{listed.authentication:<7} {'' if listed.active else '(inactive)'}")

    under = eag.list_routes(f"/{route_id}")
    print(f"\npublished under /{route_id}: {[r.path for r in under]}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
