#!/usr/bin/env bash
# Refresh every screen snapshot once. The cron entry calls this script, never
# the Python module directly: the repo root and the venv are resolved here, so
# the crontab line cannot drift when the checkout moves.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export MEE_DB_PATH="${MEE_DB_PATH:-$ROOT/ztockz.db}"
export MEE_EXPORT_DIR="${MEE_EXPORT_DIR:-$ROOT/out}"

exec "$ROOT/.venv/bin/python" -m app.screen.refresh --once
