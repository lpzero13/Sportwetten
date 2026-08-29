# Tipico Live Observer V0.2 – Implementierungsstatus

Stand: 29.08.2026  
Scope: unabhängige historische Tipico-Datensammlung, read-only

## Status

TIPICO_V02_STATUS = PARTIAL

Das V0.2-Datenmodell und der Background Collector sind implementiert. Der
Status ist noch nicht `PASS`, weil die Definition of Done einen vollständig
gesammelten Spieltag mit ausreichender Pre-Match-, Halbzeit-, HZ2- und
Finalabdeckung verlangt. Der durchgeführte Smoke-Test war bewusst kurz.

## Verifizierter Pre-Match-Zugriff

Die kleine, auf den benötigten Pfad begrenzte Discovery hat den aktiven
Upcoming-REST-Endpunkt bestätigt:

~~~text
GET /v1/tpapi/programgateway/program/events/hourEvents/{1|2|3|6|today|24|48}
~~~

Mit den Tipico-Parametern für Sprache, Lizenzregion und Fußball wurden 367
Upcoming-Fußballspiele geliefert. Ein repräsentatives Eventdetail vor Anpfiff
antwortete mit HTTP 200 und 30 Märkten / 77 Outcomes; ein größeres Beispiel
lieferte 70 Märkte / 267 Outcomes.

## Implementiert

- `competitions` mit Competition-ID, Name, Region sowie First-/Last-Seen und
  Eventanzahl.
- `snapshots` mit Typ, Trigger, Spielstand, HT-Spielstand, Markt-/Outcome-
  Zählung, Qualitätsstatus und Raw-Pfad.
- `market_presence` zur Unterscheidung „nicht angeboten“ und „angeboten, aber
  pausiert“.
- `odds_history.snapshot_id` zur Zuordnung der Quoten zu einem Snapshot.
- unabhängiger Collector-Prozess ohne Streamlit-Abhängigkeit.
- maximal fünf konfigurierbare Detailworker, Standard drei, mit Retry 1/3/10 s.
- Pre-Match-Zeitpunkte T−60, T−15, T−5 und T−1 ohne künstliches Nachholen.
- drei Halbzeit-Full-Snapshot-Phasen: sofort, +20 s, +60 s.
- strategische Minuten 55/60/65/70/75/80/85/90.
- Score-/Goal-Trigger, Final-/Verschwinde-Trigger, Core-Market-Tracking und
  Reopen-Erkennung über die historische Status-/Verfügbarkeitsfolge.
- maschinenlesbare Halbzeitberichte unter `data/halftime_reports/`.
- Streamlit-Seite **Data Collection** und **Event Data Inspector**, beide
  ausschließlich lesend.

## Echter Collector-Smoke-Test

Ausgeführt mit:

~~~powershell
python scripts/run_collector.py --root work/v02-smoke --once --workers 3
~~~

Ergebnis:

- Upcoming: 367 Events, HTTP-/Parsingfehler 0.
- Live: 72 Fußball-Events, HTTP-/Parsingfehler 0.
- Detailabrufe: 155, HTTP-/Parsingfehler 0.
- gespeicherte Snapshots: 151 `LIVE_PERIODIC`, 4 `HALFTIME`.
- Competition-Metadaten: 143.
- Reopens/Retry-Fehler in diesem kurzen Fenster: 0.

Dass in diesem One-Shot-Lauf keine Pre-Match-Snapshots entstanden, ist
erwartet: Der Collector erzeugt keine rückwirkenden T−60/T−15/T−5-Snapshots.

## Bewusst noch nicht enthalten

Keine Wettstrategie, keine Einsatzberechnung, kein ROI/EV/Ranking, keine
FotMob-Anbindung und keine Markt-Normalisierung. Diese Punkte bleiben für die
späteren Milestones zurückgestellt.

## Nächster Abnahmeschritt

Den Collector über einen vollständigen Spieltag laufen lassen und anschließend
die Coverage-Metriken in **Data Collection** prüfen: Pre-Match, mindestens ein
vollständiger Halbzeit-Full-Snapshot, HZ2-Core-Verlauf nach Toren soweit
beobachtbar sowie Final Score/HT Score für eine substanzielle Zahl von Events.
