#!/usr/bin/env python3
"""Does the sidecar actually reduce bytes, and by how much?

Measured against the local proxy, not the live archive. Bytes are deterministic
so a controlled store answers this exactly; a flaky WAN only adds noise. Both
configurations read the identical request set.
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "baseline"))
from spec import WorkloadSpec                    # noqa: E402
from georange_io import SparseReader             # noqa: E402

BUCKET = os.environ.get("GEORANGE_IO_BUCKET", "switchback")
PROXY = os.environ.get("GEORANGE_IO_PROXY_HTTP", "http://proxy:9000")
CONTROL = os.environ.get("GEORANGE_IO_PROXY_CONTROL", "http://proxy:9010")
RAW = ROOT / "results" / "raw"


def ctl(p, payload=None):
    r = urllib.request.Request(CONTROL + p, data=json.dumps(payload or {}).encode(),
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=600))


def tally(label):
    f = RAW / f"{label}.jsonl"
    n = b = 0
    for line in f.read_text().splitlines():
        d = json.loads(line or "{}")
        if d.get("record") == "req":
            n += 1; b += d.get("resp_body_bytes", 0)
    f.unlink(missing_ok=True)
    return n, b


def run(reqs, label, sbx_dir, identities):
    rd = SparseReader(None, PROXY, BUCKET, margin=0.03, workers=1,
                      sbx_dir=sbx_dir, content_identities=identities)
    ctl("/session/start", {"label": label, "meta": {}})
    out = rd.sample(reqs)
    ctl("/session/stop")
    st = rd.stats.as_dict()
    try: rd.close()
    except Exception: pass
    n, b = tally(label)
    return out, n, b, st.get("blocks_from_checkpoint", 0)


def main():
    ctl("/shape", {"latency_ms": 0, "jitter_ms": 0, "bandwidth_mbps": 0})
    spec = WorkloadSpec.load(ROOT / "results" / "specs" / "benchaws.json")
    reqs = [(r.key, r.x, r.y) for r in spec.reads]
    staged = json.loads((ROOT / "data" / "staged.json").read_text())
    identities = {k: f"sha256:{v['sha256']}" for k, v in staged.items()
                  if v.get("sha256")}
    sbx = str(ROOT / "results" / "sbx")

    a, an, ab, ac = run(reqs, "_S.plain", None, {})
    b, bn, bb, bc = run(reqs, "_S.sidecar", sbx, identities)
    same = np.array_equal(a, b)

    print(f"  {len(reqs)} point reads over {spec.theoretical['n_files']} files, "
          f"{spec.theoretical['n_blocks']} blocks\n")
    print(f"  {'configuration':<24} {'requests':>9} {'bytes':>11} {'ckpt blocks':>12}")
    print(f"  {'no sidecar':<24} {an:>9,} {ab/1e6:>10.2f}M {ac:>12}")
    print(f"  {'with sidecar':<24} {bn:>9,} {bb/1e6:>10.2f}M {bc:>12}")
    print(f"\n  values identical: {same}")
    print(f"  sidecar effect: {ab/max(1,bb):.2f}x bytes, "
          f"{an/max(1,bn):.2f}x requests")
    idx_bytes = sum(p.stat().st_size for p in Path(sbx).rglob('*.sbx'))
    print(f"  sidecar size on disk: {idx_bytes/1e6:.1f} MB")
    json.dump({"reads": len(reqs), "identical": bool(same),
               "plain": {"requests": an, "bytes": ab},
               "sidecar": {"requests": bn, "bytes": bb,
                           "checkpoint_blocks": bc},
               "sidecar_bytes_on_disk": idx_bytes},
              open(ROOT / "results" / "audit_sidecar.json", "w"), indent=2)
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
