#!/usr/bin/env python3
"""Convert a run's legacy graph pickles to the JSON the analysis API reads.

Unpickling can execute code, so run this only on run directories you trust.
Existing JSON files are left alone; the pickles are kept.

    python scripts/export_graphs_json.py runs/<run_id> [runs/<run_id> ...]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.stores.UserDB import save_graph_json  # noqa: E402


def export_run(run_dir: Path) -> tuple[int, int]:
    """Return how many graphs were written and how many already had JSON."""
    written = existing = 0
    graphs = run_dir / "graphs"
    for path in sorted(graphs.glob("*.pkl")):
        if path.with_suffix(".json").exists():
            existing += 1
            continue
        with path.open("rb") as handle:
            graph = pickle.load(handle)
        if not save_graph_json(graph, path.stem, str(graphs)):
            raise RuntimeError(f"无法写入 {path.with_suffix('.json')}")
        written += 1
    return written, existing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+", type=Path, help="实验输出目录")
    args = parser.parse_args()
    for run_dir in args.run_dirs:
        written, existing = export_run(run_dir)
        print(f"{run_dir}: 新写入 {written} 个，已存在 {existing} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
