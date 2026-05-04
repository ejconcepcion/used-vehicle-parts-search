#!/usr/bin/env bash
#
# One-shot deployment script for Cloudways (or any Debian/Ubuntu VM).
#
# Run from the project root after SSH'ing in:
#     cd /home/master/applications/<APP_NAME>/private_html/uvps
#     bash deploy/deploy.sh
#
# Idempotent --- safe to re-run after pulling new code. It will:
#   1. Check Python (>= 3.10), install python3-venv if missing
#   2. Create / refresh the virtualenv
#   3. Install / upgrade pip dependencies
#   4. Ensure the data/ directory exists and is writable
#   5. Render deploy/supervisor.conf with your paths and install it under
#      /etc/supervisor/conf.d/uvps.conf (requires sudo)
#   6. Reload supervisor and tail the first few lines of logs
#   7. Print the next-step checklist (Nginx, cron, eBay credentials)
#
# Override defaults via env vars:
#     PORT=8001 ./deploy/deploy.sh
#     SKIP_SMOKE_TEST=1 ./deploy/deploy.sh    # skip "uvicorn --version" check
#     SKIP_SUPERVISOR=1 ./deploy/deploy.sh    # only set up venv + deps
#

set -euo pipefail

# --- Help -----------------------------------------------------------------

usage() {
    cat <<EOF
Usage: bash deploy/deploy.sh [--help]

One-shot deployment for the Used Vehicle Parts Search (uvps) FastAPI app.
Run from the project root after SSH'ing into your Cloudways server.

Environment variable overrides (set before the script, e.g. PORT=8001 bash deploy/deploy.sh):

  PORT                  uvicorn bind port          (default: 8000)
  APP_USER              Linux user that owns files  (default: current user)
  VENV_DIR              Path to the virtualenv      (default: <APP_DIR>/.venv)
  PYTHON_BIN            Explicit python3 binary     (default: auto-detected)
  SUPERVISOR_CONF_DST   Where to install the conf   (default: /etc/supervisor/conf.d/uvps.conf)
  SKIP_SUPERVISOR       Set to 1 to skip supervisor steps (venv + deps only)
  SKIP_SMOKE_TEST       Set to 1 to skip the import + HTTP smoke test

Examples:
  bash deploy/deploy.sh
  PORT=8001 bash deploy/deploy.sh
  SKIP_SUPERVISOR=1 bash deploy/deploy.sh
  PYTHON_BIN=/usr/bin/python3.11 bash deploy/deploy.sh
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $arg"; usage; exit 1 ;;
    esac
done

# --- Resolve paths --------------------------------------------------------

# Project root = parent dir of this script's deploy/ folder.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_USER="${APP_USER:-$(id -un)}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-}"
SUPERVISOR_CONF_DST="${SUPERVISOR_CONF_DST:-/etc/supervisor/conf.d/uvps.conf}"
SUPERVISOR_CONF_SRC="$APP_DIR/deploy/supervisor.conf"
SKIP_SUPERVISOR="${SKIP_SUPERVISOR:-0}"
SKIP_SMOKE_TEST="${SKIP_SMOKE_TEST:-0}"

# --- Pretty output --------------------------------------------------------

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { echo; bold "==> $*"; }

trap 'red "Deployment failed at line $LINENO. See output above."' ERR

# --- Step 1: Locate a workable Python ------------------------------------

step "1/8  Locating Python >= 3.10"

if [ -z "$PYTHON_BIN" ]; then
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
            major="${ver%%.*}"
            minor="${ver##*.}"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                PYTHON_BIN="$candidate"
                break
            fi
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    red "No Python 3.10+ found on PATH."
    yellow "On Cloudways/Ubuntu, install with:"
    echo "    sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip"
    exit 1
fi

green "Using $PYTHON_BIN ($("$PYTHON_BIN" --version))"

# Make sure python3-venv is available; on minimal images the venv module
# can be missing.
if ! "$PYTHON_BIN" -c "import venv" >/dev/null 2>&1; then
    yellow "python3-venv module missing; installing..."
    sudo apt update -qq
    sudo apt install -y "${PYTHON_BIN##*/}-venv" python3-pip
fi

# --- Step 2: virtualenv ---------------------------------------------------

step "2/8  Creating virtualenv at $VENV_DIR"

if [ -d "$VENV_DIR" ]; then
    yellow "Existing venv found --- reusing."
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    green "Created venv."
fi

# Activate for the rest of the script.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# --- Step 3: Install dependencies -----------------------------------------

step "3/8  Installing Python dependencies"

pip install --upgrade --quiet pip
pip install --quiet -r "$APP_DIR/requirements.txt"
green "Dependencies installed."

if [ "$SKIP_SMOKE_TEST" != "1" ]; then
    "$VENV_DIR/bin/uvicorn" --version >/dev/null
    "$VENV_DIR/bin/python" -c "import app.main" >/dev/null
    green "Imports OK."
fi

# --- Step 4: data/ directory ----------------------------------------------

step "4/8  Ensuring data/ directory is writable"

mkdir -p "$APP_DIR/data"
if [ ! -w "$APP_DIR/data" ]; then
    red "$APP_DIR/data is not writable by $(id -un)."
    yellow "Try: sudo chown -R $APP_USER:$APP_USER \"$APP_DIR\""
    exit 1
fi
green "data/ ready."

# --- Step 5: .env file ----------------------------------------------------

step "5/8  Checking .env configuration"

ENV_FILE="$APP_DIR/.env"
ENV_EXAMPLE="$APP_DIR/.env.example"

if [ -f "$ENV_FILE" ]; then
    green ".env already exists --- skipping."
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        green "Created .env from .env.example."
        yellow ""
        yellow "  ACTION REQUIRED: open $ENV_FILE and fill in your settings."
        yellow "  At minimum review:"
        yellow "    EBAY_USE_API      (0 = HTML scraping, 1 = official API)"
        yellow "    EBAY_CLIENT_ID    (needed when EBAY_USE_API=1)"
        yellow "    EBAY_CLIENT_SECRET"
        yellow ""
    else
        yellow ".env.example not found; skipping .env creation."
        yellow "Create $ENV_FILE manually if your app needs environment variables."
    fi
fi

# --- Step 6: render supervisor config -------------------------------------

if [ "$SKIP_SUPERVISOR" = "1" ]; then
    yellow "SKIP_SUPERVISOR=1 --- skipping supervisor steps."
else
    step "6/8  Rendering and installing supervisor config"

    if ! command -v supervisorctl >/dev/null 2>&1; then
        red "supervisorctl not found. Install with: sudo apt install -y supervisor"
        yellow "Or re-run with SKIP_SUPERVISOR=1 to handle service management yourself."
        exit 1
    fi

    RENDERED="$(mktemp)"
    sed \
        -e "s|{APP_USER}|$APP_USER|g" \
        -e "s|{APP_DIR}|$APP_DIR|g" \
        -e "s|{VENV_DIR}|$VENV_DIR|g" \
        -e "s|{PORT}|$PORT|g" \
        "$SUPERVISOR_CONF_SRC" > "$RENDERED"

    bold "  Rendered supervisor config:"
    sed 's/^/    /' "$RENDERED"

    sudo install -m 644 "$RENDERED" "$SUPERVISOR_CONF_DST"
    rm -f "$RENDERED"
    green "Installed at $SUPERVISOR_CONF_DST"

    # --- Step 6: reload + status -----------------------------------------

    step "7/8  Reloading supervisor"

    sudo supervisorctl reread
    sudo supervisorctl update
    sleep 2
    sudo supervisorctl status uvps || true

    if sudo supervisorctl status uvps | grep -q RUNNING; then
        green "uvps is RUNNING on 127.0.0.1:$PORT"
    else
        red "uvps is not running. Last 30 lines of stderr:"
        sudo tail -n 30 /var/log/supervisor/uvps-stderr.log || true
        exit 1
    fi
fi

# --- Step 7: Smoke test the HTTP endpoint --------------------------------

step "8/8  Health check"

if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null; then
        green "GET /api/status returned 200 OK"
    else
        yellow "GET /api/status failed --- the app might still be starting. Try again in 10s."
    fi
else
    yellow "curl not installed; skipping HTTP check."
fi

# --- Done -----------------------------------------------------------------

echo
bold "--------------------------------------------------------------"
green "Deploy complete."
bold "--------------------------------------------------------------"
echo
echo "Local URL:    http://127.0.0.1:$PORT/"
echo "Logs:         sudo tail -f /var/log/supervisor/uvps-stdout.log"
echo "Restart:      sudo supervisorctl restart uvps"
echo "Stop:         sudo supervisorctl stop uvps"
echo
bold "Next steps"
echo "  1. Expose via your domain. Either:"
echo "       (a) open a Cloudways support ticket asking them to add an Nginx"
echo "           reverse-proxy upstream for this app pointing at"
echo "           127.0.0.1:$PORT (paste deploy/nginx-reverse-proxy.conf), or"
echo "       (b) edit /etc/nginx/sites-available/<APP_NAME> yourself,"
echo "           then: sudo nginx -t && sudo systemctl reload nginx"
echo
echo "  2. Enable HTTPS via Cloudways' Application Management -> SSL ->"
echo "     Let's Encrypt, once your domain is pointed at the server."
echo
echo "  3. (Optional) Use Cloudways' Cron Job UI for the daily pipeline run"
echo "     instead of APScheduler. See deploy/crontab-entry.txt."
echo
echo "  4. (Optional) When you have an eBay developer account, copy"
echo "     .env.example to .env, fill in EBAY_CLIENT_ID / EBAY_CLIENT_SECRET,"
echo "     set EBAY_USE_API=1, and:"
echo "         sudo supervisorctl restart uvps"
echo
echo "  5. Run a one-off pipeline now to populate the dashboard:"
echo "         cd $APP_DIR && $VENV_DIR/bin/python -m app.run_now"
echo
