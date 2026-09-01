#!/usr/bin/env bash
set -Eeuo pipefail

# Enable the explicit FotMob date-range import in an already installed
# container.  This deliberately does not enable the permanent FotMob worker.
ENV_FILE="${1:-/etc/default/tipico-observer}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Bitte als root ausführen, z. B.: sudo bash deploy/activate_fotmob.sh" >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_TEMPLATE="$SCRIPT_DIR/tipico-observer.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
    install -D -m 0640 "$ENV_TEMPLATE" "$ENV_FILE"
    echo "Neue Konfiguration angelegt: $ENV_FILE"
else
    BACKUP_FILE="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp --preserve=mode,ownership,timestamps "$ENV_FILE" "$BACKUP_FILE"
    chmod 0600 "$BACKUP_FILE"
    echo "Vorherige Konfiguration gesichert: $BACKUP_FILE"
fi

set_env_value() {
    local key="$1"
    local value="$2"
    # The daily endpoint contains an ampersand.  Escape sed replacement
    # metacharacters so an existing env file is updated literally.
    local escaped_value="${value//\\/\\\\}"
    escaped_value="${escaped_value//&/\\&}"
    escaped_value="${escaped_value//|/\\|}"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i -E "s|^${key}=.*$|${key}=${escaped_value}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

# The date-range button is an explicit manual action.  The worker remains
# disabled below, even though the shared FotMob feature is enabled.
set_env_value FOTMOB_ENABLED true
set_env_value FOTMOB_HISTORY_ENABLED true
set_env_value FOTMOB_NETWORK_MODE manual
set_env_value STORE_FOTMOB_HISTORICAL_RAW false
set_env_value FOTMOB_ARCHIVE_ROOT /var/lib/wetten/archive/fotmob
# The date-range UI uses FotMob's complete daily feed.  The legacy league
# setting below remains only for the older explicit CLI subcommands.
set_env_value FOTMOB_MATCH_DETAILS_PATH '/data/matchDetails?matchId={match_id}'
set_env_value FOTMOB_DAILY_MATCHES_PATH '/data/matches?date={date}&timezone={timezone}&ccode3={ccode3}&includeNextDayLateNight=true'
set_env_value FOTMOB_ALL_LEAGUES_PATH '/data/allLeagues?locale={locale}&country={country}'
set_env_value FOTMOB_DAILY_TIMEZONE Europe/Berlin
set_env_value FOTMOB_DAILY_CCODE3 DEU
set_env_value FOTMOB_DAILY_LOCALE de
set_env_value FOTMOB_RATE_MODE adaptive
set_env_value FOTMOB_INITIAL_RPS 5
set_env_value FOTMOB_RPS_STEP 5
set_env_value FOTMOB_MIN_RPS 0.5
set_env_value FOTMOB_MAX_RPS 100
set_env_value FOTMOB_INITIAL_WORKERS 10
set_env_value FOTMOB_MAX_WORKERS 40
set_env_value FOTMOB_RATE_WINDOW_REQUESTS 20
set_env_value FOTMOB_RATE_COOLDOWN_SECONDS 5
set_env_value FOTMOB_CONNECTION_POOL_SIZE 40
set_env_value FOTMOB_PERFORMANCE_REQUESTS_PER_LEVEL 25
set_env_value FOTMOB_PERFORMANCE_WORKER_LEVELS 10,20,30,40
set_env_value FOTMOB_PERFORMANCE_STABLE_CONFIRMATIONS 2
set_env_value FOTMOB_HISTORY_WORKERS 10
set_env_value FOTMOB_HISTORY_REQUESTS_PER_SECOND 5
set_env_value FOTMOB_HISTORY_LEAGUE_ID 54
set_env_value FOTMOB_HT_ENRICHMENT_ENABLED true

if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now wetten-fotmob.service 2>/dev/null || true
    if [[ "${TIPICO_SKIP_SERVICE_RESTART:-0}" != "1" ]] && systemctl cat wetten-ui.service >/dev/null 2>&1; then
        systemctl daemon-reload
        systemctl restart wetten-ui.service
        echo "wetten-ui.service neu gestartet."
    fi
fi

echo "FotMob ist für manuelle Datumsbereich-Läufe aktiviert."
echo "Kein permanenter FotMob-Worker wurde aktiviert."
