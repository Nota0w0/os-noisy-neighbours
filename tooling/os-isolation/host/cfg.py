#!/usr/bin/env python3
"""Read config/experiment.toml.

Library use:   from cfg import load; c = load()
Shell use:     python3 host/cfg.py ids.vm          -> 201
               python3 host/cfg.py neighbour.cores -> resolved value (auto -> number)
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "config" / "experiment.toml"


def host_cpus() -> int:
    return os.cpu_count() or 1


def load(path: str | os.PathLike | None = None) -> dict:
    p = Path(path or os.environ.get("EXP_CONFIG", DEFAULT_CONFIG))
    with open(p, "rb") as f:
        c = tomllib.load(f)
    # resolve neighbour.cores = 0 -> round(T/4), at least 1  (DEC-04)
    nb = c.setdefault("neighbour", {})
    if int(nb.get("cores", 0)) <= 0:
        nb["cores"] = max(1, int(host_cpus() / 4 + 0.5))   # half-up, not banker's rounding
    c["_path"] = str(p)
    return c


def neighbour_ids(c: dict) -> list[int]:
    first, count = int(c["ids"]["nbr_first"]), int(c["ids"]["nbr_count"])
    return list(range(first, first + count))


def get(c: dict, dotted: str):
    cur = c
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: cfg.py <dotted.key>", file=sys.stderr)
        sys.exit(2)
    cfg = load()
    try:
        val = get(cfg, sys.argv[1])
    except KeyError:
        print(f"cfg.py: no such key: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    print(" ".join(map(str, val)) if isinstance(val, list) else val)
