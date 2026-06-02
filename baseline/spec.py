#!/usr/bin/env python3
"""The read-spec model shared by every workload, the harness and the analysis.

A workload is an *ordered list of reads*. Each read names an object, an IFD
level, and a pixel window in that level's grid:

    Read(key, level, x, y, w, h)

Keeping the spec concrete has one important consequence for W4 and W5, whose
demand is data-dependent: generation reads real pixels (direct to MinIO, never
through the proxy), the resulting demand order is frozen into the spec, and the
harness then replays that identical order under every config and RTT. The
comparison across configs is therefore exactly like-for-like.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Read:
    key: str
    level: int
    x: int
    y: int
    w: int
    h: int

    def as_list(self) -> list:
        return [self.key, self.level, self.x, self.y, self.w, self.h]

    @staticmethod
    def from_list(v) -> "Read":
        return Read(v[0], int(v[1]), int(v[2]), int(v[3]), int(v[4]), int(v[5]))


@dataclass
class WorkloadSpec:
    name: str
    seed: int
    params: dict
    reads: list[Read] = field(default_factory=list)
    notes: str = ""
    theoretical: dict | None = None

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "name": self.name, "seed": self.seed, "params": self.params,
            "notes": self.notes, "theoretical": self.theoretical,
            "n_reads": len(self.reads),
            "reads": [r.as_list() for r in self.reads],
        }))

    @staticmethod
    def load(path: str | Path) -> "WorkloadSpec":
        d = json.loads(Path(path).read_text())
        return WorkloadSpec(name=d["name"], seed=d["seed"], params=d["params"],
                            reads=[Read.from_list(v) for v in d["reads"]],
                            notes=d.get("notes", ""),
                            theoretical=d.get("theoretical"))
