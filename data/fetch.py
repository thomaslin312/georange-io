#!/usr/bin/env python3
"""Stage the pinned corpus into MinIO.

For each object in data/sources.yaml:
    download to a temp file -> verify byte count -> sha256 -> upload to MinIO
    -> delete the temp file.

Nothing is kept on local disk, so peak transient usage is one object
(~165 MB worst case) regardless of corpus size. Checksums land in
data/staged.json, which is the reproducibility record.

Idempotent: an object already present in the bucket with a matching size and
recorded sha256 is skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
import yaml
from botocore.config import Config

HERE = Path(__file__).parent
ROOT = HERE.parent
STAGED = HERE / "staged.json"

ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9100")
ACCESS = os.environ.get("MINIO_ACCESS_KEY", "georange-io")
SECRET = os.environ.get("MINIO_SECRET_KEY", "georange-io123")

PUBLIC_READ = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow", "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
        "Resource": ["arn:aws:s3:::{b}", "arn:aws:s3:::{b}/*"],
    }],
}


def s3():
    return boto3.client(
        "s3", endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS, aws_secret_access_key=SECRET,
        region_name="us-east-1",
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"},
                      retries={"max_attempts": 5, "mode": "standard"},
                      max_pool_connections=32),
    )


def ensure_bucket(cli, bucket: str) -> None:
    try:
        cli.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001
        cli.create_bucket(Bucket=bucket)
    pol = json.loads(json.dumps(PUBLIC_READ).replace("{b}", bucket))
    cli.put_bucket_policy(Bucket=bucket, Policy=json.dumps(pol))


STALL_S = float(os.environ.get("GEORANGE_IO_STALL_S", "90"))


def download(url: str, dest: Path, expect: int | None = None,
             retries: int = 6) -> tuple[int, str]:
    """Download with resume and stall detection.

    The measured link caps out near 1 MB/s regardless of stream count, so a
    stalled socket is expensive: detect it, tear the connection down, and
    resume from the byte we already have rather than starting over.
    """
    last = None
    for attempt in range(retries):
        have = dest.stat().st_size if dest.exists() else 0
        if expect and have == expect:
            break
        try:
            hdrs = {"User-Agent": "georange_io-phase0"}
            if have:
                hdrs["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:
                    have = 0  # server ignored the range; start over
                mode = "ab" if have else "wb"
                with dest.open(mode) as f:
                    last_progress = time.time()
                    while True:
                        c = r.read(1 << 20)
                        if not c:
                            break
                        f.write(c)
                        have += len(c)
                        now = time.time()
                        if now - last_progress > STALL_S:
                            raise TimeoutError(f"stalled at {have} bytes")
                        last_progress = now
            if expect and dest.stat().st_size != expect:
                raise RuntimeError(f"short read {dest.stat().st_size} != {expect}")
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(20, 3 * (attempt + 1)))
    else:
        raise RuntimeError(f"download failed {url}: {last}")

    h = hashlib.sha256()
    n = 0
    with dest.open("rb") as f:
        while (c := f.read(1 << 20)):
            h.update(c); n += len(c)
    if expect and n != expect:
        raise RuntimeError(f"download failed {url}: {last}")
    return n, h.hexdigest()


def stage_one(entry: dict, bucket: str, done: dict, force: bool) -> dict:
    key, url = entry["key"], entry["url"]
    cli = s3()
    prev = done.get(key)
    if prev and not force:
        try:
            h = cli.head_object(Bucket=bucket, Key=key)
            if h["ContentLength"] == prev["bytes"]:
                return {**prev, "skipped": True}
        except Exception:  # noqa: BLE001
            pass

    with tempfile.TemporaryDirectory(prefix="sb-stage-") as td:
        tmp = Path(td) / "obj.tif"
        t0 = time.time()
        exp = entry.get("size")
        n, sha = download(url, tmp, expect=exp)
        if exp and n != exp:
            raise RuntimeError(f"{key}: size mismatch, expected {exp} got {n}")
        cli.upload_file(str(tmp), bucket, key,
                        ExtraArgs={"ContentType": "image/tiff"})
    rec = {"key": key, "url": url, "bytes": n, "sha256": sha,
           "dataset": entry["dataset"], "role": entry.get("role"),
           "tile": entry.get("tile"), "staged_s": round(time.time() - t0, 2),
           "skipped": False}
    print(f"  staged {key:44s} {n/1e6:8.1f} MB  {rec['staged_s']:6.1f}s", flush=True)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=str(HERE / "sources.yaml"))
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    doc = yaml.safe_load(Path(args.sources).read_text())
    bucket = doc["bucket"]
    objs = doc["objects"][: args.limit or None]

    cli = s3()
    ensure_bucket(cli, bucket)
    done = json.loads(STAGED.read_text()) if STAGED.exists() else {}

    total = sum(o.get("size") or 0 for o in objs)
    print(f"Staging {len(objs)} objects ({total/1e9:.2f} GB) -> {ENDPOINT}/{bucket}",
          flush=True)

    t0 = time.time()
    errs = []
    n_done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(stage_one, o, bucket, done, args.force): o for o in objs}
        for f in as_completed(futs):
            o = futs[f]
            n_done += 1
            try:
                rec = f.result()
                done[rec["key"]] = rec
            except Exception as e:  # noqa: BLE001
                errs.append((o["key"], repr(e)[:160]))
                print(f"  FAIL {o['key']}: {e}", file=sys.stderr, flush=True)
            got = sum(v["bytes"] for v in done.values())
            el = time.time() - t0
            print(f"  [{n_done}/{len(objs)}] {got/1e9:5.2f} GB  "
                  f"{got/1e6/max(el,1):5.2f} MB/s  elapsed {el/60:5.1f} min",
                  flush=True)
            STAGED.write_text(json.dumps(done, indent=2, sort_keys=True))

    STAGED.write_text(json.dumps(done, indent=2, sort_keys=True))
    got = sum(v["bytes"] for v in done.values())
    print(f"\nStaged {len(done)}/{len(objs)} objects, {got/1e9:.2f} GB "
          f"in {time.time()-t0:.0f}s", flush=True)
    if errs:
        print(f"{len(errs)} failures:", file=sys.stderr)
        for k, e in errs:
            print(f"  {k}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
