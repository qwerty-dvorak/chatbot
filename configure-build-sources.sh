#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="$ROOT_DIR/chatbot-service/Dockerfile"
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

python3 - "$MODE" "$DOCKERFILE" <<'PY'
from pathlib import Path
import sys

mode = sys.argv[1]
path = Path(sys.argv[2])
text = path.read_text()

public_blocks = {
    "BARC BASE IMAGE": "FROM ubuntu:24.04",
    "BARC APT SOURCES": """# RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list
#
# RUN echo "deb http://osrepo.barc.gov.in/ubuntu/ noble main restricted universe multiverse\\n\\
# deb http://osrepo.barc.gov.in/ubuntu/ noble-updates main restricted universe multiverse\\n\\
# deb http://osrepo.barc.gov.in/ubuntu/ noble-security main restricted universe multiverse" > /etc/apt/sources.list""",
    "BARC UV ENV": "# BARC uv mirror variables are disabled in public mode.",
    "BARC UV INSTALL": "RUN pip install --no-cache-dir --break-system-packages uv",
    "BARC UV SYNC": "RUN uv sync --upgrade --no-dev",
}

barc_blocks = {
    "BARC BASE IMAGE": "FROM dregistry.barc.gov.in/ubuntu:24.04",
    "BARC APT SOURCES": """RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list

RUN echo "deb http://osrepo.barc.gov.in/ubuntu/ noble main restricted universe multiverse\\n\\
deb http://osrepo.barc.gov.in/ubuntu/ noble-updates main restricted universe multiverse\\n\\
deb http://osrepo.barc.gov.in/ubuntu/ noble-security main restricted universe multiverse" > /etc/apt/sources.list""",
    "BARC UV ENV": """ENV UV_INDEX_URL=http://osrepo.barc.gov.in/python-pypi/simple \\
    UV_INSECURE_HOST=osrepo.barc.gov.in""",
    "BARC UV INSTALL": """RUN pip install --no-cache-dir --break-system-packages \\
    --index-url http://osrepo.barc.gov.in/pypi/simple \\
    --trusted-host osrepo.barc.gov.in \\
    uv""",
    "BARC UV SYNC": "RUN uv sync --upgrade --no-dev --default-index http://osrepo.barc.gov.in/pypi/simple --allow-insecure-host osrepo.barc.gov.in",
}

blocks = public_blocks if mode == "public" else barc_blocks
for name, replacement in blocks.items():
    start = f"# BEGIN {name}"
    end = f"# END {name}"
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"{path}: expected one {start!r} and one {end!r}")
    before, remainder = text.split(start, 1)
    _, after = remainder.split(end, 1)
    text = f"{before}{start}\n{replacement}\n{end}{after}"

path.write_text(text)
print(f"Configured {path.relative_to(path.parents[1])} for {mode} package sources.")
PY
