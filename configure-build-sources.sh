#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-apply}"

case "$MODE" in
  apply|public)
    MODE="public"
    ;;
  revert|barc)
    MODE="barc"
    ;;
  *)
    echo "Usage: bash configure-build-sources.sh [apply|revert]" >&2
    exit 2
    ;;
esac

mapfile -t DOCKERFILES < <(find "$ROOT_DIR" -type f -name 'Dockerfile*' \
  -not -path '*/.git/*' -not -path '*/.venv/*' | sort)

python3 - "$MODE" "$ROOT_DIR" "${DOCKERFILES[@]}" <<'PY'
from pathlib import Path
import sys

mode = sys.argv[1]
root = Path(sys.argv[2])
paths = [Path(value) for value in sys.argv[3:]]

apt_public = """# Public Ubuntu sources from the base image remain enabled."""
apt_barc = """RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list

RUN echo "deb http://osrepo.barc.gov.in/ubuntu/ noble main restricted universe multiverse\\n\\
deb http://osrepo.barc.gov.in/ubuntu/ noble-updates main restricted universe multiverse\\n\\
deb http://osrepo.barc.gov.in/ubuntu/ noble-security main restricted universe multiverse" > /etc/apt/sources.list"""

public_blocks = {
    "BARC BASE IMAGE": "FROM ubuntu:24.04",
    "BARC APT SOURCES": apt_public,
    "BARC UV ENV": "# Public PyPI is used.",
    "BARC UV INSTALL": "RUN pip install --no-cache-dir --break-system-packages uv",
    "BARC UV SYNC": "RUN uv sync --frozen --no-dev",
    "BARC PYMILVUS INSTALL": (
        "RUN pip install --no-cache-dir --break-system-packages pymilvus"
    ),
}

barc_blocks = {
    "BARC BASE IMAGE": "FROM dregistry.megh.barc.gov.in/ubuntu:24.04",
    "BARC APT SOURCES": apt_barc,
    "BARC UV ENV": """ENV UV_INDEX_URL=http://osrepo.barc.gov.in/python-pypi/simple \\
    UV_INSECURE_HOST=osrepo.barc.gov.in""",
    "BARC UV INSTALL": """RUN pip install --no-cache-dir --break-system-packages \\
    --index-url http://osrepo.barc.gov.in/python-pypi/simple \\
    --trusted-host osrepo.barc.gov.in \\
    uv""",
    "BARC UV SYNC": """RUN uv sync --frozen --no-dev \\
    --default-index http://osrepo.barc.gov.in/python-pypi/simple \\
    --allow-insecure-host osrepo.barc.gov.in""",
    "BARC PYMILVUS INSTALL": """RUN pip install --no-cache-dir --break-system-packages \\
    --index-url http://osrepo.barc.gov.in/python-pypi/simple \\
    --trusted-host osrepo.barc.gov.in \\
    pymilvus""",
}

blocks = public_blocks if mode == "public" else barc_blocks
configured = 0

for path in paths:
    text = path.read_text()
    for required in ("BARC BASE IMAGE", "BARC APT SOURCES"):
        start = f"# BEGIN {required}"
        end = f"# END {required}"
        if text.count(start) != 1 or text.count(end) != 1:
            raise SystemExit(
                f"{path.relative_to(root)}: expected one {start!r} and one {end!r}"
            )

    for name, replacement in blocks.items():
        start = f"# BEGIN {name}"
        end = f"# END {name}"
        start_count = text.count(start)
        end_count = text.count(end)
        if start_count == 0 and end_count == 0:
            continue
        if start_count != 1 or end_count != 1:
            raise SystemExit(
                f"{path.relative_to(root)}: expected matching {start!r} and {end!r}"
            )
        before, remainder = text.split(start, 1)
        _, after = remainder.split(end, 1)
        text = f"{before}{start}\n{replacement}\n{end}{after}"

    path.write_text(text)
    print(f"Configured {path.relative_to(root)} for {mode} sources.")
    configured += 1

print(f"Configured {configured} Dockerfile(s).")
PY
