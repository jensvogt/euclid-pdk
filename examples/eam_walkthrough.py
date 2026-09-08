#!/usr/bin/env python3
"""Everything the SDK does today, in the order you would do it.

    python examples/eam_walkthrough.py https://euclid.example.com jens secret

Read-only apart from the access key it creates and deletes again, so it is safe to point at a
running server. The administrator-only calls at the end are skipped when the login says the user is
not one - the server would refuse them anyway, but saying so here is more useful than a 403.
"""

from __future__ import annotations

import sys

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError, SigningScheme


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    base_url, username, password = argv[1], argv[2], argv[3]

    try:
        # Reuses ~/.euclid/credentials when a valid session is already cached for this server, so
        # running this twice in a row costs one login rather than two.
        session = (Euclid.for_server(base_url)
                   .access()
                   .credentials(username, password)
                   # A development server's certificate is usually its own; drop this line, or
                   # point ca_cert_path() at the real CA, anywhere it matters.
                   .verify(False)
                   .signing_scheme(SigningScheme.SIGV4)
                   .login())
    except EuclidAuthenticationError as error:
        print(f"login refused: {error.reason or error}")
        return 1

    with session:
        print(f"logged in as {session.user_id} in {session.account_id}/{session.region}, "
              f"admin={session.is_admin}")
        print(f"signing as {session.access_key_id or '(no access key - using the bearer token)'} "
              f"over {session.authority}")

        users = session.list_users(page_size=5)
        print(f"\n{users.total} user(s); first {len(users.users)}:")
        for user in users.users:
            namespaces = [ns for grant in user.account_grants for ns in grant.namespaces]
            print(f"  {user.user_id:<16} {user.email:<28} namespaces={namespaces}")

        # The secret comes back here and nowhere else, so anything that needs it has to keep it.
        created = session.create_access_key()
        print(f"\ncreated access key {created.access_key_id}")
        print(f"  this user now has: {[key.access_key_id for key in session.list_access_keys()]}")
        session.delete_access_key(created.access_key_id)
        print(f"  deleted {created.access_key_id} again")

        if not session.is_admin:
            print("\nnot an administrator - skipping the account and namespace listings")
            return 0

        accounts = session.list_accounts(page_size=5)
        print(f"\n{accounts.total} account(s):")
        for account in accounts.accounts:
            print(f"  {account.account_id:<16} {account.name}")
            try:
                namespaces = session.list_namespaces(account.account_id, page_size=5)
                print(f"      namespaces: {[ns.name for ns in namespaces.namespaces]}")
            except EuclidServiceError as error:
                print(f"      namespaces: unavailable ({error.reason})")

        groups = session.list_user_groups(page_size=5)
        print(f"\n{groups.total} user group(s): {[group.name for group in groups.user_groups]}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
