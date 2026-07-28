# Secret Lair Tracker (100% kostenlos)

Überwacht mehrere offizielle Magic-Secret-Lair-Quellen (inkl. Chaos Vault) und
eine Community-Trackerseite, erkennt per Regeln/Stichwortliste automatisch
Name, IP, Preis und Release-Datum jedes Drops, schickt dir eine Telegram-
Nachricht bei Neuigkeiten und pflegt einen **iPhone-Kalender-Feed**, der sich
automatisch aktualisiert.

**Kosten: 0 €.** Es wird keine kostenpflichtige API verwendet – nur:
- GitHub Actions (kostenloser Free-Tier, öffentliche Repos haben praktisch
  unbegrenzte Minuten für Standard-Runner)
- Telegram Bot API (kostenlos)
- Python-Bibliotheken `requests`, `beautifulsoup4`, `python-dateutil` (alle
  Open Source, kostenlos)

## Wichtiger Unterschied zur KI-Variante

Statt Claude die Texte auswerten zu lassen, sucht das Skript mit **Regeln**:
- Preis: Regex nach `$XX.XX`
- Datum: Regex nach Monatsnamen/Datumsformaten, geparst mit `python-dateutil`
- IP/Franchise: Abgleich mit einer Liste bekannter Crossover-IPs in `monitor.py`
  (`KNOWN_IPS` – einfach erweiterbar, wenn Wizards eine neue IP ankündigt)
- Enthaltene Karten: **wird nicht automatisch erkannt** (das braucht
  Sprachverständnis, das eine Regel nicht leisten kann, ohne kostenpflichtige
  API). Dafür bekommst du immer den Link zur Quelle in Telegram-Nachricht und
  Kalender-Beschreibung, um schnell selbst nachzuschauen.

Das heißt: Du bekommst zuverlässig **wann + ungefähr was + Link**, aber nicht
die genaue Kartenliste automatisch. Das ist der Kompromiss für "kostenlos".

## Quellen

Alle vier konfigurierten Quellen sind entweder offizielle Wizards-Seiten
(secretlair.wizards.com, magic.wizards.com) oder ein öffentlich zugänglicher
Community-Artikel-Feed (mtgcardlibrary.com). Games-Island wurde bewusst
**nicht** aufgenommen: Die Seite untersagt automatisiertes Crawling explizit
und markiert das gezielt gegen Scalper-Bots. Für Games-Island nutzt du die
native Discord-Kanal-Benachrichtigung (siehe vorherige Nachricht: Server
stummschalten, den MTG-Kanal einzeln auf "Alle Nachrichten" stellen).

---

## 1. Telegram-Bot einrichten

1. In Telegram **@BotFather** anschreiben, `/newbot` ausführen.
2. Token kopieren → das ist `TELEGRAM_TOKEN`.
3. Dem eigenen Bot eine Nachricht schreiben, dann
   `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen und die
   `chat.id` kopieren → das ist `TELEGRAM_CHAT_ID`.

## 2. GitHub-Repository einrichten

1. Erstelle ein **öffentliches** Repository (z. B. `secret-lair-tracker`) –
   öffentlich, damit der Kalender-Feed ohne Login abrufbar ist und GitHub
   Pages/Actions im kostenlosen Umfang bleiben. Es landen keine Zugangsdaten
   im Code, nur Secret-Lair-Termine.
2. Lade den kompletten Ordnerinhalt hoch:
   ```
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<dein-user>/secret-lair-tracker.git
   git push -u origin main
   ```

## 3. Secrets hinterlegen

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Wert |
|---|---|
| `TELEGRAM_TOKEN` | Bot-Token aus Schritt 1 |
| `TELEGRAM_CHAT_ID` | Chat-ID aus Schritt 1 |

## 4. GitHub Pages aktivieren (für den Kalender-Link)

1. **Settings → Pages**
2. Bei "Source" **"Deploy from a branch"** wählen, Branch `main`, Ordner `/docs`.
3. Speichern. Nach dem ersten erfolgreichen Workflow-Lauf ist der Kalender
   erreichbar unter:
   ```
   https://<dein-user>.github.io/secret-lair-tracker/calendar.ics
   ```

## 5. Ersten Lauf auslösen

**Actions → Secret Lair Tracker → Run workflow**. Danach in den Logs prüfen,
ob alle vier Quellen erfolgreich geladen wurden. Der allererste Lauf pro
Quelle speichert nur den Ausgangszustand (kein Alarm) – erst ab der nächsten
inhaltlichen Änderung gibt's eine Meldung.

## 6. Kalender aufs iPhone

1. **Einstellungen → Kalender → Accounts → Account hinzufügen → Andere**
2. **Kalenderabo hinzufügen**
3. URL aus Schritt 4 eintragen.
4. Fertig – neue Secret Lairs erscheinen automatisch als ganztägige Termine,
   sobald ein Release-Datum im Text gefunden wurde.

**Hinweis:** Ein Termin erscheint erst, wenn ein Datum im Format wie
"September 15, 2026" oder "2026-09-15" im Text steht und erkannt wurde. Reine
Monatsangaben ("im September") landen vorerst nur im Telegram-Alert und in
`secret_lairs.json` (Feld `release_date_text`), bis ein festes Datum
veröffentlicht wird.

---

## Weitere Quellen oder IPs ergänzen

**Neue Quelle:** In `config.json` einen Eintrag zu `"watches"` hinzufügen.

**Neue IP (z. B. neue Crossover-Ankündigung):** In `monitor.py` die Liste
`KNOWN_IPS` erweitern, damit sie korrekt erkannt wird statt als
"Magic: The Gathering" (Standardwert) durchzurutschen.

## Troubleshooting

- **Keine Telegram-Nachricht, Workflow läuft grün:** Wahrscheinlich hat sich
  seit dem letzten Lauf inhaltlich nichts geändert – normal.
- **Preis/Datum falsch oder fehlt:** Die Regel-Erkennung ist nicht perfekt,
  besonders bei ungewöhnlichen Formulierungen. Immer den mitgelieferten Link
  zur Quelle prüfen.
- **Kalender aktualisiert sich auf dem iPhone nicht sofort:** iOS ruft
  abonnierte Kalender nur in Intervallen ab (oft alle paar Stunden) – das ist
  eine iOS-Einschränkung, kein Fehler im Tracker.
