#!/usr/bin/env bash
# launch.sh — start the background cache warmer.
#
# Usage:
#   bash launch.sh                # default: sandbox env
#   bash launch.sh sandbox        # explicit sandbox
#   bash launch.sh live           # live env (real API keys + higher budget)
#
# The cache warmer runs the full analyse_pipeline per ticker in the universe
# on a fixed interval, populating data_cache/ so the on-demand CLI commands
# (analyse, pick, brief) return instantly.
#
# Stop with Ctrl+C. For persistent operation, wrap this in tmux / launchd.

set -euo pipefail

cd "$(dirname "$0")"

ANALYST_ENV="${1:-sandbox}"
export ANALYST_ENV

echo "Starting fundamental analyst engine (env=${ANALYST_ENV})"
echo "Logs: data_cache/analyst.log  ·  Ctrl+C to stop."
echo

exec python3 -m fundamental_engine engine
