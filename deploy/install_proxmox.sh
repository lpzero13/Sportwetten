#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SERVICE_USER="${TIPICO_SERVICE_USER:-tipico}"
SERVICE_GROUP="${TIPICO_SERVICE_GROUP:-$SERVICE_USER}"
ENV_FILE="/etc/default/tipico-observer"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Bitte als root ausführen, z. B.: sudo bash deploy/install_proxmox.sh" >&2
    exit 1
fi

if [[ ! -f "$INSTALL_DIR/requirements.txt" || ! -f "$INSTALL_DIR/app.py" ]]; then
    echo "Das Skript muss aus dem ausgecheckten Sportwetten-Projekt gestartet werden." >&2
    exit 1
fi

if [[ "$INSTALL_DIR" == *'|'* ]]; then
    echo "Der Installationspfad darf kein Pipe-Zeichen enthalten: $INSTALL_DIR" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "Dieses Installationsskript erwartet Debian/Ubuntu (apt-get), z. B. in einem Proxmox-LXC." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates git logrotate

if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_GROUP"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$INSTALL_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install --requirement "$INSTALL_DIR/requirements.txt"

mkdir -p \
    "$INSTALL_DIR/data/raw" \
    "$INSTALL_DIR/data/halftime_reports" \
    "$INSTALL_DIR/logs" \
    "/var/lib/wetten/archive/fotmob/match_core" \
    "/var/lib/wetten/archive/fotmob/period_stats" \
    "/var/lib/wetten/archive/fotmob/shots" \
    "/var/lib/wetten/archive/fotmob/events" \
    "/var/lib/wetten/archive/fotmob/ht_snapshots" \
    "/var/lib/wetten/archive/tipico/snapshots" \
    "/var/lib/wetten/archive/tipico/strategy"

# Quellcode bleibt root-owned; der Dienst erhält Leserechte sowie Schreibrechte
# ausschließlich für seine Laufzeitdaten und Logs.
chown -R root:"$SERVICE_GROUP" "$INSTALL_DIR"
chmod -R g+rX "$INSTALL_DIR"
chown -R "$SERVICE_USER":"$SERVICE_GROUP" "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
chmod -R g+rwX "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
chown -R "$SERVICE_USER":"$SERVICE_GROUP" /var/lib/wetten
chmod -R g+rwX /var/lib/wetten

# Older installations used /var/log/wetten/tipico.log.  Keep that path
# readable/rotatable as well, so an existing deployment cannot remain stuck
# behind the historical `Permission denied` error after the upgrade.
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 /var/log/wetten
if [[ -e /var/log/wetten/tipico.log ]]; then
    chown "$SERVICE_USER":"$SERVICE_GROUP" /var/log/wetten/tipico.log
    chmod 0640 /var/log/wetten/tipico.log
fi

if [[ ! -f "$ENV_FILE" ]]; then
    install -D -m 0640 -o root -g "$SERVICE_GROUP" \
        "$INSTALL_DIR/deploy/tipico-observer.env.example" "$ENV_FILE"
fi

sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-ui.service" \
    > /etc/systemd/system/wetten-ui.service
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-collector.service" \
    > /etc/systemd/system/wetten-collector.service
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-paper.service" \
    > /etc/systemd/system/wetten-paper.service
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-cleanup.service" \
    > /etc/systemd/system/wetten-cleanup.service
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-cleanup.timer" \
    > /etc/systemd/system/wetten-cleanup.timer
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten-fotmob.service" \
    > /etc/systemd/system/wetten-fotmob.service

# Reconcile the V0.5.9.1 production FotMob flags even when an older env file
# already exists.  The helper keeps a timestamped backup and keeps the
# standalone worker disabled because the collector owns the integrated path.
TIPICO_SKIP_SERVICE_RESTART=1 bash "$INSTALL_DIR/deploy/activate_fotmob.sh" "$ENV_FILE"

# Rotate the application log as the same service user that owns the log
# directory.  This avoids the historical `Permission denied` failure when
# logrotate runs with its default user switching rules.
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
    "$INSTALL_DIR/deploy/wetten.logrotate" \
    > /etc/logrotate.d/wetten
chown root:root /etc/logrotate.d/wetten
chmod 0644 /etc/logrotate.d/wetten

# V0.3 service names are retired in favour of the explicit V0.4 names.
systemctl disable --now tipico-observer.service tipico-collector.service 2>/dev/null || true

systemctl daemon-reload
# The integrated collector owns the FotMob production path.  Keep the
# standalone/polling FotMob service disabled; a second worker would duplicate
# provider requests and create conflicting state.
systemctl disable --now wetten-fotmob.service 2>/dev/null || true
systemctl enable wetten-ui.service wetten-collector.service wetten-paper.service wetten-cleanup.timer
systemctl restart wetten-ui.service
systemctl restart wetten-collector.service
systemctl restart wetten-paper.service
systemctl start wetten-cleanup.timer

LXC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Installation abgeschlossen."
echo "Dashboard: http://${LXC_IP:-<LXC-IP>}:8506"
echo "Status:    systemctl status wetten-ui wetten-collector wetten-paper"
echo "Logs:      journalctl -u wetten-ui -u wetten-collector -u wetten-paper -f"
