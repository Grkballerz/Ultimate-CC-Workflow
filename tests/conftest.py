"""pytest configuration — add the memory package and repo root to sys.path."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "memory"))
sys.path.insert(0, str(REPO_ROOT / "bin"))
