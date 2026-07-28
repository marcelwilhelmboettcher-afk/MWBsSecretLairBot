#!/usr/bin/env python3
"""
Secret Lair Tracker (kostenlose Version, ohne API-Kosten)
-----------------------------------------------------------
1. Prueft mehrere offizielle & Community-Quellen auf neue/geaenderte Inhalte.
2. Zerlegt jede Seite anhand ihrer Ueberschriften (h1-h4) in Abschnitte und
   sucht darin per Regex/Schluesselwortliste nach Preis, Datum und bekannten
   Crossover-IPs (Final Fantasy, Marvel, LOTR, etc.).
3. Pflegt eine persistente Liste aller bekannten Secret Lairs (secret_lairs.json).
4. Erzeugt daraus einen iCalendar-Feed (docs/calendar.ics) fuers iPhone.
5. Schickt bei neuen/aktualisierten Eintraegen eine Telegram-Nachricht.

Kein bezahlpflichtiger Dienst wird verwendet: nur requests, BeautifulSoup und
python-dateutil (alles kostenlose Open-Source-Bibliotheken), plus der
kostenlose GitHub-Actions-Free-Tier und die kostenlose Telegram-Bot-API.

WICHTIG: Die Erkennung ist heuristisch (Regeln/Stichwoerter), nicht KI-basiert.
Bei unklaren Formulierungen kann sie Preis/Datum/IP verfehlen oder falsch
zuordnen. Jede Telegram-Nachricht und jeder Kalendereintrag enthaelt daher
immer den Link zur Originalquelle zum Gegenchecken.
"""

import json
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

HEADERS = {
    # Ehrlich als Bot identifiziert, kein gefaelschter Browser-User-Agent.
    "User-Agent": "SecretLairTrackerBot/1.0 (+personal use, non-commercial)"
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

# Bekannte Universes-Beyond-/Crossover-IPs, nach denen im Text gesucht wird.
# Liste einfach erweitern, wenn eine neue IP angekuendigt wird.
KNOWN_IPS = [
    "Final Fantasy", "Fortnite", "Assassin's Creed", "Warhammer 40,000",
    "Warhammer 40000", "Stranger Things", "Doctor Who", "Jurassic World",
    "Jurassic Park", "Spider-Man", "Marvel", "Lord of the Rings",
    "Transformers", "Teenage Mutant Ninja Turtles", "TMNT", "Ghostbusters",
    "Godzilla", "Street Fighter", "Arcane", "Avatar: The Last Airbender",
    "Bob's Burgers", "Fallout", "The Walking Dead", "Portal",
    "Cowboy Bebop", "Neon Genesis Evangelion", "DuckTales", "Space Jam",
    "Baldur's Gate", "League of Legends", "My Little Pony", "SpongeBob",
    "Rick and Morty", "The Princess Bride", "Post Malone", "Beetlejuice",
    "Universes Beyond",
]

MONTH_WORDS = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December"
)
DATE_PATTERN = re.compile(
    rf"({MONTH_WORDS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*\d{{4}}|"
    rf"\d{{1,2}}\.\s*({MONTH_WORDS})\s*\d{{4}}|"
    rf"\d{{4}}-\d{{2}}-\d{{2}}",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)")


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp.text


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNUNG: Telegram nicht konfiguriert. Nachricht:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=payload, timeout=20)
    if r.status_code != 200:
        print(f"Telegram-Fehler ({r.status_code}): {r.text}", file=sys.stderr)


# --------------------------------------------------------------------------
# Seite in Abschnitte (Ueberschrift + zugehoeriger Text) zerlegen
# --------------------------------------------------------------------------

def extract_chunks(html: str, selector: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(selector) or soup

    headings = root.find_all(["h1", "h2", "h3", "h4"])
    chunks = []

    if not headings:
        # Fallback: keine Ueberschriftsstruktur gefunden -> ganze Seite als 1 Chunk
        text = root.get_text(" ", strip=True)
        title = (soup.title.string.strip() if soup.title and soup.title.string else "Unbenannt")
        return [{"title": title, "text": text}]

    for i, h in enumerate(headings):
        title = h.get_text(" ", strip=True)
        # Text sammeln bis zur naechsten Ueberschrift
        parts = []
        for sib in h.find_all_next():
            if sib in headings[i + 1:]:
                break
            if sib.name in ("h1", "h2", "h3", "h4"):
                continue
            t = sib.get_text(" ", strip=True)
            if t:
                parts.append(t)
        chunks.append({"title": title, "text": " ".join(parts)[:2000]})

    return chunks


def looks_like_secret_lair(chunk: dict) -> bool:
    combined = (chunk["title"] + " " + chunk["text"]).lower()
    return "secret lair" in combined or "chaos vault" in combined


# --------------------------------------------------------------------------
# Regelbasierte Extraktion (kostenlos, kein API-Call)
# --------------------------------------------------------------------------

def clean_name(title: str) -> str:
    title = re.sub(r"^(secret lair( drop)?:?\s*)", "", title, flags=re.IGNORECASE).strip()
    return title or "Unbenannter Drop"


def find_ip(text: str) -> str:
    for ip in KNOWN_IPS:
        if ip.lower() in text.lower():
            return ip
    return "Magic: The Gathering"


def find_price(text: str):
    m = PRICE_PATTERN.search(text)
    return float(m.group(1)) if m else None


def find_date(text: str):
    m = DATE_PATTERN.search(text)
    if not m:
        return None, None
    raw = m.group(0)
    try:
        dt = dateparser.parse(raw, fuzzy=True, default=None)
        if dt:
            return dt.strftime("%Y-%m-%d"), raw
    except (ValueError, OverflowError):
        pass
    return None, raw


def extract_with_rules(chunks: list) -> list:
    results = []
    for chunk in chunks:
        if not looks_like_secret_lair(chunk):
            continue
        text = chunk["text"]
        release_date, release_date_text = find_date(text)
        results.append({
            "name": clean_name(chunk["title"]),
            "ip": find_ip(chunk["title"] + " " + text),
            "cards": None,  # ohne KI nicht zuverlaessig extrahierbar -> manuell pruefen
            "price_usd": find_price(text),
            "release_date": release_date,
            "release_date_text": release_date_text,
            "summary": text[:200] + ("..." if len(text) > 200 else ""),
        })
    return results


# --------------------------------------------------------------------------
# Events zusammenfuehren
# --------------------------------------------------------------------------

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def merge_events(events: dict, extracted: list, source_name: str, source_url: str) -> list:
    changed = []
    for item in extracted:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        key = slugify(name)
        existing = events.get(key)

        record = {
            "name": name,
            "ip": item.get("ip") or "Magic: The Gathering",
            "cards": item.get("cards"),
            "price_usd": item.get("price_usd"),
            "release_date": item.get("release_date"),
            "release_date_text": item.get("release_date_text"),
            "summary": item.get("summary"),
            "source_name": source_name,
            "source_url": source_url,
            "first_seen": existing["first_seen"] if existing else int(time.time()),
            "last_updated": int(time.time()),
        }

        relevant_fields = ("price_usd", "release_date", "release_date_text")
        is_new = existing is None
        is_updated = existing is not None and any(
            existing.get(f) != record.get(f) for f in relevant_fields
        )

        events[key] = record
        if is_new or is_updated:
            changed.append((record, is_new))

    return changed


# --------------------------------------------------------------------------
# iCalendar-Feed erzeugen
# --------------------------------------------------------------------------

def escape_ics(text) -> str:
    if not text:
        return ""
    text = str(text)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def build_ics(events: dict) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Secret Lair Tracker//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Secret Lair Drops",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for key, ev in sorted(events.items(), key=lambda kv: kv[1].get("release_date") or "9999"):
        date = ev.get("release_date")
        if not date:
            continue
        dt = date.replace("-", "")

        price = f"${ev['price_usd']}" if ev.get("price_usd") else "Preis unbekannt (Link pruefen)"
        cards = ev.get("cards") or "Karten nicht automatisch erkannt - bitte Quelle pruefen"
        desc = f"IP: {ev.get('ip')}\\nPreis: {price}\\nKarten: {cards}\\nQuelle: {ev.get('source_url')}"

        uid = f"{key}@secret-lair-tracker"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
            f"DTSTART;VALUE=DATE:{dt}",
            f"SUMMARY:{escape_ics('Secret Lair: ' + ev['name'])}",
            f"DESCRIPTION:{desc}",
            f"URL:{ev.get('source_url', '')}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------------------
# Hauptlogik
# --------------------------------------------------------------------------

def main() -> None:
    config = load_json(CONFIG_PATH, {})
    state_path = BASE_DIR / config.get("state_file", "state.json")
    events_path = BASE_DIR / config.get("events_file", "secret_lairs.json")
    ics_path = BASE_DIR / config.get("ics_output", "docs/calendar.ics")

    state = load_json(state_path, {})
    events = load_json(events_path, {})

    any_new = False

    for watch in config.get("watches", []):
        name, url, selector = watch["name"], watch["url"], watch.get("selector", "main")
        try:
            html = fetch(url)
        except Exception as e:
            print(f"[FEHLER] {name}: Seite nicht erreichbar ({e})", file=sys.stderr)
            continue

        chunks = extract_chunks(html, selector)
        full_text = " ".join(c["title"] + " " + c["text"] for c in chunks)
        current_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        prev_hash = state.get(name, {}).get("hash")
        is_first_run = prev_hash is None
        state.setdefault(name, {})["hash"] = current_hash
        state[name]["checked_at"] = int(time.time())

        if current_hash == prev_hash:
            print(f"[OK] {name}: keine Aenderung")
            continue

        print(f"[{'INIT' if is_first_run else 'CHANGE'}] {name}: werte Abschnitte aus ...")
        extracted = extract_with_rules(chunks)
        changed = merge_events(events, extracted, name, url)

        for record, is_new in changed:
            any_new = True
            icon = "NEU" if is_new else "UPDATE"
            date_str = record.get("release_date") or record.get("release_date_text") or "Datum noch unbekannt"
            price_str = f"${record['price_usd']}" if record.get("price_usd") else "Preis unbekannt"
            msg = (
                f"[{icon}] <b>{record['name']}</b>\n"
                f"IP (automatisch erkannt, ggf. pruefen): {record.get('ip')}\n"
                f"Release: {date_str}\n"
                f"Preis: {price_str}\n"
                f"Quelle: {record.get('source_name')}\n"
                f"{record.get('source_url')}"
            )
            send_telegram(msg)

    save_json(state_path, state)
    save_json(events_path, events)

    ics_content = build_ics(events)
    ics_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics_content)

    print("Fertig. " + ("Neue/aktualisierte Eintraege gefunden." if any_new else "Keine Aenderungen."))


if __name__ == "__main__":
    main()
