"""Print the fixed-seed Korean synthetic pilot quality report."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.evals.korean_pilot_corpus import evaluate_korean_pilot_corpus  # noqa: E402


if __name__ == "__main__":
    print(
        json.dumps(
            asdict(evaluate_korean_pilot_corpus()),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
