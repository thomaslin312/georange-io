#!/usr/bin/env python3
"""Turn the raw runs into the CSV, the tables and the plots REPORT.md uses."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
PLOTS = RES / "plots"

WORKLOAD_TITLE = {
    "W1": "W1 WINDOWS", "W2": "W2 SCATTERED", "W3": "W3 LINEAR",
    "W4": "W4 HIERARCHICAL", "W5": "W5 FRONTIER",
}
CFG_ORDER = ["DEFAULT", "TUNED_chunk16k", "TUNED_chunk256k", "TUNED_chunk1m",
             "TUNED_chunk16k_mt"]
CFG_LABEL = {"DEFAULT": "DEFAULT", "TUNED_chunk16k": "TUNED 16 kB",
             "TUNED_chunk256k": "TUNED 256 kB", "TUNED_chunk1m": "TUNED 1 MB",
             "TUNED_chunk16k_mt": "TUNED 16 kB + MT decode"}


def load_runs() -> list[dict]:
    rows = []
    for f in sorted((RES / "runs").glob("*.json")):
        r = json.loads(f.read_text())
        for p in r["passes"]:
            m = p.get("metrics") or {}
            rows.append({
                "workload": r["workload"], "config": r["config"],
                "rtt_ms": r["latency_ms"], "phase": p["pass"],
                "bytes_fetched": m.get("bytes_fetched"),
                "requests": m.get("requests"),
                "byte_amp": m.get("byte_amplification"),
                "req_amp": m.get("request_amplification"),
                "min_total_bytes": m.get("min_total_bytes"),
                "min_requests": m.get("min_requests"),
                "min_requests_naive": m.get("min_requests_naive"),
                "n_files": m.get("n_files"),
                "min_block_bytes": m.get("min_block_bytes"),
                "header_bytes": m.get("header_bytes"),
                "n_blocks": m.get("n_blocks"),
                "mean_range_bytes": m.get("mean_range_bytes"),
                "connections": m.get("connections"),
                "wall_s": p.get("wall_s"), "peak_rss_mb": p.get("peak_rss_mb"),
                "reads_executed": p.get("reads_executed"),
                "reads_total": p.get("reads_total"),
                "truncated": p.get("truncated"),
                "gdal": r["versions"].get("gdal"),
                "curl": r["versions"].get("curl"),
                "run_file": f.name,
            })
    return rows


def write_csv(rows) -> Path:
    p = RES / "summary.csv"
    if not rows:
        p.write_text("")
        return p
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return p


def fmt(x, nd=2, dash="--"):
    return dash if x is None else f"{x:,.{nd}f}"


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def table_theoretical(rows) -> str:
    seen = {}
    for r in rows:
        if r["workload"] not in seen and r["min_total_bytes"]:
            seen[r["workload"]] = r
    hdr = ["Workload", "Reads", "Files", "Distinct blocks", "Block bytes",
           "Header bytes", "Minimum total", "Minimum requests (coalesced)",
           "Minimum requests (one per block)"]
    out = []
    for w in sorted(seen):
        r = seen[w]
        out.append([WORKLOAD_TITLE.get(w, w), f"{r['reads_total']:,}",
                    f"{(r.get('n_files') or 0):,}",
                    f"{r['n_blocks']:,}",
                    f"{r['min_block_bytes']/1e6:,.1f} MB",
                    f"{r['header_bytes']/1e3:,.0f} kB",
                    f"{r['min_total_bytes']/1e6:,.1f} MB",
                    f"{r['min_requests']:,}",
                    f"{(r.get('min_requests_naive') or 0):,}"])
    return md_table(hdr, out)


def table_main(rows, phase="cold") -> str:
    rtts = sorted({r["rtt_ms"] for r in rows})
    hdr = ["Workload", "Config"]
    for t in rtts:
        hdr += [f"{t:g}ms bytes x", f"{t:g}ms req x", f"{t:g}ms wall"]
    out = []
    for w in sorted({r["workload"] for r in rows}):
        for c in CFG_ORDER:
            sel = {r["rtt_ms"]: r for r in rows
                   if r["workload"] == w and r["config"] == c
                   and r["phase"] == phase}
            if not sel:
                continue
            line = [WORKLOAD_TITLE.get(w, w), CFG_LABEL.get(c, c)]
            for t in rtts:
                r = sel.get(t)
                if not r:
                    line += ["--", "--", "--"]
                else:
                    mark = " !" if r.get("truncated") else ""
                    line += [fmt(r["byte_amp"]) + mark, fmt(r["req_amp"], 1),
                             f"{r['wall_s']:,.1f}s"]
            out.append(line)
    return md_table(hdr, out)


def best_tuned(rows, phase="cold", rtt=None):
    """Per workload and RTT, the TUNED config with the lowest byte amplification."""
    best = {}
    for r in rows:
        if r["phase"] != phase or not r["config"].startswith("TUNED"):
            continue
        if r["byte_amp"] is None:
            continue
        if rtt is not None and r["rtt_ms"] != rtt:
            continue
        k = (r["workload"], r["rtt_ms"])
        if k not in best or r["byte_amp"] < best[k]["byte_amp"]:
            best[k] = r
    return best


def table_headroom(rows, rtt) -> str:
    best = best_tuned(rows, "cold", rtt)
    dflt = {(r["workload"], r["rtt_ms"]): r for r in rows
            if r["config"] == "DEFAULT" and r["phase"] == "cold"}
    warm = {(r["workload"], r["config"], r["rtt_ms"]): r for r in rows
            if r["phase"] == "warm"}
    hdr = ["Workload", "Best TUNED", "Bytes x", "Requests x",
           "Byte headroom", "Request headroom", "DEFAULT bytes x", "Warm bytes x"]
    out = []
    for w in sorted({k[0] for k in best}):
        r = best.get((w, rtt))
        if not r:
            continue
        d = dflt.get((w, rtt))
        wm = warm.get((w, r["config"], rtt))
        bh = (1 - 1 / r["byte_amp"]) * 100 if r["byte_amp"] else None
        rh = (1 - 1 / r["req_amp"]) * 100 if r["req_amp"] else None
        out.append([WORKLOAD_TITLE.get(w, w), CFG_LABEL.get(r["config"]),
                    fmt(r["byte_amp"]), fmt(r["req_amp"], 1),
                    f"{bh:,.1f}%" if bh is not None else "--",
                    f"{rh:,.1f}%" if rh is not None else "--",
                    fmt(d["byte_amp"]) if d else "--",
                    fmt(wm["byte_amp"]) if wm else "--"])
    return md_table(hdr, out)


def table_attribution(rows, rtt) -> str:
    dp = RES / "diagnosis.json"
    if not dp.exists():
        return "_diagnosis.json not present; run baseline/diagnose.py_"
    diag = json.loads(dp.read_text())
    best = best_tuned(rows, "cold", rtt)
    hdr = ["Workload", "Config", "Header", "Needed", "Redundant refetch",
           "Never needed", "Range padding", "Blocks refetched"]
    out = []
    for (w, t), r in sorted(best.items()):
        if t != rtt:
            continue
        tag = f"{w}.{r['config']}.rtt{int(t)}.cold"
        d = diag.get(tag)
        if not d:
            continue
        a = d["attribution_pct"]
        out.append([WORKLOAD_TITLE.get(w, w), CFG_LABEL.get(r["config"]),
                    f"{a['header']:.1f}%", f"{a['needed']:.1f}%",
                    f"{a['redundant']:.1f}%", f"{a['unneeded']:.1f}%",
                    f"{a['padding']:.1f}%",
                    f"{d['blocks_fetched_more_than_once']:,} of "
                    f"{d['distinct_blocks_fetched']:,}"])
    return md_table(hdr, out)


DATASET_TITLE = {
    "cop_dem_glo30": "Copernicus DEM GLO-30",
    "esa_worldcover_v200": "ESA WorldCover 10 m v200 (2021)",
    "sentinel2_l2a": "Sentinel-2 L2A",
}
COMPRESSION = {1: "none", 5: "LZW", 7: "JPEG", 8: "DEFLATE", 32946: "DEFLATE",
               50000: "ZSTD"}


def table_corpus() -> str:
    ip = RES / "cog_index.json"
    sp = ROOT / "data" / "staged.json"
    if not (ip.exists() and sp.exists()):
        return "_corpus not indexed yet_"
    index = json.loads(ip.read_text())
    staged = json.loads(sp.read_text())
    groups: dict[str, list[str]] = {}
    for k, r in index.items():
        groups.setdefault(r.get("dataset") or "?", []).append(k)
    hdr = ["Dataset", "Objects", "Staged", "CRS", "Pixel size", "Block",
           "Compression", "Overviews", "Blocks (all levels)"]
    out = []
    tot_b = tot_n = 0
    for ds, keys in sorted(groups.items()):
        e = index[sorted(keys)[0]]
        L0 = e["levels"][0]
        b = sum(staged[k]["bytes"] for k in keys if k in staged)
        nblk = sum(sum(l["nbx"] * l["nby"] for l in index[k]["levels"])
                   for k in keys)
        px = abs(e["transform"][0])
        px_s = (f"{px*3600:.4g} arcsec (~{px*111320:.0f} m)"
                if e["epsg"] == 4326 else f"{px:g} m")
        out.append([DATASET_TITLE.get(ds, ds), f"{len(keys)}",
                    f"{b/1e9:.2f} GB", f"EPSG:{e['epsg']}", px_s,
                    f"{L0['blockw']}x{L0['blockh']}",
                    COMPRESSION.get(L0["compression"], str(L0["compression"])),
                    f"{len(e['levels'])-1}", f"{nblk:,}"])
        tot_b += b
        tot_n += len(keys)
    out.append(["**Total**", f"**{tot_n}**", f"**{tot_b/1e9:.2f} GB**",
                "", "", "", "", "", ""])
    return md_table(hdr, out)


def table_environment(rows) -> str:
    if not rows:
        return "_no runs_"
    r0 = rows[0]
    hv = set()
    for f in sorted((RES / "runs").glob("*.json")):
        d = json.loads(f.read_text())
        for p in d["passes"]:
            hv |= set((p.get("metrics") or {}).get("http_versions", {}))
    lines = [
        ["GDAL", r0.get("gdal") or "?"],
        ["libcurl", r0.get("curl") or "?"],
        ["rasterio", json.loads(next((RES / "runs").glob("*.json")).read_text())
         ["versions"].get("rasterio", "?")],
        ["PROJ", json.loads(next((RES / "runs").glob("*.json")).read_text())
         ["versions"].get("proj", "?")],
        ["HTTP version negotiated", ", ".join(sorted(hv)) or "?"],
        ["Object store", "MinIO, single node, local disk"],
        ["Injected jitter", "0 ms (byte counts stay reproducible)"],
        ["Injected connection setup cost", "0 ms (generous to GDAL)"],
    ]
    return md_table(["Component", "Value"], lines)


def table_oracle() -> str:
    p = RES / "w5_oracle.json"
    if not p.exists():
        return "_oracle not computed yet_"
    d = json.loads(p.read_text())
    m, dj, ac = d["materialise"], d["dijkstra"], d["astar_comparison"]
    dw, dh = d["domain_px"]
    mx, my = d["metres_per_px"]
    rows = [
        ["Domain", f"{dw:,} x {dh:,} px "
                   f"({dw*mx/1000:.0f} x {dh*my/1000:.0f} km), "
                   f"{d['cells']/1e6:.1f} M cells"],
        ["Materialise the whole surface",
         f"{m['wall_s']:,.1f} s, peak RSS {m['peak_rss_mb']:,.0f} MB, "
         f"{m['dem_blocks']:,} DEM blocks over {m['reads']:,} reads"],
        ["Dijkstra over the full surface",
         f"{dj['wall_s']:,.1f} s, peak RSS {dj['peak_rss_mb']:,.0f} MB "
         f"({dj['engine']})"],
        ["Ground-truth path",
         f"cost {dj['path_cost']:,.0f} over {dj['path_cells']:,} cells"],
        ["W5 A* demand generator",
         f"cost {ac['astar_cost']:,.0f}, {ac['astar_expanded']:,} cells "
         f"expanded in {ac['astar_wall_s']:,.1f} s, "
         f"{ac['astar_blocks_demanded']:,} DEM blocks demanded"],
        ["A* suboptimality vs oracle",
         f"{ac['suboptimality']*100:+.4f}%"
         if ac.get("suboptimality") is not None else "--"],
    ]
    return md_table(["Quantity", "Value"], rows) + (
        "\n\nPath geometry for both is persisted in `results/w5_oracle.json` "
        "(`path_lonlat`) and in the W5 spec.")


def table_bandwidth() -> str:
    """Same measurements with the response throughput capped, to separate the
    cost of extra round trips from the cost of extra bytes."""
    bw_dir = RES / "runs_bwcap"
    if not bw_dir.exists() or not any(bw_dir.glob("*.json")):
        return "_no bandwidth-capped runs present_"
    base = {}
    for f in sorted((RES / "runs").glob("*.json")):
        d = json.loads(f.read_text())
        cold = next((p for p in d["passes"] if p["pass"] == "cold"), None)
        if cold:
            base[(d["workload"], d["config"], d["latency_ms"])] = cold
    rows = []
    for f in sorted(bw_dir.glob("*.json")):
        d = json.loads(f.read_text())
        cold = next((p for p in d["passes"] if p["pass"] == "cold"), None)
        if not cold:
            continue
        m = cold["metrics"]
        b = base.get((d["workload"], d["config"], d["latency_ms"]))
        un = f"{b['wall_s']:,.1f}s" if b else "--"
        ratio = (f"{cold['wall_s'] / b['wall_s']:.2f}x" if b and b["wall_s"] else "--")
        rows.append([WORKLOAD_TITLE.get(d["workload"], d["workload"]),
                     CFG_LABEL.get(d["config"], d["config"]),
                     f"{m['bytes_fetched']/1e6:,.0f} MB",
                     f"{m['byte_amplification']:.2f}",
                     f"{m['requests']:,}",
                     un, f"{cold['wall_s']:,.1f}s", ratio])
    return md_table(["Workload", "Config", "Bytes fetched", "Bytes x",
                     "Requests", "Wall, uncapped", "Wall, 100 Mbps",
                     "Slowdown"], rows)


def plot_bandwidth():
    bw_dir = RES / "runs_bwcap"
    if not bw_dir.exists() or not any(bw_dir.glob("*.json")):
        return
    base = {}
    for f in sorted((RES / "runs").glob("*.json")):
        d = json.loads(f.read_text())
        c = next((p for p in d["passes"] if p["pass"] == "cold"), None)
        if c:
            base[(d["workload"], d["config"], d["latency_ms"])] = c["wall_s"]
    data: dict[str, dict[str, tuple[float, float]]] = {}
    for f in sorted(bw_dir.glob("*.json")):
        d = json.loads(f.read_text())
        c = next((p for p in d["passes"] if p["pass"] == "cold"), None)
        if not c:
            continue
        u = base.get((d["workload"], d["config"], d["latency_ms"]))
        if u:
            data.setdefault(d["workload"], {})[d["config"]] = (u, c["wall_s"])
    if not data:
        return
    ws = sorted(data)
    cfgs = [c for c in CFG_ORDER if any(c in data[w] for w in ws)]
    fig, axes = plt.subplots(1, len(ws), figsize=(4.0 * len(ws), 3.8),
                             squeeze=False)
    for ax, w in zip(axes[0], ws):
        x = np.arange(len(cfgs))
        un = [data[w].get(c, (0, 0))[0] for c in cfgs]
        cp = [data[w].get(c, (0, 0))[1] for c in cfgs]
        ax.bar(x - 0.19, un, 0.38, label="unlimited bandwidth")
        ax.bar(x + 0.19, cp, 0.38, label="100 Mbps")
        ax.set_xticks(x)
        ax.set_xticklabels([CFG_LABEL.get(c, c).replace("TUNED ", "")
                            for c in cfgs], fontsize=9)
        ax.set_title(WORKLOAD_TITLE.get(w, w), fontsize=10)
        ax.set_ylabel("cold wall time (s)")
        ax.grid(axis="y", alpha=0.25)
    axes[0][-1].legend(fontsize=8)
    fig.suptitle("Chunk size at 50 ms RTT: the trade only appears when "
                 "bandwidth is finite", fontsize=11)
    fig.tight_layout()
    fig.savefig(PLOTS / "bandwidth_tradeoff.png", dpi=150)
    plt.close(fig)


def table_granularity() -> str:
    p = RES / "granularity.json"
    if not p.exists():
        return "_run baseline/granularity.py_"
    d = json.loads(p.read_text())
    rows = []
    for w in sorted(d):
        r = d[w]
        rows.append([WORKLOAD_TITLE.get(w, w),
                     f"{r['unique_requested_px']:,}",
                     f"{r['px_in_blocks_touched']:,}",
                     f"{r['granularity_ratio']:,.1f}x",
                     f"{r['min_total_mb']:,.1f} MB",
                     f"{r['mb_if_reads_were_pixel_exact']:,.2f} MB"])
    return md_table(["Workload", "Unique pixels requested",
                     "Pixels in the blocks touched", "Granularity cost",
                     "Theoretical minimum", "If reads were pixel-exact"], rows)


def table_multithread(rows) -> str:
    """Single-threaded against multi-threaded block decode, same I/O settings."""
    base, mt = {}, {}
    for r in rows:
        if r["phase"] != "cold":
            continue
        if r["config"] == "TUNED_chunk16k":
            base[(r["workload"], r["rtt_ms"])] = r
        elif r["config"] == "TUNED_chunk16k_mt":
            mt[(r["workload"], r["rtt_ms"])] = r
    if not mt:
        return "_no multi-threaded runs present_"
    out = []
    for k in sorted(base):
        b, m = base[k], mt.get(k)
        if not m:
            continue
        out.append([WORKLOAD_TITLE.get(k[0], k[0]), f"{k[1]:g} ms",
                    f"{b['requests']:,}", f"{m['requests']:,}",
                    f"{b['wall_s']:,.1f}s", f"{m['wall_s']:,.1f}s",
                    f"{b['wall_s']/m['wall_s']:.2f}x" if m["wall_s"] else "--"])
    return md_table(["Workload", "RTT", "Requests, 1 thread",
                     "Requests, MT", "Wall, 1 thread", "Wall, MT",
                     "Speedup"], out)


def table_prefix() -> str:
    p = RES / "prefix_decode.json"
    if not p.exists():
        return "_run baseline/prefix_decode.py_"
    d = json.loads(p.read_text())
    out = []
    for k in sorted(d):
        r = d[k]
        out.append([k, f"{r['n_tiles']}",
                    f"{r['mean_compressed_tile_bytes']/1024:,.0f} kB",
                    f"{r['expected_prefix_fraction_single_pixel']*100:.1f}%",
                    f"{r['expected_saving_single_pixel']*100:.1f}%",
                    f"{r['p95_overshoot_of_rowfrac']*100:.1f}%",
                    f"{r['max_overshoot_of_rowfrac']*100:.1f}%",
                    {True: "yes", False: "NO", None: "n/a"}[
                        r.get("truncated_inflate_matches_gdal")]])
    return md_table(["Source", "Tiles", "Mean tile", "Prefix needed",
                     "Saving", "p95 margin", "Worst margin",
                     "Matches GDAL"], out)


def table_checkpoint() -> str:
    p = RES / "gate_checkpoint.json"
    if not p.exists():
        return "_run baseline/gate_checkpoint.py_"
    d = json.loads(p.read_text())
    rows = [["Whole tiles", "--", f"{d['whole_mb']:,.1f} MB", "1.00x",
             f"{d['whole_mb']:,.1f} MB", "1.00x", "none"],
            ["Prefix decode", "--", f"{d['prefix_mb']:,.1f} MB",
             f"{d['whole_mb']/d['prefix_mb']:.2f}x",
             f"{d['prefix_mb']:,.1f} MB",
             f"{d['whole_mb']/d['prefix_mb']:.2f}x", "none"]]
    for span in sorted(d["spans"], key=int):
        v = d["spans"][span]
        rows.append([f"Checkpoint index", f"{int(span)//1024} kB",
                     f"{v['fetch_mb_index_cached']:,.1f} MB",
                     f"{v['speedup_cached']:.2f}x",
                     f"{v['fetch_mb_index_remote']:,.1f} MB",
                     f"{v['speedup_remote']:.2f}x",
                     f"{v['index_pct_of_archive']:.1f}%"])
    return md_table(["Approach", "Span", "Bytes, index cached", "Gain",
                     "Bytes, index fetched", "Gain", "Index size"], rows)


def plot_amp_vs_rtt(rows, metric="byte_amp", fname="amplification_vs_rtt.png"):
    ws = sorted({r["workload"] for r in rows})
    if not ws:
        return
    fig, axes = plt.subplots(1, len(ws), figsize=(3.4 * len(ws), 3.6),
                             sharey=False)
    axes = np.atleast_1d(axes)
    for ax, w in zip(axes, ws):
        for c in CFG_ORDER:
            pts = sorted((r["rtt_ms"], r[metric]) for r in rows
                         if r["workload"] == w and r["config"] == c
                         and r["phase"] == "cold" and r[metric] is not None)
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts],
                        marker="o", ms=4, label=CFG_LABEL.get(c, c))
        ax.axhline(1.0, color="k", ls=":", lw=1)
        ax.set_title(WORKLOAD_TITLE.get(w, w), fontsize=10)
        ax.set_xlabel("injected RTT (ms)")
        ax.set_xscale("log")
        ax.set_xticks([5, 50, 150])
        ax.set_xticklabels(["5", "50", "150"])
        ax.grid(alpha=0.25)
    label = ("bytes fetched / theoretical minimum" if metric == "byte_amp"
             else "requests / theoretical minimum")
    axes[0].set_ylabel(label, fontsize=9)
    axes[-1].legend(fontsize=7, loc="best")
    fig.suptitle("Amplification against the theoretical minimum, cold cache",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(PLOTS / fname, dpi=150)
    plt.close(fig)


def plot_headroom(rows, rtt):
    best = best_tuned(rows, "cold", rtt)
    ws = sorted({k[0] for k in best})
    if not ws:
        return
    bh, rh = [], []
    for w in ws:
        r = best[(w, rtt)]
        bh.append((1 - 1 / r["byte_amp"]) * 100 if r["byte_amp"] else 0)
        rh.append((1 - 1 / r["req_amp"]) * 100 if r["req_amp"] else 0)
    x = np.arange(len(ws))
    fig, ax = plt.subplots(figsize=(1.9 * len(ws) + 2, 4.0))
    ax.bar(x - 0.19, bh, 0.38, label="bytes")
    ax.bar(x + 0.19, rh, 0.38, label="requests")
    ax.axhline(15, color="crimson", ls="--", lw=1.2,
               label="15% cut-off: not worth attacking")
    ax.set_xticks(x)
    ax.set_xticklabels([WORKLOAD_TITLE.get(w, w).replace(" ", "\n", 1)
                        for w in ws], fontsize=9)
    ax.set_ylabel("share of fetched traffic that is avoidable (%)")
    ax.set_title(f"Headroom over best TUNED GDAL at {rtt:g} ms RTT, cold cache")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(PLOTS / "headroom_by_workload.png", dpi=150)
    plt.close(fig)


def plot_attribution(rows, rtt):
    dp = RES / "diagnosis.json"
    if not dp.exists():
        return
    diag = json.loads(dp.read_text())
    best = best_tuned(rows, "cold", rtt)
    ws = sorted({k[0] for k in best})
    cats = ["needed", "redundant", "unneeded", "padding", "header",
            "list_and_other"]
    labels = {"needed": "genuinely required", "redundant": "redundant refetch",
              "unneeded": "blocks never needed", "padding": "range padding",
              "header": "TIFF headers", "list_and_other": "listings, other"}
    data = {c: [] for c in cats}
    keep = []
    for w in ws:
        r = best[(w, rtt)]
        d = diag.get(f"{w}.{r['config']}.rtt{int(rtt)}.cold")
        if not d:
            continue
        keep.append(w)
        for c in cats:
            data[c].append(d["attribution_pct"].get(c, 0.0))
    if not keep:
        return
    x = np.arange(len(keep))
    bottom = np.zeros(len(keep))
    fig, ax = plt.subplots(figsize=(1.9 * len(keep) + 3, 4.2))
    for c in cats:
        v = np.array(data[c])
        ax.bar(x, v, 0.6, bottom=bottom, label=labels[c])
        bottom += v
    ax.set_xticks(x)
    ax.set_xticklabels([WORKLOAD_TITLE.get(w, w).replace(" ", "\n", 1)
                        for w in keep], fontsize=9)
    ax.set_ylabel("share of fetched bytes (%)")
    ax.set_title(f"Where the fetched bytes go, best TUNED GDAL at {rtt:g} ms RTT")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(PLOTS / "byte_attribution.png", dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headline-rtt", type=float, default=50.0)
    args = ap.parse_args()

    PLOTS.mkdir(parents=True, exist_ok=True)
    rows = load_runs()
    csvp = write_csv(rows)
    print(f"{len(rows)} rows -> {csvp}")
    if not rows:
        print("no runs found; nothing to plot")
        return 0

    rtts = sorted({r["rtt_ms"] for r in rows})
    hr = args.headline_rtt if args.headline_rtt in rtts else rtts[len(rtts) // 2]

    parts = {
        "corpus": table_corpus(),
        "environment": table_environment(rows),
        "oracle": table_oracle(),
        "theoretical": table_theoretical(rows),
        "main_cold": table_main(rows, "cold"),
        "main_warm": table_main(rows, "warm"),
        "headroom": table_headroom(rows, hr),
        "attribution": table_attribution(rows, hr),
        "bandwidth": table_bandwidth(),
        "granularity": table_granularity(),
        "multithread": table_multithread(rows),
        "prefix": table_prefix(),
        "checkpoint": table_checkpoint(),
    }
    (RES / "tables.json").write_text(json.dumps(parts, indent=2))
    (RES / "tables.md").write_text(
        "\n\n".join(f"### {k}\n\n{v}" for k, v in parts.items()))

    plot_amp_vs_rtt(rows, "byte_amp", "amplification_vs_rtt.png")
    plot_amp_vs_rtt(rows, "req_amp", "request_amplification_vs_rtt.png")
    plot_headroom(rows, hr)
    plot_attribution(rows, hr)
    plot_bandwidth()
    print(f"tables -> {RES/'tables.md'}   plots -> {PLOTS}  "
          f"(headline RTT {hr:g} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
