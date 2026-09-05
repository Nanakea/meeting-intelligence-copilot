"""Fail on high-confidence credential patterns without printing matched values.

This is a source hygiene gate, not a replacement for tenant permission review
or a comprehensive security audit. It scans the current tracked file contents.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{32,}",
    rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    rb"\bgithub_pat_[A-Za-z0-9_]{70,}\b",
    rb"\bxox[baprs]-[A-Za-z0-9-]{30,}\b",
    rb"\bAKIA[0-9A-Z]{16}\b",
)


def contains_credential(data: bytes) -> bool:
    return any(re.search(pattern, data) for pattern in PATTERNS)


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    scanned = flagged = 0
    for name in result.stdout.decode("utf-8").split("\0"):
        if not name:
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        scanned += 1
        if contains_credential(path.read_bytes()):
            flagged += 1
            # Do not expose the matching secret or its containing source line.
            print(f"Credential-shaped content requires review: {name}")
    print(f"Tracked source scan: {scanned} files, {flagged} flagged files")
    return int(flagged > 0)


if __name__ == "__main__":
    raise SystemExit(main())
