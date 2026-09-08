#!/usr/bin/env python3
"""EKM and ESS end to end: a key, something encrypted with it, and a secret kept under it.

    python examples/secrets_walkthrough.py https://euclid.example.com jens secret

Works with a key and a secret of its own, named after the moment it started, and cleans both up at
the end - so it is safe to point at a running server. The key is scheduled for deletion rather than
removed, because that is all delete-key does; a run that dies halfway leaves one obviously
disposable key with a deletion date on it.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError


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
        ekm, ess = session.ekm(), session.ess()
        name = f"pdk-walkthrough-{int(time.time())}"

        key = ekm.create_key(description=f"created by {name}")
        print(f"created key {key.name}  ({key.algorithm}-{key.length}, {key.status})")
        print(f"  ern: {key.ern}")
        print("  the name is what encrypts and decrypts; the ERN is what revokes, describes and tags")

        try:
            walk(ekm, ess, key, name)
        finally:
            try:
                ess.delete_secret(name)
            except EuclidServiceError as error:
                print(f"\ncould not delete the secret: {error.reason or error}")
            scheduled = ekm.delete_key(key.name, pending_window_in_days=7)
            print(f"\nscheduled key {key.name} for deletion on {scheduled.deletion_date}")
            print("  everything it encrypted becomes unreadable then - which is why it is a date "
                  "rather than an act")

    return 0


def walk(ekm, ess, key, name: str) -> None:
    """Everything between creating the key and scheduling it for deletion."""
    plaintext = b"account 4711"
    sealed = ekm.encrypt(key.name, plaintext)
    print(f"\nencrypted {len(plaintext)} bytes into {len(sealed)} "
          f"(IV || ciphertext || tag, which is what decrypt takes back)")
    print(f"  round trip intact: {ekm.decrypt(key.name, sealed) == plaintext}")
    print("  the key never left the server: the bytes went to it, not it to the bytes")

    ekm.add_key_tag(key.ern, "purpose", "sdk-walkthrough")
    ekm.set_key_description(key.ern, "created by a walkthrough, safe to delete")
    print("\ntagged and described the key - neither of which changes its material or its life")

    # Named explicitly, so this secret's life is tied to the key created above rather than to the
    # account's default one.
    secret = ess.create_secret(name, "hunter2", description="a walkthrough's idea of a password",
                               key_ern=key.ern)
    print(f"\ncreated secret {secret.name} (version {secret.version}) under {secret.encryption_key_ern}")

    print(f"  value now: {ess.get_secret(name).value!r}  (the one call that returns one)")

    rotated = ess.rotate_secret(name, "hunter3")
    print(f"  rotated to version {rotated.version}; value now: {ess.get_secret(name).value!r}")

    # Only what is named changes: the value stays as it was rotated to a moment ago.
    described = ess.update_secret(name, description="rotated by the walkthrough")
    print(f"  described without rotating: still version {described.version}, "
          f"value {ess.get_secret(name).value!r}")

    tagged = ess.add_secret_tag(name, "team", "platform")
    print(f"  tags: {tagged.tags}")

    keys = ekm.list_keys(page_size=5)
    print(f"\n{keys.total} key(s) in this namespace; first {len(keys.keys)}:")
    for listed in keys.keys:
        print(f"  {listed.name:<24} {listed.algorithm}-{listed.length:<5} {listed.status:<17} "
              f"{listed.description}")

    secrets = ess.list_secrets(page_size=5)
    print(f"\n{secrets.total} secret(s); first {len(secrets.secrets)} - metadata only, never values:")
    for listed in secrets.secrets:
        print(f"  {listed.name:<32} v{listed.version:<4} {listed.description}")

    certificates = ekm.list_certificates(page_size=5)
    print(f"\n{certificates.total} certificate(s): "
          f"{[(c.name, 'self-signed' if c.generated else 'issued') for c in certificates.certificates]}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
