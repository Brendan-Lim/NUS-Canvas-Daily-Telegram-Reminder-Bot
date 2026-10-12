"""Conservative offline screening; prints only counts, never matched data.

Checks every available Git blob (including unreachable objects) and tracked files.
This is a heuristic, not proof of absence; review fictional fixtures separately.
"""

import re
import subprocess
from pathlib import Path

PATTERNS = [
    rb"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b",
    rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"(?i)(?:CANVAS_TOKEN|TELEGRAM_BOT_TOKEN)\s*=\s*[\"']?[A-Za-z0-9~_-]{30,}",
    rb"(?i)\b[A-Z0-9._%+-]+@(?:u\.)?nus\.edu\.sg\b",
    rb"\b[A-Z]\d{7}[A-Z]\b",  # typical student identifiers
]


def main():
    objects = subprocess.check_output(
        ["git", "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"]
    ).splitlines()
    blobs = [row.split()[0] for row in objects if row.endswith(b" blob")]
    data = [subprocess.check_output(["git", "cat-file", "blob", oid]) for oid in blobs]
    paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    data += [Path(p.decode()).read_bytes() for p in paths if p and Path(p.decode()).is_file()]
    findings = sum(any(re.search(pattern, value) for pattern in PATTERNS) for value in data)
    print(
        f"Screened {len(blobs)} available Git blobs and {len(paths) - 1} tracked paths; suspect objects/files: {findings}"
    )
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
