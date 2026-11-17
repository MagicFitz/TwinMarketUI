#!/usr/bin/env python3
"""Deprecated wrapper: use ``python -m market_service sync``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_service.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["sync", *sys.argv[1:]]))
