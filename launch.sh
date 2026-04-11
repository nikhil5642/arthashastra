#!/usr/bin/env bash
# launch.sh — start the background cache warmer for the fundamental analyst.
#
# The warmer walks the emergent universe (whatever is already in
# data_cache/scores.json), re-fetches live fundamentals + daily closes,
# recomputes deterministic scores, and persists everything. Qualitative
# pillars come from judgment files Claude writes in slash commands;
# the warmer never calls an LLM itself.
#
# Usage:
#   ./launch.sh                # run with output tee'd to data_cache/engine.log
#   ./launch.sh --foreground   # stream to tty only (for systemd / launchd)
#   ./launch.sh -h             # show this help
#
# Environment:
#   DATA_CACHE_DIR  — read by AnalystConfig.from_env; defaults to ./data_cache
#   BRAVE_API_KEY   — optional, only used for the /research-founder skill
#
# Stopping:
#   Ctrl+C sends SIGINT; the Python `run_forever` loop catches it and
#   exits cleanly (persists any in-flight writes, then returns).
#   For unattended operation wrap this in tmux / launchd / systemd.

set -euo pipefail

# Resolve to the project root so relative paths (data_cache/, .env)
# behave the same no matter where the user invoked us from.
cd "$(dirname "$0")"

# ── Args ────────────────────────────────────────────────────────────────────
FOREGROUND=0
for arg in "$@"; do
    case "$arg" in
        --foreground|-f) FOREGROUND=1 ;;
        -h|--help)
            sed -n '1,23p' "$0" | sed -e 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "launch.sh: unknown flag '$arg' (try -h for help)" >&2
            exit 2
            ;;
    esac
done

# ── Pre-flight ──────────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "launch.sh: python3 not found on PATH" >&2
    exit 127
fi

DATA_DIR="${DATA_CACHE_DIR:-data_cache}"
mkdir -p "$DATA_DIR"

SCORES_JSON="$DATA_DIR/scores.json"
if [[ ! -s "$SCORES_JSON" ]]; then
    cat >&2 <<'WARN'
⚠  The cache is empty — the engine will idle until you load at least
   one ticker. In a separate shell:
       python3 -m fundamental_engine refresh RELIANCE
       python3 -m fundamental_engine refresh HDFCBANK
   (or `python3 -m fundamental_engine refresh all` once populated).

WARN
fi

LOG_FILE="$DATA_DIR/engine.log"

echo "fundamental-analyst engine starting"
echo "  data cache : $DATA_DIR"
if [[ "$FOREGROUND" -eq 1 ]]; then
    echo "  log        : stdout only (--foreground)"
else
    echo "  log file   : $LOG_FILE"
fi
echo "  Ctrl+C to stop"
echo

# ── Launch ──────────────────────────────────────────────────────────────────
# With `--foreground`, `exec` replaces this shell with the Python
# process so Ctrl+C / signals land directly on the engine loop with
# no shell-in-the-middle. Otherwise we pipe through tee into the log
# file, preserving python3's exit code via PIPESTATUS.
if [[ "$FOREGROUND" -eq 1 ]]; then
    exec python3 -m fundamental_engine engine
else
    python3 -m fundamental_engine engine 2>&1 | tee -a "$LOG_FILE"
    exit "${PIPESTATUS[0]}"
fi
