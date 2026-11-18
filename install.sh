#!/usr/bin/env bash
# Installiert Abhaengigkeiten, erzeugt die Konfiguration und richtet den launchd-Agent ein.
# Optionen: --dry-run  --yes  --folder PFAD  --date-format FORMAT  --model MODELL
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

FOLDER="" ; DATE_FORMAT="" ; MODEL="" ; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) YES=1 ;;
    --folder) FOLDER="$2"; shift ;;
    --date-format) DATE_FORMAT="$2"; shift ;;
    --model) MODEL="$2"; shift ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) die "Unbekannte Option: $1" ;;
  esac
  shift
done

require_macos
ensure_brew

# ---- Eingaben
if [ -z "$FOLDER" ]; then
  FOLDER="$HOME/Documents/Rechnungen"
  [ "$YES" = 1 ] || { read -r -p "Ordner mit Rechnungen [$FOLDER]: " in; FOLDER="${in:-$FOLDER}"; }
fi
FOLDER="${FOLDER/#\~/$HOME}"

if [ -z "$DATE_FORMAT" ]; then
  DATE_FORMAT="%Y-%m-%d"
  if [ "$YES" != 1 ]; then
    d="$(date +'%Y-%m-%d / %d.%m.%Y / %Y%m%d / %d-%m-%Y')"
    echo "Datumsformat im Dateinamen (heute): $d"
    echo "  1) 2026-11-18   (%Y-%m-%d)"
    echo "  2) 18.11.2026   (%d.%m.%Y)"
    echo "  3) 20261118     (%Y%m%d)"
    echo "  4) 18-11-2026   (%d-%m-%Y)"
    echo "  5) eigenes strftime-Format"
    read -r -p "Auswahl [1]: " c
    case "${c:-1}" in
      1) DATE_FORMAT="%Y-%m-%d" ;;
      2) DATE_FORMAT="%d.%m.%Y" ;;
      3) DATE_FORMAT="%Y%m%d" ;;
      4) DATE_FORMAT="%d-%m-%Y" ;;
      5) read -r -p "Format: " DATE_FORMAT ;;
      *) die "Ungueltige Auswahl" ;;
    esac
  fi
fi
case "$DATE_FORMAT" in */*|*:*) die "Format darf kein '/' oder ':' enthalten (ungueltig in Dateinamen)" ;; esac
MODEL="${MODEL:-qwen2.5:3b}"

# ---- Abhaengigkeiten
info "Installiere Abhaengigkeiten"
ensure_brew_pkg ollama
setup_venv

# ---- Konfiguration
CONFIG="$APP_DIR/config.yaml"
if [ -f "$CONFIG" ] && [ "$YES" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  read -r -p "config.yaml existiert bereits. Ueberschreiben? [j/N] " o
  [ "$o" = j ] || [ "$o" = J ] || { info "Behalte bestehende Konfiguration"; KEEP_CFG=1; }
fi
if [ -z "${KEEP_CFG:-}" ]; then
  info "Schreibe config.yaml (Ordner: $FOLDER, Datum: $DATE_FORMAT, Modell: $MODEL)"
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] rename_invoices.py --write-config --folder '$FOLDER' --date-format '$DATE_FORMAT' --model '$MODEL'"
  else
    "$VENV/bin/python" "$APP_DIR/rename_invoices.py" --write-config \
      --folder "$FOLDER" --date-format "$DATE_FORMAT" --model "$MODEL"
  fi
fi

# ---- Ollama + Modell
if [ "$DRY_RUN" = 1 ]; then OLLAMA_URL="http://localhost:11434"; else OLLAMA_URL="$(cfg_get ollama_url)"; MODEL="$(cfg_get model)"; fi
start_ollama "$OLLAMA_URL"
info "Lade Modell $MODEL (einmalig, ca. 2 GB)"
run ollama pull "$MODEL"

# ---- Agent
[ "$DRY_RUN" = 1 ] || FOLDER="$(cfg_get watch_folder)"
install_agent "$FOLDER"

info "Fertig. Test ohne Aenderungen:  $VENV/bin/python $APP_DIR/rename_invoices.py --dry-run"
echo "Hinweis: Beim ersten Zugriff fragt macOS evtl. nach Zugriff auf den Ordner (z. B. Dokumente) - bitte erlauben."
