#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 server.py serve --port 8770 --pages sample-pages --open
