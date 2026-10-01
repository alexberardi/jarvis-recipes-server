"""Copy objects between two S3-compatible stores, then prove the copy landed.

Written for the MinIO -> SeaweedFS migration (prds/minio-eol-object-store.md):
MinIO archived its OSS project and closed every distribution channel, so the
store had to be replaced. Existing installs keep running MinIO from images
already on disk; this moves their data to the new store.

    # rehearse -- reads nothing, writes nothing
    python scripts/migrate_object_store.py --dry-run

    # copy
    python scripts/migrate_object_store.py

    # prove a previous run landed, without copying anything
    python scripts/migrate_object_store.py --verify-only

Runs inside the jarvis-recipes-server image, which already carries boto3:

    docker run --rm --network jarvis \\
      -e SRC_ENDPOINT_URL=http://minio:9000 \\
      -e SRC_ACCESS_KEY=... -e SRC_SECRET_KEY=... \\
      -e DST_ENDPOINT_URL=http://seaweedfs:8333 \\
      -e DST_ACCESS_KEY=... -e DST_SECRET_KEY=... \\
      ghcr.io/alexberardi/jarvis-recipes-server \\
      python /app/scripts/migrate_object_store.py

That is deliberate: adding a migration image would mean another reference into a
registry we do not control, which is the exact failure being migrated away from.

WHY THIS IS SAFE TO RE-RUN
--------------------------
An object is skipped only when the destination already holds a MATCHING copy --
same size, and same MD5 where both stores expose a single-part ETag. An
interrupted run therefore resumes rather than restarting, and a half-written
object from the previous attempt is re-copied rather than skipped forever.
(Skipping on size alone made corruption permanent: --verify-only reported it
and the copy kept stepping over it. Found by corrupting a destination object
on purpose and watching the repair fail.)

Nothing is ever deleted from the source -- decommissioning MinIO stays a
separate, manual step, taken only after --verify-only passes.

WHY IT VERIFIES RATHER THAN COUNTS
----------------------------------
A migration that reports "847 copied" and is wrong about it is worse than one
that fails loudly: the next step is deleting the source. So each object is
checked by SIZE, and by MD5 when both stores report a single-part ETag.

ETags are NOT comparable in general across stores -- a multipart upload's ETag
is a hash of part hashes plus "-N", and the part size is a property of whoever
wrote the object. Objects that were multipart at the source are therefore
verified by size alone, and counted separately so the gap is visible rather
than implied.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterator

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

# Objects at or under this are copied through memory; larger ones spool to a
# temp file. Recipe photos are a few MB, but "it is only photos today" is how a
# migration script meets a 2 GB export tomorrow and gets OOM-killed halfway.
SPOOL_THRESHOLD_BYTES: int = 64 * 1024 * 1024

# Headers worth carrying over. ContentType alone is not enough: a lost
# ContentEncoding serves gzipped bytes as plain and the image renders as noise.
_METADATA_HEADERS: tuple[str, ...] = (
    "ContentType",
    "ContentEncoding",
    "ContentDisposition",
    "ContentLanguage",
    "CacheControl",
)


@dataclass
class Stats:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    verified_md5: int = 0
    verified_size_only: int = 0
    bytes_copied: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def make_client(endpoint: str, access_key: str, secret_key: str, region: str) -> BaseClient:
    """An S3 client for a self-hosted store.

    Path-style addressing is not optional here: virtual-host style is boto3's
    default and neither MinIO nor SeaweedFS serves it.
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def iter_keys(client: BaseClient, bucket: str) -> Iterator[dict[str, Any]]:
    """Every object in a bucket, paginated (a bucket can exceed one page)."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            yield obj


def list_buckets(client: BaseClient) -> list[str]:
    return [b["Name"] for b in client.list_buckets().get("Buckets", [])]


def ensure_bucket(client: BaseClient, bucket: str, dry_run: bool) -> None:
    """Create the destination bucket when absent.

    The S3 API does not create a bucket on first write -- a PUT into a missing
    bucket is NoSuchBucket, which reads as a credentials problem and is how the
    recipes photo import failed for days while the store sat there healthy.
    """
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchBucket", "NoSuchKey", "403", "Forbidden"):
            raise
    if dry_run:
        print(f"  [dry-run] would create destination bucket {bucket}")
        return
    client.create_bucket(Bucket=bucket)
    print(f"  created destination bucket {bucket}")


def _simple_md5(etag: str | None) -> str | None:
    """The MD5 inside a single-part ETag, or None when it is not one.

    A multipart ETag ends in "-N" and is a hash OF HASHES, so it cannot be
    compared against a plain MD5 or against the same object re-uploaded with a
    different part size.
    """
    if not etag:
        return None
    cleaned = etag.strip('"')
    if "-" in cleaned:
        return None
    return cleaned.lower() if len(cleaned) == 32 else None


def already_present(
    dst: BaseClient, bucket: str, key: str, size: int, source_etag: str | None
) -> bool:
    """True when the destination already holds a MATCHING copy of this object.

    Size alone is not enough. An earlier interrupted run can leave a
    same-length but different object behind, and skipping on size would make
    that corruption permanent: --verify-only would keep reporting it and
    re-running the copy would keep skipping it. So when both ends expose a
    single-part ETag, the MD5 has to agree too.

    Where either side is multipart the ETags are not comparable (hash of part
    hashes, plus a part count that belongs to whoever wrote it), so size is all
    there is -- the same compromise verify_object makes, and counted there.
    """
    try:
        head = dst.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    if int(head.get("ContentLength", -1)) != size:
        return False

    src_md5 = _simple_md5(source_etag)
    dst_md5 = _simple_md5(head.get("ETag"))
    if src_md5 and dst_md5:
        return src_md5 == dst_md5
    return True


def copy_object(
    src: BaseClient,
    dst: BaseClient,
    bucket: str,
    key: str,
    size: int,
    stats: Stats,
) -> None:
    """Copy one object and verify it landed, streaming when it is large."""
    response = src.get_object(Bucket=bucket, Key=key)
    extra: dict[str, Any] = {}
    for header in _METADATA_HEADERS:
        value = response.get(header)
        if value:
            extra[header] = value
    user_meta = response.get("Metadata") or {}
    if user_meta:
        extra["Metadata"] = user_meta

    digest = hashlib.md5()  # noqa: S324 - matching S3 ETags, not hashing secrets
    body = response["Body"]

    if size <= SPOOL_THRESHOLD_BYTES:
        payload = body.read()
        digest.update(payload)
        dst.put_object(Bucket=bucket, Key=key, Body=payload, **extra)
    else:
        with tempfile.TemporaryFile() as spool:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(chunk)
                spool.write(chunk)
            spool.seek(0)
            # upload_fileobj handles multipart for us; the spool file is
            # seekable, which a StreamingBody is not -- that is the whole
            # reason for the temp file rather than piping the response through.
            dst.upload_fileobj(spool, bucket, key, ExtraArgs=extra or None)

    verify_object(dst, bucket, key, size, digest.hexdigest(), response.get("ETag"), stats)
    stats.bytes_copied += size


def verify_object(
    dst: BaseClient,
    bucket: str,
    key: str,
    size: int,
    source_md5: str | None,
    source_etag: str | None,
    stats: Stats,
) -> None:
    """Raise unless the destination object matches the source."""
    head = dst.head_object(Bucket=bucket, Key=key)
    dst_size = int(head.get("ContentLength", -1))
    if dst_size != size:
        raise RuntimeError(f"size mismatch: source {size} != destination {dst_size}")

    dst_md5 = _simple_md5(head.get("ETag"))
    if source_md5 and dst_md5:
        if source_md5 != dst_md5:
            raise RuntimeError(f"MD5 mismatch: source {source_md5} != destination {dst_md5}")
        stats.verified_md5 += 1
    elif _simple_md5(source_etag) and dst_md5:
        if _simple_md5(source_etag) != dst_md5:
            raise RuntimeError("ETag mismatch between stores")
        stats.verified_md5 += 1
    else:
        # Multipart at one end or the other. Size is all that is comparable;
        # counted separately so the weaker check is visible in the summary.
        stats.verified_size_only += 1


def migrate_bucket(
    src: BaseClient,
    dst: BaseClient,
    bucket: str,
    stats: Stats,
    dry_run: bool,
    verify_only: bool,
) -> None:
    print(f"\n=== bucket: {bucket} ===")
    if not (dry_run or verify_only):
        ensure_bucket(dst, bucket, dry_run)
    elif dry_run:
        ensure_bucket(dst, bucket, True)

    for obj in iter_keys(src, bucket):
        key = obj["Key"]
        size = int(obj["Size"])

        if verify_only:
            try:
                verify_object(dst, bucket, key, size, None, obj.get("ETag"), stats)
                stats.copied += 1
            except (ClientError, RuntimeError) as exc:
                stats.failed += 1
                stats.failures.append((key, str(exc)))
                print(f"  MISSING/BAD {key}: {exc}")
            continue

        if already_present(dst, bucket, key, size, obj.get("ETag")):
            stats.skipped += 1
            continue

        if dry_run:
            print(f"  [dry-run] would copy {key} ({size} bytes)")
            stats.copied += 1
            continue

        try:
            copy_object(src, dst, bucket, key, size, stats)
            stats.copied += 1
            print(f"  copied {key} ({size} bytes)")
        except (ClientError, RuntimeError, OSError) as exc:
            stats.failed += 1
            stats.failures.append((key, str(exc)))
            print(f"  FAILED {key}: {exc}")


def env_or_die(name: str, *, default: str | None = None) -> str:
    value = os.getenv(name, default or "")
    if not value:
        print(f"missing required environment variable: {name}")
        raise SystemExit(2)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be copied; write nothing")
    parser.add_argument("--verify-only", action="store_true",
                        help="check a previous run landed; copy nothing")
    parser.add_argument("--bucket", action="append", dest="buckets", default=None,
                        help="bucket to migrate (repeatable; default: every source bucket)")
    parser.add_argument("--failures-file", default="/tmp/object-store-migration-failures.txt",
                        help="where to write failed keys for retry")
    args = parser.parse_args()

    if args.dry_run and args.verify_only:
        print("--dry-run and --verify-only are mutually exclusive")
        return 2

    region = os.getenv("S3_REGION", "us-east-1")
    src = make_client(
        env_or_die("SRC_ENDPOINT_URL"),
        env_or_die("SRC_ACCESS_KEY"),
        env_or_die("SRC_SECRET_KEY"),
        region,
    )
    dst = make_client(
        env_or_die("DST_ENDPOINT_URL"),
        env_or_die("DST_ACCESS_KEY"),
        env_or_die("DST_SECRET_KEY"),
        region,
    )

    mode = "DRY RUN" if args.dry_run else "VERIFY ONLY" if args.verify_only else "COPY"
    print(f"object store migration [{mode}]")
    print(f"  source     : {os.getenv('SRC_ENDPOINT_URL')}")
    print(f"  destination: {os.getenv('DST_ENDPOINT_URL')}")

    try:
        buckets = args.buckets or list_buckets(src)
    except ClientError as exc:
        print(f"cannot list source buckets: {exc}")
        return 1
    if not buckets:
        print("source has no buckets; nothing to do")
        return 0
    print(f"  buckets    : {', '.join(buckets)}")

    stats = Stats()
    for bucket in buckets:
        try:
            migrate_bucket(src, dst, bucket, stats, args.dry_run, args.verify_only)
        except ClientError as exc:
            print(f"  bucket {bucket} failed outright: {exc}")
            stats.failed += 1
            stats.failures.append((f"{bucket}/*", str(exc)))

    print("\n=== summary ===")
    label = "would copy" if args.dry_run else "verified" if args.verify_only else "copied"
    print(f"  {label:18s}: {stats.copied}")
    print(f"  {'skipped (present)':18s}: {stats.skipped}")
    print(f"  {'failed':18s}: {stats.failed}")
    if not args.dry_run:
        print(f"  {'md5-verified':18s}: {stats.verified_md5}")
        print(f"  {'size-only verified':18s}: {stats.verified_size_only}"
              " (multipart at one end; size is all that is comparable)")
    if not (args.dry_run or args.verify_only):
        print(f"  {'bytes copied':18s}: {stats.bytes_copied}")

    if stats.failures:
        # A count is not actionable; the keys are. Without this a single failure
        # means re-running the whole set to find it again.
        try:
            with open(args.failures_file, "w", encoding="utf-8") as handle:
                for key, reason in stats.failures:
                    handle.write(f"{key}\t{reason}\n")
            print(f"\n  failed keys written to {args.failures_file}")
        except OSError as exc:
            print(f"\n  could not write failures file: {exc}")
        print("  DO NOT decommission the source store.")
        return 1

    if args.verify_only:
        print("\n  verification passed -- the destination holds every source object.")
    elif not args.dry_run:
        print("\n  copy complete. Re-run with --verify-only before decommissioning"
              " the source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
