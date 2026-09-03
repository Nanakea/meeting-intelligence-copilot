"""Print the provider-free semantic candidate baseline report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.evals.semantic_candidate_eval import (
    evaluate_semantic_annotations,
    render_semantic_eval_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ANNOTATIONS = REPO_ROOT / "evals" / "semantic" / "local-semantic-paraphrases.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", nargs="?", type=Path, default=DEFAULT_ANNOTATIONS)
    args = parser.parse_args()
    print(render_semantic_eval_markdown(evaluate_semantic_annotations(args.annotations)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
