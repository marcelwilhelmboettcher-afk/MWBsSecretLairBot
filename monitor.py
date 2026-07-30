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
import difflib
import hashlib
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
 
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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
 
GEMINI_PROMPT_TEMPLATE = """Du bekommst Textabschnitte von einer Magic: The Gathering Secret-Lair-Ankuendigungsseite.
Extrahiere ALLE einzelnen Secret-Lair-Drops, die in diesen Abschnitten beschrieben werden, als JSON-Array.
 
Jedes Element im Array muss folgende Felder haben:
- "name": Name des Drops, ohne das Praefix "Secret Lair" oder "Secret Lair Drop"
- "ip": Die zugehoerige Crossover-IP/Franchise (z.B. "Marvel", "Lord of the Rings", "Final Fantasy").
  WICHTIG: Nur die IP eintragen, wenn der Drop wirklich zu dieser Franchise gehoert - nicht bei
  zufaelligen Wortaehnlichkeiten (Beispiel: "A Marvelous Mathoms Superdrop" ist KEIN Marvel-Drop,
  sondern ein Hobbit/Der-Herr-der-Ringe-Drop; "Marvelous" ist hier nur ein normales Adjektiv).
  Wenn keine erkennbare Crossover-IP vorliegt, nutze "Magic: The Gathering".
- "cards": Kurze Liste/Beschreibung der enthaltenen Karten oder Motive, falls im Text erwaehnt,
  sonst null.
- "price_usd": Preis in US-Dollar als Zahl (ohne Dollarzeichen), sonst null.
- "release_date": Erscheinungsdatum im Format YYYY-MM-DD, falls ein konkretes Datum genannt wird,
  sonst null.
- "release_date_text": Das Datum/die Zeitangabe genau so, wie sie im Originaltext steht (z.B.
  "September 2026" oder "10.08."), sonst null.
- "release_time_text": Falls im Text eine KONKRETE Uhrzeit fuer den Verkaufsstart genannt wird
  (z.B. "9 a.m. PT", "Noon EDT", "9:00 AM Pacific Time"), gib sie moeglichst genau wieder,
  inklusive Zeitzonen-Kuerzel. Sonst null. Erfinde NIEMALS eine Uhrzeit, die nicht im Text steht.
- "summary": Ein bis zwei Saetze Zusammenfassung auf Deutsch, max. 200 Zeichen.
 
Gib AUSSCHLIESSLICH das JSON-Array zurueck, keinen weiteren Text, keine Markdown-Codebloecke.
Wenn kein echter Secret-Lair-Drop im Text zu finden ist, gib ein leeres Array [] zurueck.
 
TEXTABSCHNITTE:
{chunks_text}
"""
 
 
def call_gemini(prompt: str, max_retries: int = 2):
    if not GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=45)
            if r.status_code in (429, 503) and attempt < max_retries:
                wait_s = 5.0 * (attempt + 1)
                print(f"[GEMINI] Temporaer nicht verfuegbar ({r.status_code}), warte {wait_s:.0f}s (Versuch {attempt + 1}/{max_retries + 1}) ...", file=sys.stderr)
                time.sleep(wait_s)
                continue
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            if attempt < max_retries:
                wait_s = 5.0 * (attempt + 1)
                print(f"[GEMINI] Fehler ({e}), warte {wait_s:.0f}s (Versuch {attempt + 1}/{max_retries + 1}) ...", file=sys.stderr)
                time.sleep(wait_s)
                continue
            print(f"[GEMINI-FEHLER] {e}", file=sys.stderr)
            return None
    return None


 
def extract_with_ai(chunks: list, source_name: str) -> list:
    relevant = [c for c in chunks if looks_like_secret_lair(c)]
    if not relevant:
        return []
 
    chunks_text = "\n\n---\n\n".join(
        f"UEBERSCHRIFT: {c['title']}\nTEXT: {c['text'][:1500]}" for c in relevant
    )
    prompt = GEMINI_PROMPT_TEMPLATE.format(chunks_text=chunks_text[:12000])
 
    raw = call_gemini(prompt)
    if raw is None:
        print(f"[{source_name}] Gemini nicht verfuegbar - falle auf Regel-Erkennung zurueck.")
        return extract_with_rules(chunks)
 
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
 
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("Antwort ist kein JSON-Array")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[{source_name}] Gemini-Antwort nicht parsebar ({e}) - falle auf Regel-Erkennung zurueck.")
        return extract_with_rules(chunks)
 
    results = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        price = item.get("price_usd")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        results.append({
            "name": str(item.get("name")).strip(),
            "ip": item.get("ip") or "Magic: The Gathering",
            "cards": item.get("cards"),
            "price_usd": price,
            "release_date": item.get("release_date"),
            "release_date_text": item.get("release_date_text"),
            "release_time_text": item.get("release_time_text"),
            "summary": item.get("summary"),
        })
    return results
 
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
 
 
def extract_chunks_from_rss(xml_text: str) -> list:
    chunks = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[RSS-FEHLER] Konnte Feed nicht parsen: {e}", file=sys.stderr)
        return chunks
 
    for item in root.findall(".//item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        link_el = item.find("link")
        title = (title_el.text or "").strip() if title_el is not None else ""
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        text = f"{desc} {link}".strip()
        chunks.append({"title": title, "text": text})
 
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
    text_lower = text.lower()
    for ip in KNOWN_IPS:
        # \b sorgt fuer Wortgrenzen-Matching, damit z.B. "Marvelous" nicht
        # faelschlich als Treffer fuer "Marvel" zaehlt.
        pattern = r"\b" + re.escape(ip.lower()) + r"\b"
        if re.search(pattern, text_lower):
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
 
 
# Wizards' Secret-Lair-Store-Drops starten so gut wie immer um 9 a.m. PT,
# auch wenn ein Ankuendigungsartikel keine explizite Uhrzeit nennt. Wird
# als Fallback-Annahme genutzt (siehe merge_events) - transparent im
# Kalendereintrag als "angenommen" gekennzeichnet, nicht als Fakt verkauft.
DEFAULT_RELEASE_TIME_TEXT = "9:00 AM PT"

US_TIMEZONE_ALIASES = {
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
}


def parse_release_datetime_utc(release_date: str, time_text: str):
    """Versucht, aus einem Datum (YYYY-MM-DD) und einer Freitext-Uhrzeit wie
    '9 a.m. PT' oder 'Noon EDT' einen exakten UTC-Zeitpunkt zu berechnen
    (inkl. korrekter Sommerzeit-Behandlung). Gibt None zurueck, wenn Datum
    oder Uhrzeit fehlen oder nicht eindeutig erkennbar sind - dann bleibt der
    Kalendereintrag ein ganztaegiger Termin ohne Minuten-genaue Erinnerung."""
    if not release_date or not time_text:
        return None

    tz_match = re.search(r"\b(PT|PST|PDT|ET|EST|EDT|CT|CST|CDT|MT|MST|MDT)\b", time_text, re.IGNORECASE)
    if not tz_match:
        return None
    tz_name = US_TIMEZONE_ALIASES.get(tz_match.group(1).upper())
    if not tz_name:
        return None

    text_lower = time_text.lower()
    if "noon" in text_lower:
        hour, minute = 12, 0
    else:
        time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?", time_text, re.IGNORECASE)
        if not time_match:
            return None
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        if time_match.group(3).lower() == "p" and hour != 12:
            hour += 12
        if time_match.group(3).lower() == "a" and hour == 12:
            hour = 0

    try:
        y, m, d = (int(x) for x in release_date.split("-"))
        local_dt = datetime(y, m, d, hour, minute, tzinfo=ZoneInfo(tz_name))
        return local_dt.astimezone(timezone.utc)
    except Exception as e:
        print(f"[ZEIT-FEHLER] Konnte Release-Zeit nicht umrechnen: {e}", file=sys.stderr)
        return None


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
 
def fetch_breakdown_links(html: str) -> list:
    """Findet MTGStocks-'Value Breakdown'-Artikel auf der News-Seite."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if "value breakdown" in text.lower() and "secret lair" in text.lower():
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.mtgstocks.com" + href
            if href in seen_urls:
                continue
            seen_urls.add(href)
            results.append({"title": text, "url": href})
    return results


def match_event_key(events: dict, breakdown_title: str):
    """Ordnet einen MTGStocks-Breakdown-Titel einem bereits bekannten Secret-
    Lair-Event zu (per Substring- bzw. Aehnlichkeits-Abgleich der Namen)."""
    best_key, best_score = None, 0.0
    norm_title = breakdown_title.lower()
    for key, ev in events.items():
        name = ev.get("name", "")
        norm_name = re.sub(r"[™®]", "", name).lower().strip()
        if not norm_name:
            continue
        if norm_name in norm_title:
            return key
        score = difflib.SequenceMatcher(None, norm_name, norm_title).ratio()
        if score > best_score:
            best_score, best_key = score, key
    if best_score >= 0.55:
        return best_key
    return None


def enrich_with_mtgstocks(events: dict) -> bool:
    """Prueft MTGStocks auf neue Value-Breakdown-Artikel und ergaenzt
    passende, bereits bekannte Events um den Link. Gibt True zurueck, falls
    mindestens ein Event neu ergaenzt wurde."""
    any_updated = False
    try:
        html = fetch("https://www.mtgstocks.com/news")
    except Exception as e:
        print(f"[MTGSTOCKS-FEHLER] Seite nicht erreichbar: {e}", file=sys.stderr)
        return any_updated

    breakdowns = fetch_breakdown_links(html)
    for b in breakdowns:
        key = match_event_key(events, b["title"])
        if key and not events[key].get("mtgstocks_url"):
            events[key]["mtgstocks_url"] = b["url"]
            events[key]["last_updated"] = int(time.time())
            any_updated = True
            print(f"[MTGSTOCKS] Breakdown gefunden fuer: {events[key]['name']}")
            msg = (
                f"[UPDATE] <b>{events[key]['name']}</b>\n"
                f"MTGStocks Value Breakdown ist da:\n{b['url']}"
            )
            send_telegram(msg)

    return any_updated


def match_event_by_ip(events: dict, title: str) -> list:
    """Ordnet einen Mana-Value-Drop-Titel (z.B. 'Stardew Valley: Welcome to
    Stardew Valley') per IP-Namen bekannten Events zu. Ein Event kann zu
    mehreren Mana-Value-Eintraegen passen (z.B. bei Superdrops mit mehreren
    Teil-Drops), daher wird eine Liste aller Treffer zurueckgegeben."""
    norm_title = title.lower()
    matches = []
    for key, ev in events.items():
        ip = (ev.get("ip") or "").strip()
        if not ip or ip.lower() == "magic: the gathering":
            continue
        if ip.lower() in norm_title:
            matches.append(key)
    return matches


def extract_manavalue_cards(html: str) -> list:
    """Liest die Kartennamen aus dem Abschnitt 'Cards in this drop' einer
    Mana-Value-Detailseite aus."""
    soup = BeautifulSoup(html, "html.parser")
    heading = None
    for tag in soup.find_all(["h2", "h3"]):
        if "cards in this drop" in tag.get_text(" ", strip=True).lower():
            heading = tag
            break
    if not heading:
        return []

    cards = []
    seen = set()
    node = heading.find_next()
    while node is not None and getattr(node, "name", None) not in ("h1", "h2", "h3"):
        if getattr(node, "name", None) == "a":
            raw = node.get_text(" ", strip=True)
            raw = re.sub(r"\$\d+(?:\.\d+)?", "", raw).strip()
            # Falls Name+Preis-Text den Kartennamen doppelt enthaelt (z.B. durch
            # Bild-Alt-Text): "Wedding Ring Wedding Ring" -> "Wedding Ring"
            half = len(raw) // 2
            if len(raw) % 2 == 0 and raw[:half].strip() and raw[:half].strip() == raw[half:].strip():
                raw = raw[:half].strip()
            if raw and raw not in seen:
                seen.add(raw)
                cards.append(raw)
        node = node.find_next()
    return cards


def _scryfall_search(query: str, extra_params: dict = None, max_retries: int = 3):
    """Fuehrt eine Scryfall-Suche aus, inkl. Retry bei 429/Verbindungsfehlern.
    Gibt die geparste JSON-Antwort zurueck oder None bei 404/endgueltigem
    Fehler."""
    params = {"q": query, "unique": "prints"}
    if extra_params:
        params.update(extra_params)

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                "https://api.scryfall.com/cards/search",
                params=params,
                headers=HEADERS,
                timeout=15,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait_s = float(resp.headers.get("Retry-After", 1.5))
                print(f"[SCRYFALL] Rate-Limit ('{query}'), warte {wait_s:.1f}s (Versuch {attempt + 1}/{max_retries + 1}) ...", file=sys.stderr)
                time.sleep(wait_s)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait_s = 1.0 * (attempt + 1)
                print(f"[SCRYFALL] Verbindungsfehler ('{query}': {e}), warte {wait_s:.1f}s (Versuch {attempt + 1}/{max_retries + 1}) ...", file=sys.stderr)
                time.sleep(wait_s)
                continue
            print(f"[SCRYFALL-FEHLER] {query}: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[SCRYFALL-FEHLER] {query}: {e}", file=sys.stderr)
            return None

    print(f"[SCRYFALL-FEHLER] {query}: nach {max_retries} Wiederholungen weiterhin nicht erreichbar", file=sys.stderr)
    return None


def _extract_eur_prices(card_obj: dict):
    prices = card_obj.get("prices") or {}
    eur_str = prices.get("eur")
    eur_foil_str = prices.get("eur_foil")
    try:
        eur_price = float(eur_str) if eur_str else None
    except (TypeError, ValueError):
        eur_price = None
    try:
        eur_foil_price = float(eur_foil_str) if eur_foil_str else None
    except (TypeError, ValueError):
        eur_foil_price = None
    return eur_price, eur_foil_price


def fetch_scryfall_card_info(card_name: str, max_retries: int = 3) -> dict:
    """Fragt die kostenlose Scryfall-API pro Karte ab und liefert sowohl den
    Secret-Lair-Sondernamen (z.B. 'Wedding Ring' -> 'Mermaid's Pendant',
    falls vorhanden) als auch den aktuellen Cardmarket-EUR-Preis (Scryfall
    bezieht EUR-Preise offiziell von Cardmarket).

    Falls die konkrete Secret-Lair-Auflage noch keinen Preis hat (z.B. weil
    der Drop noch nicht erschienen ist), wird ersatzweise die guenstigste
    verfuegbare Auflage derselben Karte ueber alle Sets hinweg herangezogen -
    das Ergebnis wird dann als Schaetzwert (eur_is_estimate=True) markiert.

    Gibt ein leeres dict zurueck, wenn die Karte gar nicht gefunden wird."""
    data = _scryfall_search(f'!"{card_name}" set:sld', max_retries=max_retries)
    if not data:
        return {}

    candidates = data.get("data", [])
    if not candidates:
        return {}
    candidates.sort(key=lambda c: c.get("released_at") or "", reverse=True)
    newest = candidates[0]

    flavor_candidates = [c for c in candidates if c.get("flavor_name")]
    flavor_name = flavor_candidates[0]["flavor_name"] if flavor_candidates else None

    eur_price, eur_foil_price = _extract_eur_prices(newest)
    eur_is_estimate = False

    if eur_price is None and eur_foil_price is None:
        fallback_data = _scryfall_search(
            f'!"{card_name}"',
            extra_params={"order": "eur", "dir": "asc"},
            max_retries=max_retries,
        )
        if fallback_data:
            priced = [c for c in fallback_data.get("data", []) if (c.get("prices") or {}).get("eur")]
            if priced:
                cheapest = priced[0]
                eur_price, eur_foil_price = _extract_eur_prices(cheapest)
                eur_is_estimate = True

    return {
        "flavor_name": flavor_name,
        "eur_price": eur_price,
        "eur_foil_price": eur_foil_price,
        "eur_is_estimate": eur_is_estimate,
    }


def format_card_name(real_name: str, info: dict) -> str:
    """Ergaenzt einen Kartennamen um den Secret-Lair-Sondernamen (falls
    vorhanden), z.B. 'Wedding Ring (\"Mermaid's Pendant\" im Secret Lair)'."""
    flavor_name = info.get("flavor_name")
    if flavor_name and flavor_name != real_name:
        return f'{real_name} ("{flavor_name}" im Secret Lair)'
    return real_name


def enrich_with_manavalue(events: dict) -> bool:
    """Prueft den Mana-Value-RSS-Feed (europaeische Cardmarket-Preise) auf
    neue Drops und ergaenzt passende, bereits bekannte Events um Link(s) und
    Kartenliste(n). Ein Event kann mehrere Mana-Value-Eintraege bekommen,
    wenn es (wie ein Superdrop) aus mehreren Teil-Drops besteht."""
    any_updated = False
    try:
        feed_xml = fetch("https://www.manavalue.org/feed.xml")
    except Exception as e:
        print(f"[MANAVALUE-FEHLER] Feed nicht erreichbar: {e}", file=sys.stderr)
        return any_updated

    items = extract_chunks_from_rss(feed_xml)

    for item in items:
        title = item["title"]
        if not title:
            continue
        matched_keys = match_event_by_ip(events, title)
        if not matched_keys:
            continue

        slug = slugify(title)
        detail_url = f"https://www.manavalue.org/secret-lair/{slug}"

        for key in matched_keys:
            existing_entries = events[key].setdefault("manavalue_drops", [])
            if any(e.get("url") == detail_url for e in existing_entries):
                continue  # schon erfasst

            try:
                detail_html = fetch(detail_url)
            except Exception as e:
                print(f"[MANAVALUE-FEHLER] {detail_url}: {e}", file=sys.stderr)
                continue

            cards_raw = extract_manavalue_cards(detail_html)
            cards_display = []
            cardmarket_total_nonfoil = 0.0
            cardmarket_total_foil = 0.0
            nonfoil_known = False
            foil_known = False
            any_estimate = False
            for c in cards_raw:
                info = fetch_scryfall_card_info(c)
                time.sleep(0.15)  # etwas Sicherheitsabstand unter Scryfalls ca. 10 Anfragen/Sekunde
                cards_display.append(format_card_name(c, info))
                if info.get("eur_price") is not None:
                    cardmarket_total_nonfoil += info["eur_price"]
                    nonfoil_known = True
                if info.get("eur_foil_price") is not None:
                    cardmarket_total_foil += info["eur_foil_price"]
                    foil_known = True
                if info.get("eur_is_estimate"):
                    any_estimate = True

            existing_entries.append({
                "title": title,
                "url": detail_url,
                "cards": cards_display,
                "cardmarket_eur_total": round(cardmarket_total_nonfoil, 2) if nonfoil_known else None,
                "cardmarket_eur_total_foil": round(cardmarket_total_foil, 2) if foil_known else None,
                "cardmarket_has_estimate": any_estimate,
            })
            events[key]["last_updated"] = int(time.time())
            any_updated = True
            print(f"[MANAVALUE] Gefunden fuer {events[key]['name']}: {title} ({len(cards_display)} Karten)")

            msg = (
                f"[UPDATE] <b>{events[key]['name']}</b>\n"
                f"Mana Value (EUR/Cardmarket) verfuegbar fuer '{title}':\n{detail_url}"
            )
            send_telegram(msg)

    return any_updated


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

        # Mit einer Kopie des bestehenden Eintrags starten (statt komplett
        # neu), damit Zusatzfelder wie mtgstocks_url/manavalue_drops bei
        # einer erneuten Aenderung der Quellseite nicht verloren gehen.
        record = dict(existing) if existing else {}
        record.update({
            "name": name,
            "ip": item.get("ip") or "Magic: The Gathering",
            "cards": item.get("cards"),
            "price_usd": item.get("price_usd"),
            "release_date": item.get("release_date"),
            "release_date_text": item.get("release_date_text"),
            "release_time_text": item.get("release_time_text"),
            "summary": item.get("summary"),
            "source_name": source_name,
            "source_url": source_url,
            "first_seen": existing["first_seen"] if existing else int(time.time()),
            "last_updated": int(time.time()),
        })

        release_time_text = record.get("release_time_text")
        release_time_is_assumed = False
        if not release_time_text and record.get("release_date"):
            # Wizards' Secret-Lair-Store-Drops starten so gut wie immer um
            # 9 a.m. PT, auch wenn der Ankuendigungstext keine Uhrzeit nennt.
            # Wird keine explizite Uhrzeit gefunden, nehmen wir das als
            # Annahme an - transparent gekennzeichnet, nicht als Fakt.
            release_time_text = DEFAULT_RELEASE_TIME_TEXT
            release_time_is_assumed = True
        record["release_time_text"] = release_time_text
        record["release_time_is_assumed"] = release_time_is_assumed

        release_dt_utc = parse_release_datetime_utc(
            record.get("release_date"), release_time_text
        )
        record["release_datetime_utc"] = release_dt_utc.isoformat() if release_dt_utc else None

        relevant_fields = ("price_usd", "release_date", "release_date_text", "release_time_text")
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

        # Falls eine exakte Uhrzeit bekannt ist (von Gemini erkannt und nach
        # UTC umgerechnet), wird ein zeitgenauer Termin mit Erinnerungen
        # erzeugt. Ohne Uhrzeit bleibt es ein ganztaegiger Termin ohne
        # minutengenaue Erinnerung (bei einem reinen Datum waere "30 Minuten
        # vorher" nicht sinnvoll definierbar).
        release_dt_utc_str = ev.get("release_datetime_utc")
        has_precise_time = False
        if release_dt_utc_str:
            try:
                dt_utc = datetime.fromisoformat(release_dt_utc_str)
                dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
                dtend_utc = dt_utc + timedelta(hours=1)
                dtend_line = f"DTEND:{dtend_utc.strftime('%Y%m%dT%H%M%SZ')}"
                has_precise_time = True
            except (ValueError, TypeError):
                has_precise_time = False
        if not has_precise_time:
            dt = date.replace("-", "")
            dtstart_line = f"DTSTART;VALUE=DATE:{dt}"
            dtend_line = None

        price = f"${ev['price_usd']}" if ev.get("price_usd") else "Preis unbekannt (Link pruefen)"

        manavalue_drops = ev.get("manavalue_drops") or []
        manavalue_cards = []
        for md in manavalue_drops:
            manavalue_cards.extend(md.get("cards") or [])

        if manavalue_cards:
            cards = ", ".join(manavalue_cards)
        else:
            cards = ev.get("cards") or "Karten nicht automatisch erkannt - bitte Quelle pruefen"

        desc = f"IP: {ev.get('ip')}\\nPreis: {price}\\nKarten: {cards}\\nQuelle: {ev.get('source_url')}"
        if has_precise_time and ev.get("release_time_is_assumed"):
            desc += "\\nHinweis: Uhrzeit ist eine Annahme (Standard 9 a.m. PT), nicht offiziell im Artikel bestaetigt"
        if ev.get("mtgstocks_url"):
            desc += f"\\nMTGStocks Value Breakdown: {ev['mtgstocks_url']}"

        cardmarket_nonfoil_totals = [md.get("cardmarket_eur_total") for md in manavalue_drops if md.get("cardmarket_eur_total") is not None]
        cardmarket_foil_totals = [md.get("cardmarket_eur_total_foil") for md in manavalue_drops if md.get("cardmarket_eur_total_foil") is not None]
        any_estimate = any(md.get("cardmarket_has_estimate") for md in manavalue_drops)
        if cardmarket_nonfoil_totals or cardmarket_foil_totals:
            parts = []
            if cardmarket_nonfoil_totals:
                parts.append(f"{sum(cardmarket_nonfoil_totals):.2f} EUR Non-Foil")
            if cardmarket_foil_totals:
                parts.append(f"{sum(cardmarket_foil_totals):.2f} EUR Foil")
            label = "Cardmarket-Wert (Singles gesamt, ca.)"
            if any_estimate:
                label += " - teils geschaetzt aus anderer Auflage, da SL-Print noch ungelistet"
            desc += f"\\n{label}: {' / '.join(parts)}"

        for md in manavalue_drops:
            desc += f"\\nMana Value ({escape_ics(md['title'])}): {md['url']}"
 
        uid = f"{key}@secret-lair-tracker"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
            dtstart_line,
        ]
        if dtend_line:
            lines.append(dtend_line)
        lines += [
            f"SUMMARY:{escape_ics('Secret Lair: ' + ev['name'])}",
            f"DESCRIPTION:{desc}",
            f"URL:{ev.get('source_url', '')}",
        ]
        if has_precise_time:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Secret Lair Drop startet in 1 Stunde",
                "TRIGGER:-PT60M",
                "END:VALARM",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Secret Lair Drop startet in 15 Minuten",
                "TRIGGER:-PT15M",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
 
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
        source_kind = watch.get("type", "html")
        try:
            raw = fetch(url)
        except Exception as e:
            print(f"[FEHLER] {name}: Seite nicht erreichbar ({e})", file=sys.stderr)
            continue
 
        if source_kind == "rss":
            chunks = extract_chunks_from_rss(raw)
        else:
            chunks = extract_chunks(raw, selector)
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
        time.sleep(4)  # Sicherheitsabstand, damit mehrere Quellen hintereinander nicht Geminis Minutenlimit reissen
        extracted = extract_with_ai(chunks, name)
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

    if enrich_with_mtgstocks(events):
        any_new = True

    if enrich_with_manavalue(events):
        any_new = True

    save_json(state_path, state)
    save_json(events_path, events)
 
    ics_content = build_ics(events)
    ics_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics_content)
 
    print("Fertig. " + ("Neue/aktualisierte Eintraege gefunden." if any_new else "Keine Aenderungen."))
 
 
if __name__ == "__main__":
    main()
