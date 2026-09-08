"""Build a workload spec matching bench_aws's own request set, so sidecars can
be built for the blocks that benchmark actually reads."""
import sys, json
sys.path[:0] = ["/work", "/work/baseline", "/work/experiments"]
from spec import Read, WorkloadSpec
from theoretical import compute
from bench_aws import build_requests

reqs, key_map = build_requests(5, 12)
reads = [Read(key_map[k], 0, x, y, 1, 1) for (k, x, y) in reqs]
sp = WorkloadSpec(name="BENCHAWS", seed=0,
                  params={"note": "mirrors experiments/bench_aws.py 5x12"},
                  reads=reads)
sp.theoretical = compute(sp.reads)
sp.save("/work/results/specs/benchaws.json")
print(f"  {len(reads)} reads over {sp.theoretical['n_files']} files, "
      f"{sp.theoretical['n_blocks']} blocks")
