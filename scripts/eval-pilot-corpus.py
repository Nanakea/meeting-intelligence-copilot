"""Print the fixed-seed synthetic internal-pilot quality report."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.pilot_corpus import evaluate_pilot_corpus  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(asdict(evaluate_pilot_corpus()), indent=2, sort_keys=True))
