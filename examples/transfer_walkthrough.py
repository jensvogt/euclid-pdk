#!/usr/bin/env python3
"""ETS end to end: an SFTP endpoint onto a bucket, and the object that appears behind it.

    python examples/transfer_walkthrough.py https://euclid.example.com jens secret
    python examples/transfer_walkthrough.py https://euclid.example.com jens secret --start

Defines a transfer server of its own, named after the moment it started, onto a bucket of its own,
and removes both at the end - so it is safe to point at a running server. It does not start the
process unless --start says so: starting one binds a port on the host, which is not something an
example should do to somebody's machine without being asked.

Administrator-only, all of it: the server refuses every ETS action to anybody else.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid.modules.ets import SFTP

# Well above anything privileged, and not a port anything else conventionally wants.
PORT = 42222


def main(argv: list[str]) -> int:
    arguments = [a for a in argv[1:] if not a.startswith("--")]
    start = "--start" in argv
    if len(arguments) < 3:
        print(__doc__)
        return 2
    base_url, username, password = arguments[0], arguments[1], arguments[2]

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
            print(f"{session.user_id} is not an administrator - every ETS action would be refused")
            return 1

        esm, ets = session.esm(), session.ets()
        name = f"pdk-walkthrough-{int(time.time())}"

        bucket = esm.create_bucket(name)
        print(f"created bucket {bucket.name}")

        try:
            walk(esm, ets, bucket.ern, name, start)
        finally:
            try:
                ets.stop_server(name)
                ets.delete_server(name)
                print(f"\ndeleted transfer server {name}")
            except EuclidServiceError as error:
                print(f"\ncould not delete transfer server {name}: {error.reason or error}")
            esm.purge_bucket(bucket.ern)
            esm.delete_bucket(bucket.ern)
            print(f"deleted bucket {name}")

    return 0


def walk(esm, ets, bucket_ern: str, name: str, start: bool) -> None:
    """Everything between creating the bucket and taking it all down again."""
    # The definition, not a process: which protocol, which port, who may log in, and which bucket
    # the files really live in. Nothing binds anything until it is started.
    server = ets.create_server(name, bucket=name, port=PORT, protocol=SFTP,
                               home_directory="incoming/", user_ids=[ets.session.user_id],
                               directories=["incoming/2026/"])
    print(f"\ndefined {server.protocol} server {server.server_id}")
    print(f"  ern: {server.ern}")
    print(f"  {server.address}:{server.port} -> {server.bucket_name} ({server.bucket_ern})")
    print(f"  logins land in {server.home_directory or 'the root of the bucket'}")
    print(f"  may log in: users {server.user_ids}, groups {server.user_groups or '(none)'}")
    print(f"  host key: {server.host_key or '(generated on first start)'}")
    print(f"  desired {server.desired_state}, actually {server.state}")

    # Only what is named changes; the port, the bucket and the home directory stay as they are.
    widened = ets.update_server(name, user_groups=["administrators"])
    print(f"\nadded a group: {widened.user_groups}")
    print("  a running server keeps its old definition until it is restarted - the process reads "
          "this once, at startup")

    # A file put here over SFTP would be exactly this object: one bucket, two ways in.
    esm.put_object(bucket_ern, "incoming/2026/hello.txt", b"as if uploaded over SFTP\n")
    objects = esm.list_objects(bucket_ern, prefix="incoming/")
    print(f"\nwhat a client logging in would see under {widened.home_directory}: "
          f"{[o.key for o in objects.objects]}")

    if start:
        started = ets.start_server(name)
        print(f"\nasked {name} to run: desired {started.desired_state}, actually {started.state}")
        print("  asking is all it does - euclid's manager is what starts the process")

        # Give the reconciler a moment, then look again rather than claiming anything.
        time.sleep(3)
        observed = ets.get_server(name)
        print(f"  three seconds later: {observed.state}"
              f"{'  (listening)' if observed.is_running else '  (not up yet)'}")
    else:
        print("\nnot started: pass --start to bind the port on the host")

    servers = ets.list_servers()
    print(f"\n{len(servers)} transfer server(s) defined:")
    for listed in servers:
        print(f"  {listed.server_id:<32} {listed.protocol:<5} {listed.address}:{listed.port:<6} "
              f"-> {listed.bucket_name:<20} {listed.state}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
