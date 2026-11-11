#!/usr/bin/env bash
# launch.sh — open the Claude-native fundamental-analyst cockpit.
#
# The default mode (`./launch.sh`) just opens an interactive Claude
# Code session with a short primer prompt explaining what's available.
# There is NO background process by default — refresh is on-demand,
# done inside each slash command when the user asks to analyse a
# specific ticker. Nothing gets pre-warmed.
#
# The optional `--engine` mode runs a periodic cache warmer that walks
# every cached ticker and re-fetches screener.in + yfinance. Most users
# don't need this — it's mainly useful if you want `/market-brief` to
# show fresh numbers without invoking refresh yourself, or you're
# monitoring a large universe and want a continuous background tick.
# Use sparingly: every tick costs N live HTTP requests against
# screener.in.
#
# Usage:
#   ./launch.sh              # cockpit: open Claude with a primer prompt
#   ./launch.sh --engine     # engine-only: periodic background refresher
#                            #   with log tail in the current terminal
#   ./launch.sh --foreground # engine-only, no log tee; for systemd / tmux
#   ./launch.sh -h           # this help
#
# Environment:
#   DATA_CACHE_DIR  — read by AnalystConfig.from_env; defaults to ./data_cache
#   BASE_CURRENCY   — your own currency for budgets / FX (default INR)
#   BRAVE_API_KEY   — optional, only used by /research-founder
#
# Manual stop of a stray engine (in case --engine was killed uncleanly):
#   kill -INT "$(cat data_cache/engine.pid)" && rm data_cache/engine.pid

set -euo pipefail

cd "$(dirname "$0")"

# ── Args ────────────────────────────────────────────────────────────────────
MODE="cockpit"   # cockpit | engine | foreground
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            # Print the comment header (everything after the shebang up to
            # the first non-comment line), so the help never drifts.
            awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
            exit 0
            ;;
        --engine)        MODE="engine" ;;
        --foreground|-f) MODE="foreground" ;;
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

# ── Cockpit mode: open Claude, no background engine ─────────────────────────
if [[ "$MODE" == "cockpit" ]]; then
    if [[ "${CLAUDECODE:-}" == "1" ]]; then
        echo "⚠  already inside a Claude Code session — nothing to launch." >&2
        echo "   Use \`./launch.sh --engine\` if you specifically want the" >&2
        echo "   periodic background cache warmer." >&2
        exit 0
    fi
    if ! command -v claude >/dev/null 2>&1; then
        echo "launch.sh: 'claude' CLI not found on PATH." >&2
        echo "Install Claude Code from https://claude.com/claude-code" >&2
        exit 127
    fi

    # Primer prompt — keep short, it's just the session opener. The real
    # playbooks live in .claude/skills/<name>/SKILL.md; this just points
    # Claude at them and restates the load-bearing rules.
    read -r -d '' PRIMER <<'EOF' || true
You're driving the fundamental analyst for global equities in this repo —
US, India, China / Hong Kong, Japan, Europe, and any market Yahoo lists.

Data model:
- Tickers are Yahoo symbols: MSFT, GOOGL, RELIANCE.NS, 0700.HK, 7203.T.
  Always resolve a company name first.
- Fetching is **on-demand** — there is no background warmer running.
  Each slash command refreshes the ticker it operates on before scoring.
- Every analysis also names **early signals**: concerns and strengths the
  newest quarters, the balance sheet, the ownership tape and the week's
  headlines show before any score moves. Read that panel first, and
  answer every URGENT concern by name in your judgment.
- CLAUDE.md at the project root has the project rules. Read "Your role"
  and "The rules" before scoring anything. Non-negotiables: no LLM in
  Python, no hardcoding, anti-optimism defaults, verify verdicts from
  numbers not brand priors, unknown data is never treated as good.

Available slash commands:
  /analyse-stock <TICKER>    full five-pillar deep dive
  /research-founder <name>   leadership research + mode-aware rubric
  /screen-stocks <filters>   screen every listed stock worldwide ("Japan tech, P/E < 15")
  /pick-stocks <amount> [filters]  buy-the-dip picks for a budget, screened globally
  /market-brief              overview of the cached universe + what changed lately
  /refresh-now <TICKER|all>  force live data refresh
  /explain-score <TICKER>    audit trail for a cached score, no re-fetch
  /resolve-ticker <name>     free-text name → ticker on any exchange
  /backtest [suites]         test each scoring claim on up to 30 years of history

Greet the user in one short line and ask what they want to analyse.
Don't pre-run any commands until they ask for something specific.
EOF

    # Linux (VPS / Azure CLI): wrap Claude in tmux so SSH disconnects
    # don't kill the session. macOS → straight exec (iTerm2 handles it).
    if [[ "$(uname)" != "Darwin" ]]; then
        if ! command -v tmux >/dev/null 2>&1; then
            echo "launch.sh: tmux not found on PATH (required on Linux)." >&2
            echo "   install with: sudo apt-get install tmux" >&2
            exit 127
        fi
        SESSION="arthashastra"
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "Session '$SESSION' exists. Reattaching..."
            exec tmux attach -t "$SESSION"
        fi
        # Write primer + launcher to temp files to avoid tmux command-
        # string quoting hell with multi-line heredoc content.
        PRIMER_FILE="$(mktemp "${TMPDIR:-/tmp}/arthashastra-primer.XXXXXX")"
        LAUNCHER="$(mktemp "${TMPDIR:-/tmp}/arthashastra-launcher.XXXXXX.sh")"
        printf '%s' "$PRIMER" > "$PRIMER_FILE"
        cat > "$LAUNCHER" <<LAUNCHEOF
#!/usr/bin/env bash
cd "$(pwd)"
PRIMER_CONTENT="\$(cat '$PRIMER_FILE')"
rm -f '$PRIMER_FILE' '$LAUNCHER'
exec claude "\$PRIMER_CONTENT"
LAUNCHEOF
        chmod +x "$LAUNCHER"
        exec tmux new-session -s "$SESSION" "bash '$LAUNCHER'"
    fi

    exec claude "$PRIMER"
fi

# ── Engine modes ────────────────────────────────────────────────────────────
LOG_FILE="$DATA_DIR/engine.log"
PID_FILE="$DATA_DIR/engine.pid"
SCORES_JSON="$DATA_DIR/scores.json"

# Kill any stale engine from a prior crashed run.
if [[ -f "$PID_FILE" ]]; then
    stale_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$stale_pid" ]] && kill -0 "$stale_pid" 2>/dev/null; then
        echo "Stopping stale engine from previous run (pid $stale_pid)..."
        kill -INT "$stale_pid" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$stale_pid" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$stale_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

if [[ ! -s "$SCORES_JSON" ]]; then
    cat >&2 <<'WARN'
⚠  The cache is empty. The engine will walk 0 tickers per tick until
   you load some. In another shell:
       python3 -m fundamental_engine refresh MSFT
       python3 -m fundamental_engine refresh RELIANCE.NS

WARN
fi

# ── Foreground mode (systemd / launchd / tmux pane) ─────────────────────────
if [[ "$MODE" == "foreground" ]]; then
    echo "fundamental-analyst engine (foreground, interval 900s)"
    echo "  data cache : $DATA_DIR"
    echo "  Ctrl+C to stop"
    echo
    exec python3 -m fundamental_engine engine
fi

# ── Engine mode: background + tail ──────────────────────────────────────────
echo "fundamental-analyst engine (--engine, background + log tail)"
echo "  data cache : $DATA_DIR"
echo "  log file   : $LOG_FILE"

: > "$LOG_FILE"

python3 -m fundamental_engine engine >>"$LOG_FILE" 2>&1 &
ENGINE_PID=$!
echo "$ENGINE_PID" > "$PID_FILE"
echo "  engine pid : $ENGINE_PID"

cleanup() {
    local rc=$?
    trap - EXIT INT TERM HUP
    if [[ -n "${ENGINE_PID:-}" ]] && kill -0 "$ENGINE_PID" 2>/dev/null; then
        echo
        echo "Stopping engine (pid $ENGINE_PID)..."
        kill -INT "$ENGINE_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$ENGINE_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$ENGINE_PID" 2>/dev/null || true
        wait "$ENGINE_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    exit "$rc"
}
trap cleanup EXIT INT TERM HUP

# Fail fast if startup blew up.
sleep 1
if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "launch.sh: engine died during startup — see $LOG_FILE" >&2
    tail -20 "$LOG_FILE" >&2 || true
    exit 1
fi
echo "  engine ok  ·  Ctrl+C to stop"
echo

tail -f "$LOG_FILE"
