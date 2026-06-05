#!/usr/bin/env python3
"""Run the workload x config x RTT matrix.

Each measurement runs in its own process inside the bench container, so "cold"
really means a fresh GDAL block cache and a fresh /vsicurl cache rather than
whatever the previous run left behind.

Raw proxy logs are gzipped in place after each run and kept: they are the
primary measurement, and the summaries are derived from them.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "results" / "runs"
RAW = ROOT / "results" / "raw"
SPECS = ROOT / "results" / "specs"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]

WORKLOADS = ["w1", "w2", "w3", "w4", "w5"]
CONFIGS = ["DEFAULT", "TUNED_chunk16k", "TUNED_chunk256k", "TUNED_chunk1m"]
RTTS = [5.0, 50.0, 150.0]


def bench(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(COMPOSE + ["exec", "-T", "bench"] + args,
                          capture_output=True, text=True, timeout=timeout)


def kill_stragglers() -> None:
    """A killed `docker compose exec` leaves the process running inside the
    container. Two harnesses on one proxy corrupt each other's capture, so
    clear any before starting."""
    subprocess.run(COMPOSE + ["exec", "-T", "bench",
                              "pkill", "-f", "baseline/harness.py"],
                   capture_output=True, text=True, timeout=60)
    # A harness killed mid-run leaves its capture session open on the proxy,
    # which the next run's guard would refuse to start against. Close it.
    subprocess.run(
        COMPOSE + ["exec", "-T", "bench", "python3", "-c",
                   "import urllib.request as u\n"
                   "try:\n"
                   "    u.urlopen(u.Request('http://proxy:9010/session/stop',"
                   "data=b'{}',headers={'Content-Type':'application/json'}),"
                   "timeout=30)\n"
                   "except Exception:\n"
                   "    pass\n"],
        capture_output=True, text=True, timeout=60)


def gzip_logs(tag: str) -> list[str]:
    out = []
    for phase in ("cold", "warm"):
        p = RAW / f"{tag}.{phase}.jsonl"
        if p.exists():
            gz = p.with_suffix(".jsonl.gz")
            with p.open("rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            p.unlink()
            out.append(gz.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", nargs="*", default=WORKLOADS)
    ap.add_argument("--configs", nargs="*", default=CONFIGS)
    ap.add_argument("--rtts", nargs="*", type=float, default=RTTS)
    ap.add_argument("--jitter-frac", type=float, default=0.0,
                    help="jitter as a fraction of latency; 0 keeps runs "
                         "byte-for-byte reproducible")
    ap.add_argument("--max-wall-s", type=float, default=900.0)
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    RUNS.mkdir(parents=True, exist_ok=True)
    kill_stragglers()
    todo = [(w, c, r) for w in args.workloads for c in args.configs
            for r in args.rtts]
    print(f"sweep: {len(todo)} measurements "
          f"({len(args.workloads)} workloads x {len(args.configs)} configs "
          f"x {len(args.rtts)} RTTs), cold+warm each\n", flush=True)

    t_all = time.time()
    failures = []
    for i, (w, c, rtt) in enumerate(todo, 1):
        spec = SPECS / f"{w}.json"
        if not spec.exists():
            print(f"[{i}/{len(todo)}] {w} {c} {rtt:g}ms  SKIP (no spec)",
                  flush=True)
            continue
        tag = f"{w.upper()}.{c}.rtt{int(rtt)}"
        out = RUNS / f"{tag}.json"
        if args.skip_existing and out.exists():
            print(f"[{i}/{len(todo)}] {tag}  skip (exists)", flush=True)
            continue

        print(f"[{i}/{len(todo)}] {tag}", flush=True)
        t0 = time.time()
        cp = bench(["python3", "baseline/harness.py",
                    "--spec", f"results/specs/{w}.json",
                    "--config", c,
                    "--latency-ms", str(rtt),
                    "--jitter-ms", str(rtt * args.jitter_frac),
                    "--out", f"results/runs/{tag}.json",
                    "--tag", tag,
                    "--max-wall-s", str(args.max_wall_s)],
                   timeout=args.max_wall_s * 3 + 300)
        if cp.stdout.strip():
            print(cp.stdout.rstrip(), flush=True)
        if cp.returncode != 0:
            failures.append((tag, cp.stderr[-3000:]))
            print(f"  FAILED rc={cp.returncode}\n{cp.stderr[-3000:]}",
                  file=sys.stderr, flush=True)
            kill_stragglers()
        logs = gzip_logs(tag)
        print(f"  {time.time()-t0:.1f}s  logs: {', '.join(logs) or 'none'}",
              flush=True)

    # Reset the proxy to no injected latency so nothing later is confused.
    bench(["python3", "-c",
           "import urllib.request,json;"
           "urllib.request.urlopen(urllib.request.Request("
           "'http://proxy:9010/shape',data=json.dumps({'latency_ms':0,"
           "'jitter_ms':0}).encode(),headers={'Content-Type':'application/json'}))"],
          timeout=60)

    print(f"\nsweep done in {(time.time()-t_all)/60:.1f} min, "
          f"{len(failures)} failures", flush=True)
    for tag, err in failures:
        print(f"  {tag}: {err.splitlines()[-1] if err.strip() else '?'}",
              file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
