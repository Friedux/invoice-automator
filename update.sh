#!/usr/bin/env bash
# Aktualisiert das Tool (git pull), die Abhaengigkeiten, das Modell und den launchd-Agent.
# Die config.yaml bleibt unveraendert. Option: --dry-run
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) die "Unbekannte Option: $a" ;;
  esac
done

require_macos
[ -f "$APP_DIR/config.yaml" ] || [ "$DRY_RUN" = 1 ] || die "Keine config.yaml gefunden - bitte zuerst ./install.sh ausfuehren."
[ -x "$VENV/bin/python" ] || [ "$DRY_RUN" = 1 ] || die "Python-Umgebung fehlt - bitte ./install.sh ausfuehren."

OLD="$(tr -d '[:space:]' < "$APP_DIR/VERSION")"
if [ -d "$APP_DIR/.git" ]; then
  info "Hole Updates (git pull)"
  if [ -n "$(git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]; then
    die "Lokale Aenderungen im Tool-Ordner vorhanden - bitte erst sichern oder zuruecksetzen."
  fi
  run git -C "$APP_DIR" pull --ff-only
else
  warn "Kein git-Repository - bitte neue Version manuell in diesen Ordner kopieren (config.yaml behalten)."
fi

setup_venv
if command -v ollama >/dev/null 2>&1 || [ "$DRY_RUN" = 1 ]; then
  run brew upgrade ollama || true
  if [ "$DRY_RUN" = 1 ]; then
    run ollama pull qwen2.5:3b
    FOLDER="(watch_folder aus config.yaml)"
  else
    start_ollama "$(cfg_get ollama_url)"
    run ollama pull "$(cfg_get model)"
    FOLDER="$(cfg_get watch_folder)"
  fi
fi
install_agent "$FOLDER"

NEW="$(tr -d '[:space:]' < "$APP_DIR/VERSION")"
info "Fertig. Version: $OLD -> $NEW"
