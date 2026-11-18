#!/usr/bin/env python3
"""Benennt Rechnungen automatisch nach dem Schema firma_leistung_datum um.

Text: PyMuPDF (digitale PDFs), Apple Vision (Scans/Bilder, nur macOS).
KI:   Ollama (lokal), liefert firma / leistung / rechnungsdatum als JSON.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
STATE_PATH = APP_DIR / "state.json"
LOG_PATH = APP_DIR / "rechnungen.log"
LOCK_PATH = APP_DIR / ".lock"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic"}

DEFAULTS = {
    "watch_folder": "~/Documents/Rechnungen",
    "review_subfolder": "_pruefen",
    "date_format": "%Y-%m-%d",
    "name_pattern": "{firma}_{leistung}_{datum}",
    "ollama_url": "http://localhost:11434",
    "model": "qwen2.5:3b",
    "keep_alive": 0,
    "max_chars": 3000,
    "min_text_chars": 50,
    "stable_seconds": 2,
    "extensions": [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic"],
    "check_updates": True,
    "update_check_hours": 24,
    "update_version_url": "",
}

CONFIG_TEMPLATE = """\
# Konfiguration Invoice Automator (wird von install.sh erzeugt)
watch_folder: "{watch_folder}"      # Ordner mit den Rechnungen
review_subfolder: "_pruefen"        # unsichere Dateien landen hier
date_format: "{date_format}"        # strftime, z. B. %Y-%m-%d oder %d.%m.%Y
name_pattern: "{name_pattern}"   # Schema des Dateinamens, siehe README
                                    # Platzhalter: {{firma}} {{leistung}} {{datum}} {{jahr}} {{monat}} {{tag}}
                                    #   {{rechnungsnummer}} {{betrag}}; Datum frei: {{datum:%Y-%m}}
ollama_url: "{ollama_url}"          # spaeter z. B. http://NAS-IP:11434
model: "{model}"
keep_alive: 0                       # 0 = RAM direkt nach Anfrage freigeben
max_chars: 3000                     # so viel Text geht an die KI
min_text_chars: 50                  # darunter wird OCR verwendet
stable_seconds: 2                   # Datei muss so lange unveraendert sein
extensions: [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic"]
check_updates: true                 # Mac-Benachrichtigung bei neuer Version
update_check_hours: 24
update_version_url: ""              # optional: URL zu einer Rohdatei VERSION (ohne git)
"""

log = logging.getLogger("rechnungen")


# --------------------------------------------------------------------------- config
def load_config(path: Path) -> dict:
    import yaml

    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    cfg["watch_folder"] = Path(str(cfg["watch_folder"])).expanduser()
    validate_pattern(cfg["name_pattern"])
    return cfg


def write_config(path: Path, folder: str, date_format: str, model: str, ollama_url: str,
                 name_pattern: str = DEFAULTS["name_pattern"]) -> None:
    datetime(2000, 1, 2).strftime(date_format)
    validate_pattern(name_pattern)
    path.write_text(
        CONFIG_TEMPLATE.format(
            watch_folder=folder, date_format=date_format, model=model, ollama_url=ollama_url,
            name_pattern=name_pattern,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- state
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            log.warning("state.json beschaedigt, wird neu angelegt")
    return {"processed": [], "last_update_check": 0, "notified_update": ""}


def save_state(state: dict) -> None:
    state["processed"] = state["processed"][-5000:]
    STATE_PATH.write_text(json.dumps(state, indent=1))


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- text extraction
def extract_pdf_text(path: Path, max_pages: int = 2) -> str:
    import pymupdf as fitz

    with fitz.open(path) as doc:
        return "\n".join(doc[i].get_text() for i in range(min(max_pages, len(doc)))).strip()


def vision_ocr(png_bytes: bytes) -> str:
    """OCR ueber Apples Vision-Framework (nur macOS)."""
    import Foundation
    import Vision

    data = Foundation.NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["de-DE", "en-US"])
    request.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision-OCR fehlgeschlagen: {err}")
    lines = [r.topCandidates_(1)[0].string() for r in (request.results() or []) if r.topCandidates_(1)]
    return "\n".join(lines)


def ocr_pdf(path: Path, max_pages: int = 2) -> str:
    import pymupdf as fitz

    texts = []
    with fitz.open(path) as doc:
        for i in range(min(max_pages, len(doc))):
            png = doc[i].get_pixmap(dpi=200).tobytes("png")
            texts.append(vision_ocr(png))
    return "\n".join(texts).strip()


def extract_text(path: Path, min_chars: int) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        text = extract_pdf_text(path)
        if len(text) >= min_chars:
            return text
        log.info("%s: wenig Text, starte OCR", path.name)
        return ocr_pdf(path)
    if ext in IMAGE_EXTS:
        if sys.platform != "darwin":
            raise RuntimeError("OCR fuer Bilder nur auf macOS (Apple Vision) verfuegbar")
        if ext in {".heic", ".tif", ".tiff"}:
            # Vision liest PNG/JPEG sicher; per sips (macOS) konvertieren
            tmp = APP_DIR / ".ocr_tmp.png"
            subprocess.run(["sips", "-s", "format", "png", str(path), "--out", str(tmp)],
                           check=True, capture_output=True)
            try:
                return vision_ocr(tmp.read_bytes())
            finally:
                tmp.unlink(missing_ok=True)
        return vision_ocr(path.read_bytes())
    raise RuntimeError(f"Nicht unterstuetzter Dateityp {ext}")


# --------------------------------------------------------------------------- LLM
PROMPT = """Du bekommst den Text einer Rechnung. Extrahiere:
- ist_rechnung: true/false
- firma: Name des Rechnungsstellers (Aussteller), kurz, ohne Rechtsform wie GmbH/AG
- leistung: Kurzbeschreibung der Leistung/des Produkts in 1-3 Woertern (z. B. "Strom", "Internet", "Hosting", "Reparatur")
- rechnungsdatum: Rechnungsdatum im Format YYYY-MM-DD
- rechnungsnummer: Rechnungsnummer (nur falls klar erkennbar)
- betrag: Gesamtbetrag brutto als Zahl mit Punkt, z. B. 123.45 (ohne Waehrung)

Antworte NUR mit JSON in der Form:
{{"ist_rechnung": true, "firma": "...", "leistung": "...", "rechnungsdatum": "YYYY-MM-DD", "rechnungsnummer": "...", "betrag": 0.0}}
Wenn etwas nicht erkennbar ist, setze den Wert auf null.

Text:
\"\"\"
{text}
\"\"\"
"""


def ask_llm(cfg: dict, text: str) -> dict:
    body = json.dumps({
        "model": cfg["model"],
        "prompt": PROMPT.format(text=text[: cfg["max_chars"]]),
        "stream": False,
        "format": "json",
        "keep_alive": cfg["keep_alive"],
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        cfg["ollama_url"].rstrip("/") + "/api/generate", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = json.loads(resp.read())["response"]
    return json.loads(raw)


# --------------------------------------------------------------------------- naming
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_LEGAL = re.compile(r"\b(gmbh|ag|kg|ug|se|e\.?\s?k\.?|ohg|mbh|co|inc|ltd|llc)\b\.?", re.I)


def clean_part(value: str, max_len: int = 40) -> str:
    value = _LEGAL.sub(" ", value).translate(_TRANSLIT)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value[:max_len].strip("-")


def parse_date(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    try:
        if m:
            d = datetime(int(m[1]), int(m[2]), int(m[3]))
        else:
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
            if not m:
                return None
            d = datetime(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    return d if 2000 <= d.year <= datetime.now().year + 1 else None


PLACEHOLDER = re.compile(r"\{(\w+)(?::([^}]*))?\}")
FIELDS = {"firma", "leistung", "datum", "jahr", "monat", "tag", "rechnungsnummer", "betrag"}


def validate_pattern(pattern: str) -> None:
    names = {m[1] for m in PLACEHOLDER.finditer(pattern)}
    if not names:
        raise ValueError("Schema enthaelt keinen Platzhalter, z. B. {firma}_{leistung}_{datum}")
    unknown = names - FIELDS
    if unknown:
        raise ValueError(f"Unbekannte Platzhalter: {', '.join(sorted(unknown))}. Erlaubt: {', '.join(sorted(FIELDS))}")
    for m in PLACEHOLDER.finditer(pattern):
        if m[2] is not None and m[1] != "datum":
            raise ValueError(f"Format-Angabe nur bei {{datum:...}} erlaubt, nicht bei {{{m[1]}}}")
        if m[2]:
            datetime(2000, 1, 2).strftime(m[2])


def build_name(cfg: dict, data: dict) -> str | None:
    """Gibt den Dateinamen (ohne Endung) zurueck oder None, wenn unsicher."""
    if data.get("ist_rechnung") is False:
        return None
    datum = parse_date(data.get("rechnungsdatum"))
    try:
        betrag = f"{float(data.get('betrag')):.2f}"
    except (TypeError, ValueError):
        betrag = ""
    values = {
        "firma": clean_part(str(data.get("firma") or "")),
        "leistung": clean_part(str(data.get("leistung") or "")),
        "rechnungsnummer": clean_part(str(data.get("rechnungsnummer") or "")),
        "betrag": betrag,
    }
    failed = False

    def fill(m: re.Match) -> str:
        nonlocal failed
        key, fmt = m[1], m[2]
        if key in {"datum", "jahr", "monat", "tag"}:
            if not datum:
                failed = True
                return ""
            return datum.strftime({"jahr": "%Y", "monat": "%m", "tag": "%d"}.get(
                key, fmt or cfg["date_format"]))
        if not values[key]:
            failed = True
        return values[key]

    name = PLACEHOLDER.sub(fill, cfg["name_pattern"])
    name = re.sub(r'[/\\:\x00]', "-", name).strip(" .")
    return None if failed or not name else name


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    target = folder / f"{stem}{ext}"
    n = 2
    while target.exists():
        target = folder / f"{stem}_{n}{ext}"
        n += 1
    return target


# --------------------------------------------------------------------------- processing
def wait_until_stable(path: Path, seconds: float) -> bool:
    try:
        size = path.stat().st_size
        time.sleep(seconds)
        return size == path.stat().st_size and size > 0
    except FileNotFoundError:
        return False


def process_file(cfg: dict, path: Path, state: dict, dry_run: bool, force: bool = False) -> None:
    digest = file_hash(path)
    if not force and digest in state["processed"]:
        return
    review_dir = cfg["watch_folder"] / cfg["review_subfolder"]
    try:
        text = extract_text(path, cfg["min_text_chars"])
        if not text:
            raise RuntimeError("kein Text erkannt")
        stem = build_name(cfg, ask_llm(cfg, text))
    except urllib.error.URLError as e:
        log.error("%s: Ollama nicht erreichbar (%s), wird spaeter erneut versucht", path.name, e)
        return
    except Exception as e:  # noqa: BLE001 - jede Datei einzeln absichern
        log.warning("%s: Fehler (%s)", path.name, e)
        stem = None

    if stem:
        target = unique_path(path.parent, stem, path.suffix.lower())
    else:
        target = unique_path(review_dir, path.stem, path.suffix)
        log.warning("%s: unsicher -> %s/", path.name, cfg["review_subfolder"])

    if dry_run:
        print(f"[dry-run] {path.name}  ->  {target.relative_to(cfg['watch_folder'])}")
        return
    target.parent.mkdir(exist_ok=True)
    path.rename(target)
    log.info("%s -> %s", path.name, target.relative_to(cfg["watch_folder"]))
    if stem:
        state["processed"].append(digest)


def run(cfg: dict, dry_run: bool, only: list[Path] | None, force: bool = False) -> None:
    folder = cfg["watch_folder"]
    if not folder.is_dir():
        log.error("Ordner %s existiert nicht", folder)
        return
    exts = {e.lower() for e in cfg["extensions"]}
    files = only or sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts
                           and not p.name.startswith("."))
    state = load_state()
    for path in files:
        if not wait_until_stable(path, cfg["stable_seconds"]):
            log.info("%s: noch nicht fertig kopiert, uebersprungen", path.name)
            continue
        process_file(cfg, path, state, dry_run, force)
        if not dry_run:
            save_state(state)


# --------------------------------------------------------------------------- updates
def notify(title: str, message: str) -> None:
    if sys.platform == "darwin":
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(message)}" with title "{esc(title)}"'],
                       check=False)
    else:
        print(f"[{title}] {message}")


def local_version() -> str:
    v = APP_DIR / "VERSION"
    return v.read_text().strip() if v.exists() else "0"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(APP_DIR), *args], capture_output=True, text=True,
                          timeout=60, check=True).stdout.strip()


def update_available(cfg: dict) -> str | None:
    """Liefert eine Beschreibung der neuen Version oder None."""
    if (APP_DIR / ".git").exists():
        try:
            _git("fetch", "--quiet")
            behind = int(_git("rev-list", "--count", "HEAD..@{u}"))
            if behind:
                return _git("rev-parse", "--short", "@{u}")
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    url = cfg.get("update_version_url")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                remote = r.read().decode().strip()
            if remote and remote != local_version():
                return remote
        except (urllib.error.URLError, OSError):
            pass
    return None


def check_updates(cfg: dict, force: bool = False) -> None:
    if not cfg["check_updates"]:
        return
    state = load_state()
    if not force and time.time() - state["last_update_check"] < cfg["update_check_hours"] * 3600:
        return
    state["last_update_check"] = time.time()
    new = update_available(cfg)
    if new and (force or state["notified_update"] != new):
        notify("Invoice Automator", f"Update verfuegbar ({new}). Bitte ./update.sh ausfuehren.")
        state["notified_update"] = new
        log.info("Update verfuegbar: %s", new)
    elif not new and force:
        print("Keine Updates verfuegbar.")
    save_state(state)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", type=Path, help="nur diese Dateien verarbeiten")
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts veraendern")
    ap.add_argument("--check-update", action="store_true", help="Update-Pruefung erzwingen")
    ap.add_argument("--force", action="store_true", help="bereits verarbeitete Dateien erneut pruefen")
    ap.add_argument("--write-config", action="store_true", help="config.yaml erzeugen (Installer)")
    ap.add_argument("--folder", default=DEFAULTS["watch_folder"])
    ap.add_argument("--date-format", default=DEFAULTS["date_format"])
    ap.add_argument("--model", default=DEFAULTS["model"])
    ap.add_argument("--name-pattern", default=DEFAULTS["name_pattern"])
    ap.add_argument("--ollama-url", default=DEFAULTS["ollama_url"])
    ap.add_argument("--get", metavar="KEY", help="Konfigurationswert ausgeben (fuer Skripte)")
    args = ap.parse_args()

    if args.write_config:
        write_config(args.config, args.folder, args.date_format, args.model, args.ollama_url,
                     args.name_pattern)
        print(f"Konfiguration geschrieben: {args.config}")
        return 0

    cfg = load_config(args.config)
    if args.get:
        print(cfg[args.get])
        return 0

    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)

    if args.check_update:
        check_updates(cfg, force=True)
        return 0

    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Es laeuft bereits eine Instanz")
            return 0
        run(cfg, args.dry_run, [p.expanduser().resolve() for p in args.files] or None, args.force)
        check_updates(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
