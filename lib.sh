#!/usr/bin/env bash
# Gemeinsame Funktionen fuer install.sh und update.sh
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.user.invoice-automator"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$APP_DIR/.venv"
DRY_RUN=0

run() {
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi
}

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

require_macos() {
  if [ "$(uname -s)" != "Darwin" ]; then
    [ "$DRY_RUN" = 1 ] && { warn "Kein macOS - Dry-Run laeuft trotzdem durch."; return; }
    die "Dieses Skript ist nur fuer macOS gedacht (Apple Vision, launchd)."
  fi
}

ensure_brew() {
  load_brew_env
  if command -v brew >/dev/null 2>&1; then return; fi
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] Homebrew nicht gefunden -> wuerde mit dem offiziellen Installer von brew.sh installiert"
    return
  fi
  warn "Homebrew ist nicht installiert."
  if [ "${YES:-0}" != 1 ]; then
    read -r -p "Homebrew jetzt installieren (offizielles Skript von brew.sh, fragt nach dem Mac-Passwort)? [J/n] " a
    case "${a:-J}" in n|N) die "Ohne Homebrew kann nicht fortgefahren werden." ;; esac
  fi
  info "Installiere Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew_env
  command -v brew >/dev/null 2>&1 || die "Homebrew-Installation fehlgeschlagen."
}

# Homebrew liegt nicht automatisch im PATH (Apple Silicon: /opt/homebrew)
load_brew_env() {
  command -v brew >/dev/null 2>&1 && return
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && { eval "$("$b" shellenv)"; return 0; }
  done
  return 0
}

ensure_brew_pkg() {
  local pkg="$1"
  if command -v brew >/dev/null 2>&1 && brew list "$pkg" >/dev/null 2>&1; then info "$pkg bereits installiert"
  else info "Installiere $pkg"; run brew install "$pkg"; fi
}

setup_venv() {
  command -v python3 >/dev/null 2>&1 || ensure_brew_pkg python
  info "Richte Python-Umgebung ein"
  [ -d "$VENV" ] || run python3 -m venv "$VENV"
  run "$VENV/bin/pip" install --quiet --upgrade pip
  run "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
}

start_ollama() {
  info "Starte Ollama-Dienst"
  run brew services start ollama
  [ "$DRY_RUN" = 1 ] && return
  for _ in $(seq 1 30); do
    curl -fs "${1:-http://localhost:11434}/api/tags" >/dev/null 2>&1 && return
    sleep 1
  done
  die "Ollama antwortet nicht unter ${1:-http://localhost:11434}"
}

cfg_get() { "$VENV/bin/python" "$APP_DIR/rename_invoices.py" --get "$1"; }

install_agent() {
  local folder="$1"
  info "Richte launchd-Agent ein (laeuft bei Aenderungen im Ordner + taeglich 12:00)"
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] schreibe $PLIST (WatchPaths=$folder)"
    echo "[dry-run] launchctl bootout/bootstrap gui/\$(id -u) $PLIST"
    return
  fi
  mkdir -p "$(dirname "$PLIST")" "$folder"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$APP_DIR/rename_invoices.py</string>
  </array>
  <key>WatchPaths</key><array><string>$folder</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$APP_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/launchd.err.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
}
