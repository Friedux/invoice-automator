# Invoice Automator (macOS)

Benennt Rechnungen in einem Ordner automatisch nach einem frei wählbaren Schema um (Standard `firma_leistung_datum`), z. B. `Telekom_Internet-Flat_03.11.2026.pdf`.
Alles läuft lokal: Text aus PDFs (PyMuPDF), OCR für Scans/Bilder (Apple Vision), Auswertung per KI (Ollama, Standardmodell `qwen2.5:3b`).

## Voraussetzungen
- Mac mit Apple Silicon (M1 oder neuer), mind. 8 GB RAM, ca. 3 GB freier Speicher
- Homebrew (wird bei Bedarf vom Installer nach Rückfrage installiert)
- `git` (macOS bietet die Installation beim ersten Aufruf an)

## Installation
```bash
git clone https://github.com/Friedux/invoice-automator.git
cd invoice-automator
./install.sh
```
Der Installer fragt nach Rechnungsordner, Datumsformat und Namensschema und richtet danach alles ein (Ollama, Python-Umgebung, Modell, Hintergrunddienst).

Optionen für eine Installation ohne Rückfragen:
```bash
./install.sh --yes --folder ~/Documents/Rechnungen --date-format "%d.%m.%Y" --name-pattern "{datum}_{firma}_{leistung}" --model qwen2.5:3b
./install.sh --dry-run   # zeigt nur, was passieren würde
```

Beim ersten Zugriff fragt macOS evtl. nach Ordnerrechten. Bitte erlauben.

## Test ohne Änderungen
```bash
.venv/bin/python rename_invoices.py --dry-run
```

## Funktionsweise
- Ein launchd-Agent startet das Skript, sobald sich der Ordner ändert, sowie täglich um 12:00. Es läuft nur kurz und beendet sich wieder.
- Pro Datei: Text auslesen (bei Scans OCR) → KI ermittelt Firma, Leistung und Rechnungsdatum → Umbenennen.
- Bei Duplikaten wird `_2`, `_3` usw. angehängt.
- Unsichere Dateien (fehlende Angaben, keine Rechnung) werden nach `_pruefen/` verschoben und nicht umbenannt.
- Bereits verarbeitete Dateien werden nicht erneut verarbeitet.
- Log: `rechnungen.log` im Programmordner.

## Konfiguration
`config.yaml` im Programmordner, z. B.:

| Schlüssel | Bedeutung |
|---|---|
| `watch_folder` | Ordner mit den Rechnungen |
| `date_format` | Datumsformat (strftime) für `{datum}`, z. B. `%Y-%m-%d` |
| `name_pattern` | Schema des Dateinamens, siehe unten |
| `model` | Ollama-Modell, z. B. `qwen2.5:7b` für bessere Qualität (braucht mehr RAM) |
| `ollama_url` | z. B. `http://NAS-IP:11434`, um Ollama auf einem anderen Gerät zu nutzen |
| `keep_alive` | `0` gibt den RAM direkt nach jeder Anfrage frei |
| `check_updates` | Benachrichtigung bei neuer Version |

Nach Änderungen an `watch_folder` bitte `./update.sh` ausführen, damit der Hintergrunddienst den neuen Ordner überwacht. Eine Änderung des Modells wird mit `./update.sh` heruntergeladen.

## Namensschema
In `config.yaml` unter `name_pattern`, z. B. `"{datum}_{firma}_{leistung}"`. Die Endung bleibt erhalten.

| Platzhalter | Inhalt |
|---|---|
| `{firma}` | Rechnungssteller |
| `{leistung}` | Kurzbeschreibung der Leistung |
| `{datum}` | Rechnungsdatum im `date_format` |
| `{datum:%Y-%m}` | Rechnungsdatum mit eigenem Format |
| `{jahr}` `{monat}` `{tag}` | Teile des Rechnungsdatums |
| `{rechnungsnummer}` | Rechnungsnummer |
| `{betrag}` | Gesamtbetrag, z. B. `119.00` |

Beispiele: `{jahr}-{monat}_{firma}`, `{firma}_{rechnungsnummer}_{datum}`.
Fehlt ein im Schema verwendeter Wert (z. B. keine Rechnungsnummer erkannt), landet die Datei in `_pruefen/`. Kleine Modelle erkennen Rechnungsnummer und Betrag weniger zuverlässig als Firma und Datum. Ein ungültiges Schema meldet das Programm beim Start. Änderungen gelten ab dem nächsten Lauf und nur für neue Dateien.

## Updates
Das Programm prüft einmal täglich, ob es eine neue Version gibt, und zeigt dann eine macOS-Benachrichtigung. Aktualisieren:
```bash
cd invoice-automator
./update.sh
```
Die `config.yaml` bleibt erhalten. Sofort prüfen: `.venv/bin/python rename_invoices.py --check-update`.

## Deinstallation
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.user.invoice-automator.plist
rm ~/Library/LaunchAgents/com.user.invoice-automator.plist
brew services stop ollama     # optional
brew uninstall ollama         # optional
rm -rf <Programmordner>
```

## Weitergabe an andere Nutzer
**Empfohlen: GitHub-Repository.** Repo anlegen und pushen (`config.yaml`, `.venv` und `state.json` sind per `.gitignore` ausgeschlossen). Der Nutzer führt die Schritte unter *Installation* aus. Bei einem privaten Repo muss er als Collaborator eingeladen sein und sich anmelden (`gh auth login` oder Token). Neue Versionen pushen, `VERSION` erhöhen; der Nutzer wird benachrichtigt und führt `./update.sh` aus.

**Alternative: ZIP.** Entpacken und `./install.sh` ausführen. Falls macOS blockiert:
```bash
chmod +x *.sh
xattr -dr com.apple.quarantine <Ordner>
```
Automatische Updates gibt es dann nicht. Dafür muss die Datei `VERSION` öffentlich erreichbar sein (Rohdatei-URL) und in `config.yaml` als `update_version_url` stehen. Neue ZIPs ersetzt der Nutzer von Hand (`config.yaml` behalten).
