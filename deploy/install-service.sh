#!/usr/bin/env bash
# --- Install the news channel as a background service ---
#
# WHAT THIS DOES
#   Registers the channel with systemd so it starts automatically on boot and
#   restarts itself if it ever crashes. Also sets up log rotation so the log
#   file cannot grow without limit.
#
# RUN IT WITH
#   sudo bash deploy/install-service.sh
#
# BEFORE YOU RUN IT
#   Make sure the channel works by hand first:
#       python3 main.py check
#       python3 main.py publish --once --limit 1
#   Installing a service that doesn't work just means it fails repeatedly in the
#   background where you can't see it.

set -euo pipefail

PROJECT_DIR="/home/nikita/systems/news-channel"
SERVICE_NAME="news-channel"

echo "Installing ${SERVICE_NAME}..."

# --- Refuse to install something that isn't configured ---
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    echo "❌ No .env file found."
    echo "   Run:  cp ${PROJECT_DIR}/.env.example ${PROJECT_DIR}/.env"
    echo "   then fill in your bot token, channel id and OpenRouter key."
    exit 1
fi

if ! sudo -u nikita python3 "${PROJECT_DIR}/main.py" check >/dev/null 2>&1; then
    echo "⚠️  'main.py check' reported problems. Showing them:"
    sudo -u nikita python3 "${PROJECT_DIR}/main.py" check || true
    echo ""
    read -r -p "   Install anyway? [y/N] " reply
    [[ "${reply}" =~ ^[Yy]$ ]] || exit 1
fi

# --- The service itself ---
install -m 644 "${PROJECT_DIR}/deploy/${SERVICE_NAME}.service" \
    "/etc/systemd/system/${SERVICE_NAME}.service"
echo "   ✅ service file installed"

# --- Log rotation ---
install -m 644 "${PROJECT_DIR}/deploy/${SERVICE_NAME}.logrotate" \
    "/etc/logrotate.d/${SERVICE_NAME}"
echo "   ✅ log rotation configured"

# --- Make sure the folders exist and belong to the right user ---
mkdir -p "${PROJECT_DIR}/logs" "${PROJECT_DIR}/data"
chown -R nikita:nikita "${PROJECT_DIR}/logs" "${PROJECT_DIR}/data"

# --- Start it ---
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

sleep 3
echo ""
systemctl status "${SERVICE_NAME}" --no-pager --lines=15 || true

cat <<EOF

Done. Useful commands:

   sudo systemctl status ${SERVICE_NAME}     is it running?
   sudo systemctl restart ${SERVICE_NAME}    restart after editing config.py
   sudo systemctl stop ${SERVICE_NAME}       stop posting
   tail -f ${PROJECT_DIR}/logs/${SERVICE_NAME}.log
   cd ${PROJECT_DIR} && python3 main.py stats

EOF
