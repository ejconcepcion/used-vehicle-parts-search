#!/usr/bin/env bash
#
# droplet-deploy.sh — Full first-time deployment for a DigitalOcean Droplet
#                     running Ubuntu 22.04.
#
# Run as root after SSH'ing into the Droplet:
#     bash deploy/droplet-deploy.sh
#
# --------------------------------------------------------------------------
# What it does
# --------------------------------------------------------------------------
#   1.  Install system packages (Python, git, supervisor, ufw, nginx, certbot)
#   2.  Create the 'uvps' app user
#   3.  Clone the repo (or accept pre-uploaded files)
#   4.  Create the virtualenv and install Python dependencies
#   5.  Create data/ and write .env (from .env.example or inline env vars)
#   6.  Install and start supervisor → uvicorn
#   7.  Configure Nginx as a reverse proxy on port 80
#   8.  Obtain a free SSL certificate via Certbot  (only if DOMAIN is set)
#   9.  Open ports in UFW
#  10.  HTTP health check
#  11.  Optional: run the initial Row52 + eBay scrape
#
# --------------------------------------------------------------------------
# Environment-variable overrides
# --------------------------------------------------------------------------
#
#   PORT                uvicorn bind port          (default: 8000)
#   APP_USER            Linux app user             (default: uvps)
#   REPO_URL            Git clone URL              (required if not pre-uploaded)
#   DOMAIN              Your domain/subdomain       (e.g. uvps.example.com)
#                         • If set: Nginx vhost uses the domain, Certbot runs
#                         • If unset: Nginx listens on all IPs (IP-only access)
#   SSL_EMAIL           Email for Certbot notices  (required when DOMAIN is set)
#   WITH_NGINX          1 = install Nginx (auto-on when DOMAIN is set)
#                                                   (default: 1)
#   RUN_INITIAL_SCRAPE  1 = run first Row52+eBay pass after deploy
#                                                   (default: 0)
#   EBAY_USE_API        Passed into .env            (default: 0)
#   EBAY_CLIENT_ID      Passed into .env            (default: "")
#   EBAY_CLIENT_SECRET  Passed into .env            (default: "")
#
# --------------------------------------------------------------------------
# Examples
# --------------------------------------------------------------------------
#
#   # Minimal — direct IP access on port 8000 (no Nginx)
#   WITH_NGINX=0 bash deploy/droplet-deploy.sh
#
#   # Default — Nginx reverse proxy on port 80, no SSL
#   bash deploy/droplet-deploy.sh
#
#   # With domain + free SSL
#   DOMAIN=uvps.example.com SSL_EMAIL=you@example.com \
#     bash deploy/droplet-deploy.sh
#
#   # With domain, SSL, and immediately run the first scrape
#   DOMAIN=uvps.example.com SSL_EMAIL=you@example.com \
#     RUN_INITIAL_SCRAPE=1 \
#     bash deploy/droplet-deploy.sh
#
#   # Clone from private repo and run the initial scrape
#   REPO_URL=https://github.com/you/uvps.git RUN_INITIAL_SCRAPE=1 \
#     bash deploy/droplet-deploy.sh
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

usage() {
    cat <<'EOF'
Usage: bash deploy/droplet-deploy.sh [--help]

Full first-time deployment for a DigitalOcean Droplet (Ubuntu 22.04).
Must be run as root.

Environment variables (set before calling the script):

  PORT                uvicorn bind port            (default: 8000)
  APP_USER            Linux user for the app       (default: uvps)
  REPO_URL            Git clone URL                (needed if code not yet on server)
  WITH_NGINX          1 = install Nginx proxy      (default: 1)
  DOMAIN              domain/subdomain for Nginx   (e.g. uvps.example.com)
  SSL_EMAIL           email for Certbot            (required when DOMAIN is set)
  RUN_INITIAL_SCRAPE  1 = run scrape after deploy  (default: 0)
  EBAY_USE_API        0 or 1                       (default: 0)
  EBAY_CLIENT_ID      eBay developer client ID     (default: "")
  EBAY_CLIENT_SECRET  eBay developer secret        (default: "")

Examples:
  # IP-only, no Nginx
  WITH_NGINX=0 bash deploy/droplet-deploy.sh

  # Nginx on port 80 (default)
  bash deploy/droplet-deploy.sh

  # Full stack: Nginx + SSL + initial scrape
  DOMAIN=uvps.example.com SSL_EMAIL=you@example.com RUN_INITIAL_SCRAPE=1 \
    bash deploy/droplet-deploy.sh
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $arg"; usage; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT="${PORT:-8000}"
APP_USER="${APP_USER:-uvps}"
REPO_URL="${REPO_URL:-}"
WITH_NGINX="${WITH_NGINX:-1}"
DOMAIN="${DOMAIN:-}"
SSL_EMAIL="${SSL_EMAIL:-}"
RUN_INITIAL_SCRAPE="${RUN_INITIAL_SCRAPE:-0}"

EBAY_USE_API="${EBAY_USE_API:-0}"
EBAY_CLIENT_ID="${EBAY_CLIENT_ID:-}"
EBAY_CLIENT_SECRET="${EBAY_CLIENT_SECRET:-}"

APP_DIR="/home/$APP_USER/uvps"
VENV_DIR="$APP_DIR/.venv"

# Domain implies Nginx
[[ -n "$DOMAIN" ]] && WITH_NGINX=1

# When Nginx proxies, uvicorn only needs to listen locally
if [[ "$WITH_NGINX" == "1" ]]; then
    UVICORN_HOST="127.0.0.1"
else
    UVICORN_HOST="0.0.0.0"
fi

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
step()  { echo; bold "==> $*"; }

trap 'red "Deployment failed at line $LINENO — see output above."' ERR

# ---------------------------------------------------------------------------
# Must run as root
# ---------------------------------------------------------------------------

if [[ "$(id -u)" -ne 0 ]]; then
    red "This script must be run as root."
    yellow "Try: sudo bash deploy/droplet-deploy.sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate: SSL requires a domain and email
# ---------------------------------------------------------------------------

if [[ -n "$DOMAIN" && -z "$SSL_EMAIL" ]]; then
    red "SSL_EMAIL is required when DOMAIN is set."
    yellow "Re-run with: SSL_EMAIL=you@example.com DOMAIN=$DOMAIN bash deploy/droplet-deploy.sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# Print plan
# ---------------------------------------------------------------------------

echo
bold "======================================================================"
bold " Used Vehicle Parts Search — Droplet Deployment"
bold "======================================================================"
echo "  App user   : $APP_USER"
echo "  App dir    : $APP_DIR"
echo "  Port       : $PORT"
echo "  Nginx      : $WITH_NGINX"
if [[ -n "$DOMAIN" ]]; then
echo "  Domain     : $DOMAIN  (SSL via Certbot)"
fi
echo "  Initial scrape: $RUN_INITIAL_SCRAPE"
echo

# ---------------------------------------------------------------------------
# Step 1: System dependencies
# ---------------------------------------------------------------------------

step "1/10  Installing system dependencies"

apt-get update -qq

PKGS="python3 python3-venv python3-pip git curl supervisor ufw"
[[ "$WITH_NGINX" == "1" ]] && PKGS="$PKGS nginx"
[[ -n "$DOMAIN"   ]] && PKGS="$PKGS certbot python3-certbot-nginx"

# shellcheck disable=SC2086
apt-get install -y -qq $PKGS

green "System packages installed."

# Verify Python 3.10+
PYTHON_BIN=$(command -v python3)
PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
    red "Python 3.10+ required. Found: $PY_VERSION"
    exit 1
fi
green "Python $PY_VERSION OK."

# ---------------------------------------------------------------------------
# Step 2: App user
# ---------------------------------------------------------------------------

step "2/10  App user '$APP_USER'"

if id "$APP_USER" &>/dev/null; then
    yellow "User '$APP_USER' already exists — skipping."
else
    useradd -m -s /bin/bash "$APP_USER"
    green "User '$APP_USER' created."
fi

# ---------------------------------------------------------------------------
# Step 3: Clone or locate app code
# ---------------------------------------------------------------------------

step "3/10  Deploying application code"

if [[ -d "$APP_DIR/.git" ]]; then
    yellow "Repo already exists at $APP_DIR — pulling latest."
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
    green "Code updated."
elif [[ -d "$APP_DIR" && -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
    yellow "Files already present at $APP_DIR (assumed SCP/SFTP upload) — using them."
else
    if [[ -z "$REPO_URL" ]]; then
        red "No code found at $APP_DIR and REPO_URL is not set."
        yellow "Either:"
        yellow "  1. Set REPO_URL=https://github.com/you/uvps.git and re-run, or"
        yellow "  2. Upload files to $APP_DIR via SCP first, then re-run."
        yellow ""
        yellow "  SCP example (run on your local machine):"
        yellow "    scp -r /path/to/uvps/ root@<droplet-ip>:$APP_DIR"
        exit 1
    fi
    sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
    green "Repo cloned."
fi

# ---------------------------------------------------------------------------
# Step 4: Virtualenv + dependencies
# ---------------------------------------------------------------------------

step "4/10  Python virtualenv + dependencies"

if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
    green "Virtualenv created."
else
    yellow "Virtualenv already exists — reusing."
fi

sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade --quiet pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
green "Python dependencies installed."

# Smoke-test: ensure app.main imports cleanly
sudo -u "$APP_USER" \
    env PYTHONPATH="$APP_DIR" \
    "$VENV_DIR/bin/python" -c "import app.main" \
    && green "Import smoke test passed." \
    || { red "Import failed — check requirements.txt and app code."; exit 1; }

# ---------------------------------------------------------------------------
# Step 5: data/ directory and .env
# ---------------------------------------------------------------------------

step "5/10  Environment configuration"

sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"
green "data/ directory ready."

ENV_FILE="$APP_DIR/.env"
ENV_EXAMPLE="$APP_DIR/.env.example"

if [[ -f "$ENV_FILE" ]]; then
    yellow ".env already exists — not overwriting."
else
    # Build .env from env vars passed to this script, or fall back to .env.example
    if [[ -n "$EBAY_CLIENT_ID" || -n "$EBAY_CLIENT_SECRET" || "$EBAY_USE_API" == "1" ]]; then
        sudo -u "$APP_USER" tee "$ENV_FILE" > /dev/null <<ENVEOF
EBAY_USE_API=$EBAY_USE_API
EBAY_CLIENT_ID=$EBAY_CLIENT_ID
EBAY_CLIENT_SECRET=$EBAY_CLIENT_SECRET
ENVEOF
        green ".env written from environment variables."
    elif [[ -f "$ENV_EXAMPLE" ]]; then
        sudo -u "$APP_USER" cp "$ENV_EXAMPLE" "$ENV_FILE"
        green ".env created from .env.example."
        echo
        yellow "  ACTION REQUIRED: fill in $ENV_FILE before running a scrape."
        yellow "    EBAY_USE_API=0 means HTML scraping (no credentials needed)."
        yellow "    Set EBAY_USE_API=1 and add EBAY_CLIENT_ID/SECRET for the official API."
        echo
    else
        yellow ".env.example not found — create $ENV_FILE manually if needed."
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Supervisor → uvicorn
# ---------------------------------------------------------------------------

step "6/10  Supervisor + uvicorn"

cat > /etc/supervisor/conf.d/uvps.conf <<SUPEOF
[program:uvps]
command=$VENV_DIR/bin/uvicorn app.main:app --host $UVICORN_HOST --port $PORT --workers 2
directory=$APP_DIR
user=$APP_USER
autostart=true
autorestart=true
startsecs=5
stopwaitsecs=20
stdout_logfile=/var/log/supervisor/uvps-stdout.log
stderr_logfile=/var/log/supervisor/uvps-stderr.log
environment=PYTHONUNBUFFERED="1"
SUPEOF

green "Supervisor config written."

systemctl enable supervisor --quiet
systemctl start supervisor 2>/dev/null || true

supervisorctl reread
supervisorctl update

sleep 3
supervisorctl status uvps || true

if supervisorctl status uvps | grep -q RUNNING; then
    green "uvps is RUNNING on $UVICORN_HOST:$PORT"
else
    red "uvps is not running. Last 30 lines of stderr:"
    tail -n 30 /var/log/supervisor/uvps-stderr.log || true
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 7: Nginx reverse proxy (optional)
# ---------------------------------------------------------------------------

step "7/10  Nginx configuration"

if [[ "$WITH_NGINX" == "1" ]]; then

    # Remove default site if it's still the placeholder
    if [[ -f /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
        yellow "Removed default Nginx site."
    fi

    NGINX_CONF="/etc/nginx/sites-available/uvps"

    if [[ -n "$DOMAIN" ]]; then
        # Named vhost — Certbot will add SSL directives in Step 8
        cat > "$NGINX_CONF" <<NGINXEOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location / {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_read_timeout 90s;
    }
}
NGINXEOF
    else
        # IP-only vhost
        cat > "$NGINX_CONF" <<NGINXEOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_read_timeout 90s;
    }
}
NGINXEOF
    fi

    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/uvps

    nginx -t && systemctl reload nginx
    green "Nginx configured and reloaded."

else
    yellow "Skipping Nginx (WITH_NGINX=0)."
fi

# ---------------------------------------------------------------------------
# Step 8: SSL via Certbot (only when DOMAIN is set)
# ---------------------------------------------------------------------------

step "8/10  SSL certificate"

if [[ -n "$DOMAIN" ]]; then
    green "Requesting SSL certificate for $DOMAIN ..."
    certbot --nginx \
        --non-interactive \
        --agree-tos \
        --email "$SSL_EMAIL" \
        --redirect \
        -d "$DOMAIN"
    green "SSL certificate obtained and Nginx updated for HTTPS redirect."
else
    yellow "DOMAIN not set — skipping SSL. To add SSL later:"
    if [[ "$WITH_NGINX" == "1" ]]; then
        yellow "  apt install -y certbot python3-certbot-nginx"
        yellow "  certbot --nginx -d yourdomain.com"
    else
        yellow "  Set WITH_NGINX=1 DOMAIN=yourdomain.com and re-run this script."
    fi
fi

# ---------------------------------------------------------------------------
# Step 9: UFW firewall rules
# ---------------------------------------------------------------------------

step "9/10  Firewall (UFW)"

ufw allow OpenSSH >/dev/null

if [[ "$WITH_NGINX" == "1" ]]; then
    ufw allow 'Nginx Full' >/dev/null   # ports 80 + 443
    green "UFW: OpenSSH + Nginx Full (80, 443) allowed."
else
    ufw allow "$PORT/tcp" >/dev/null
    green "UFW: OpenSSH + port $PORT allowed."
fi

if ! ufw status | grep -q "Status: active"; then
    ufw --force enable >/dev/null
    green "UFW enabled."
else
    yellow "UFW already active — rules added."
fi

# ---------------------------------------------------------------------------
# Step 10: Health check
# ---------------------------------------------------------------------------

step "10/10  Health check"

sleep 2

# Always check the internal uvicorn endpoint
if curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null; then
    green "Internal: GET http://127.0.0.1:$PORT/api/status → 200 OK"
else
    yellow "Internal health check failed — the app may still be warming up."
    yellow "Check: tail -f /var/log/supervisor/uvps-stdout.log"
fi

# If Nginx is on, also check port 80
if [[ "$WITH_NGINX" == "1" ]]; then
    if curl -fsS "http://127.0.0.1/api/status" >/dev/null; then
        green "Nginx proxy: GET http://127.0.0.1/api/status → 200 OK"
    else
        yellow "Nginx proxy check failed — run: nginx -t && systemctl status nginx"
    fi
fi

# ---------------------------------------------------------------------------
# Optional: initial scrape
# ---------------------------------------------------------------------------

if [[ "$RUN_INITIAL_SCRAPE" == "1" ]]; then
    echo
    bold "==> Running initial Row52 + eBay scrape (this takes a few minutes…)"
    sudo -u "$APP_USER" \
        env HOME="/home/$APP_USER" \
        "$VENV_DIR/bin/python" -m app.run_now \
        && green "Initial scrape complete — dashboard is ready with data." \
        || yellow "Initial scrape exited with an error. Check logs and re-run manually:"
    yellow "  sudo -u $APP_USER $VENV_DIR/bin/python -m app.run_now"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

DROPLET_IP=$(curl -fsS http://ifconfig.me 2>/dev/null || echo "<droplet-ip>")

echo
bold "======================================================================"
green " Deployment complete!"
bold "======================================================================"
echo

if [[ -n "$DOMAIN" ]]; then
    echo "  Dashboard:    https://$DOMAIN/"
    echo "  API status:   https://$DOMAIN/api/status"
elif [[ "$WITH_NGINX" == "1" ]]; then
    echo "  Dashboard:    http://$DROPLET_IP/"
    echo "  API status:   http://$DROPLET_IP/api/status"
else
    echo "  Dashboard:    http://$DROPLET_IP:$PORT/"
    echo "  API status:   http://$DROPLET_IP:$PORT/api/status"
fi

echo

if [[ "$RUN_INITIAL_SCRAPE" != "1" ]]; then
    bold "Next: run the initial scrape to populate the dashboard"
    echo "  sudo -u $APP_USER $VENV_DIR/bin/python -m app.run_now"
    echo
fi

bold "Useful commands"
echo "  App status:    supervisorctl status uvps"
echo "  Live logs:     tail -f /var/log/supervisor/uvps-stdout.log"
echo "  Error logs:    tail -f /var/log/supervisor/uvps-stderr.log"
echo "  Restart app:   supervisorctl restart uvps"
echo "  Manual scrape: sudo -u $APP_USER $VENV_DIR/bin/python -m app.run_now"
echo "  Update app:    bash $APP_DIR/deploy/droplet-update.sh"
echo
