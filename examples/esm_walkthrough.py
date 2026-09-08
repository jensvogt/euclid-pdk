#!/usr/bin/env python3
"""Everything ESM does, in the order you would do it.

    python examples/esm_walkthrough.py https://euclid.example.com jens secret

Works in a bucket of its own, named after the moment it started, and deletes it again at the end -
so it is safe to point at a running server, and a run that dies halfway leaves one obviously
disposable bucket behind rather than touching anything of yours.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

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
        esm = session.esm()
        name = f"pdk-walkthrough-{int(time.time())}"

        bucket = esm.create_bucket(name)
        print(f"created bucket {bucket.name}")
        print(f"  ern: {bucket.ern}   (this, not the name, is what every other call takes)")

        try:
            walk(esm, bucket.ern)
        finally:
            # Emptied first: a bucket with objects in it cannot be deleted, which is the server
            # refusing to lose track of data rather than an inconvenience.
            esm.purge_bucket(bucket.ern)
            esm.delete_bucket(bucket.ern)
            print(f"\ndeleted bucket {name} again")

    return 0


def walk(esm, bucket_ern: str) -> None:
    """Everything between creating the bucket and deleting it."""
    esm.set_bucket_tag(bucket_ern, "purpose", "sdk-walkthrough")

    # Small enough for one request. The bytes go over the wire as bytes, not as base64 in a JSON
    # field, which is what keeps a large object the size it is.
    stored = esm.put_object(bucket_ern, "notes/hello.txt", b"written in one request\n",
                            attributes={"author": "euclid-pdk", "revision": 1})
    print(f"\nput   {stored.key:<24} {stored.size:>8} bytes  md5={stored.md5_sum}")
    print(f"      attributes: {esm.list_object_attributes(stored.ern)}")

    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "large.bin"
        source.write_bytes(bytes(range(256)) * 40_000)  # ~10 MiB: three parts at the default size

        uploaded = esm.upload_file(bucket_ern, "data/large.bin", source,
                                   attributes={"origin": "esm_walkthrough.py"})
        print(f"put   {uploaded.key:<24} {uploaded.size:>8} bytes  (in parts, several at a time)")

        # Tried in one request first and fetched in parts when that comes back "too large", so the
        # caller does not have to know which of the two an object needs.
        target = Path(scratch) / "downloaded.bin"
        written = esm.download_file(bucket_ern, "data/large.bin", target)
        print(f"got   {target.name:<24} {written:>8} bytes  identical={target.read_bytes() == source.read_bytes()}")

    objects = esm.list_objects(bucket_ern, page_size=10)
    print(f"\n{objects.total} object(s) in the bucket:")
    for stored_object in objects.objects:
        print(f"  {stored_object.key:<24} {stored_object.size:>9} {stored_object.content_type}")

    copied = esm.copy_object(bucket_ern, "notes/hello.txt", bucket_ern, "notes/hello.copy.txt")
    print(f"\ncopied to {copied.key}, which is its own object with its own ERN")
    deleted = esm.delete_objects(bucket_ern, ["notes/hello.copy.txt", "never-existed.txt"])
    print(f"deleted {deleted.objects} of the {deleted.asked} key(s) asked for - one named nothing, "
          f"which is not an error")

    print(f"\nbucket holds {esm.get_object_count(bucket_ern)} object(s), "
          f"{esm.get_bucket_size(bucket_ern)} byte(s)")

    try:
        print(f"subscriptions: {esm.list_subscriptions(bucket_ern)}")
    except EuclidServiceError as error:
        print(f"subscriptions: unavailable ({error.reason})")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
