#!/usr/bin/env python3
"""EAP: what euclid is running, from what, and as whom.

    python examples/applications_walkthrough.py https://euclid.example.com jens secret
    python examples/applications_walkthrough.py https://euclid.example.com jens secret order-service

Read-only unless an application is named. Deploying one needs an artifact already in a bucket, which
is not something an example should invent on somebody's server - so this reads what is deployed, and
shows what the deployment call would have looked like. Naming an application additionally turns its
log level up and puts it back exactly as it was, which is the one EAP change that is both reversible
and free.

Administrator-only, all of it: the server refuses every EAP action to anybody else.
"""

from __future__ import annotations

import sys

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid.modules.eap import DEBUG, JAVA


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    base_url, username, password = argv[1], argv[2], argv[3]
    application_id = argv[4] if len(argv) > 4 else ""

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
            print(f"{session.user_id} is not an administrator - every EAP action would be refused")
            return 1

        eap = session.eap()
        deployed(eap)
        show_deployment_call()

        if application_id:
            log_level(eap, application_id)
        else:
            print("\nname an application as a fourth argument to see its log level turned up "
                  "and put back")

    return 0


def deployed(eap) -> None:
    """What is deployed, what was asked of it, and what is actually answering."""
    applications = eap.list_applications()
    print(f"{len(applications)} application(s) deployed:\n")

    for application in applications:
        asked = application.desired_state
        running = application.state
        # The two differing is the ordinary picture of an application starting up. The two
        # differing for long is one that cannot.
        agreement = "" if asked == running else "   <- asked for one thing, doing another"
        print(f"  {application.application_id}  ({application.runtime} {application.version})")
        print(f"      desired {asked}, actually {running}{agreement}")
        print(f"      pool {application.min_instances}..{application.max_instances}, "
              f"{application.instances} instance(s) answering")

        for endpoint in application.endpoints:
            print(f"        {endpoint.instance_id}  pid {endpoint.pid}  port {endpoint.http_port}")

        # The artifact it was deployed from, by ERN and key: names are what an operator deploys
        # with, and these are what euclid stored.
        print(f"      from {application.bucket_ern} / {application.artifact_key}")
        # ESM's checksum of the artifact, which is what a redeploy has to differ from.
        print(f"      md5 {application.md5_sum}")
        print(f"      runs as {application.user_id}"
              f"{' (a principal euclid made for it)' if application.user_id.startswith('app-') else ''}")

        if application.resources:
            print(f"      may reach {application.resources}")
        if application.command:
            print(f"      command {application.command} {' '.join(application.arguments)}")
        elif application.arguments:
            print(f"      arguments {application.arguments}")
        if application.environment:
            print(f"      environment {sorted(application.environment)}")
        print(f"      logs at {application.log_level or '(the configured default)'}")
        print()


def show_deployment_call() -> None:
    """What deploying one looks like, since this example will not do it to somebody's server."""
    print("deploying looks like this - the artifact has to be in the bucket already:\n")
    print("    esm.upload_file(bucket_ern, 'order-service-1.4.0.jar', 'target/order-service.jar')")
    print(f"    eap.create_application('order-service', {JAVA!r}, bucket='artifacts',")
    print("                           artifact='order-service-1.4.0.jar', queues=['orders'],")
    print("                           min_instances=2, max_instances=5)")
    print("    eap.start_application('order-service')")
    print("\n  ...and a new build of the same thing is a redeploy rather than an update:")
    print("    eap.redeploy_application('order-service', version='1.4.1')")


def log_level(eap, application_id: str) -> None:
    """Turn one application's logging up, then put it back exactly as it was."""
    try:
        application = eap.get_application(application_id)
    except EuclidServiceError as error:
        print(f"\n{application_id}: {error.reason or error}")
        return

    previous = application.log_level
    print(f"\n{application_id} logs at {previous or '(the configured default)'}")

    turned_up = eap.set_log_level(application_id, DEBUG)
    print(f"  turned up to {turned_up.log_level} on channel {turned_up.channel}")
    print("  no restart, no redeploy - the running instances pick it up")

    if previous:
        restored = eap.set_log_level(application_id, previous)
        print(f"  put back to {restored.log_level}")
    else:
        # Back under the installation's configuration, rather than pinned to whatever it says now.
        restored = eap.reset_log_level(application_id)
        print(f"  put back under the configured default ({restored.log_level or 'no override'})")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
