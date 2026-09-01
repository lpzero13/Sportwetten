# V0.5.6.2 – Zwei-Monats-All-Leagues-Status

**Status:** PASS  
**Zeitraum:** `2026-07-01` bis `2026-08-31` (inklusiv, 62 Kalendertage)  
**Scope:** FotMob-Tagesfeed aller Länder und Ligen; kein Liga-Filter; `10` Detail-Worker  
**Laufzeit:** `4 min 53.109 s` (2026-09-01T11:49:37.421611+00:00 bis 2026-09-01T11:54:30.530305+00:00 UTC)

## Ergebnis

Der vollständige All-Leagues-Tagesfeed lieferte **12,110 Feed-Zeilen** für **10,484 eindeutige Spiele**. Die Detailphase fand **3,896 eindeutige Spiele mit nutzbaren Halbzeitdaten**; **6,588 Spiele** wurden gemäß Collector-Regel ohne Halbzeitdaten übersprungen.

| Metrik | Wert |
| --- | --- |
| Tagesfeeds | 62 / 62 erfolgreich |
| Feed-Zeilen / Spiele | 12,110 / 10,484 |
| Gefundene Länder / Liga-Schlüssel | 89 / 256 |
| Eindeutige FotMob-Liga-IDs | 255 |
| Saisons | 2026/27 |
| Eindeutige Spiele mit HZ-Daten | 3,896 (37.2%) |
| Eindeutige Spiele ohne HZ-Daten | 6,588 (62.8%) |
| Ungeklärte Detailstatus | 0 |
| Next-Day-Late-Night-Markierungen | 1,604 |

Die 610 bereits vorhandenen HZ-Archive wurden im Lauf wiederverwendet; 3.286 neue HZ-Archive wurden geschrieben. Dadurch ist die beobachtete Speicherzunahme kleiner als ein vollständig leerer Erstimport desselben Zeitraums.

## Requests und Datenqualität

| Kennzahl | Wert |
| --- | --- |
| HTTP-Requests gesamt | 9,937 |
| Detail-Requests | 9,874 |
| Payload gesamt | 865.60 MiB (907.65 MB) |
| HTTP-Erfolg | 100.0% (alle 200) |
| 429 / 403 / 5xx / Timeout / Parse | 0 / 0 / 0 / 0 / 0 |
| Retries / Fehler | 0 / 0 |
| Detail-Worker | 10 |
| Collector-Status | PASS |

Die 9.937 Requests setzen sich aus einem Länder-/Liga-Katalog, 62 Tagesfeeds und 9.874 Detailabfragen zusammen. Ein Spiel ohne Halbzeitdaten erzeugt dabei trotzdem eine erfolgreiche Detailantwort, wird aber absichtlich nicht als Metrikarchiv gespeichert.

## Speicherverbrauch und Hochrechnung

SQLite ist der query-freundliche Katalog (inklusive WAL/SHM-Seiten); die Matchmetriken liegen im Parquet-Archiv. Die Delta-Werte sind die tatsächlich gemessene Dateigrößenänderung dieses Laufs.

| Bereich | Vorher | Nachher | Delta |
| --- | --- | --- | --- |
| SQLite inkl. WAL/SHM | 15.92 MiB (16.70 MB) | 34.43 MiB (36.10 MB) | 18.50 MiB (19.40 MB) |
| Parquet-Archiv | 55.50 MiB (58.20 MB) | 337.00 MiB (353.37 MB) | 281.50 MiB (295.18 MB) |
| SQLite + Parquet gesamt | 71.42 MiB (74.89 MB) | 371.43 MiB (389.47 MB) | 300.01 MiB (314.58 MB) |
| Parquet-Dateien | 2,456 | 15,077 | +12,621 |

### Beobachtete inkrementelle Rate dieses Laufs

| Speicher | pro Kalendertag | pro Ø-Monat (30,44 Tage) |
| --- | --- | --- |
| SQLite inkl. WAL/SHM | 305.61 KiB (312.94 kB) | 9.08 MiB (9.53 MB) |
| Parquet-Archiv | 4.54 MiB (4.76 MB) | 138.20 MiB (144.91 MB) |
| Gesamt | 4.84 MiB (5.07 MB) | 147.28 MiB (154.44 MB) |

### Hochrechnung für einen frischen Erstimport

Aus der gemessenen Delta-Rate ergeben sich für den kompletten Zeitraum (einschließlich der 610 schon vorhandenen HZ-Spiele) ungefähr **355.40 MiB (372.66 MB)**: SQLite-Katalog ca. **21.64 MiB (22.69 MB)**, Parquet ca. **333.76 MiB (349.97 MB)**.

| Schätzung | pro Kalendertag | pro Ø-Monat (30,44 Tage) |
| --- | --- | --- |
| SQLite-Katalog | 357.40 KiB (365.98 kB) | 10.62 MiB (11.14 MB) |
| Parquet-Metriken | 5.38 MiB (5.64 MB) | 163.85 MiB (171.81 MB) |
| Gesamt frischer Erstimport | 5.73 MiB (6.01 MB) | 174.48 MiB (182.95 MB) |

Normierte Messwerte: SQLite ca. 1,874 Byte je neuem Tagesfeed-Eintrag und Parquet ca. 89,829 Byte je neu archiviertem Spiel mit HZ-Daten. Das ist eine Näherung; Wochenenden, Ligen und die Anzahl der gelieferten Einzelmetriken verändern die Tageswerte.

## Monatsauswertung

| Monat | Feed-Zeilen | Eindeutige Spiele | HZ-Zeilen | HZ eindeutige Spiele | Ohne HZ eindeutige | Erstimport-Schätzung |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07 | 3,545 | 2,979 | 1,486 | 1,067 | 1,912 | 97.74 MiB (102.49 MB) |
| 2026-08 | 8,565 | 7,550 | 3,502 | 2,829 | 4,701 | 257.66 MiB (270.17 MB) |

Die Monatswerte zählen Feed-Zeilen nach Beobachtungstag. Für die Speicher-Schätzung wird ein Spiel nur an seinem ersten Beobachtungstag als HZ-Archivkosten angesetzt; damit werden die Next-Day-Duplikate nicht doppelt veranschlagt.

## Tagesauswertung

| Tag | Feed | Eindeutig | Next-Day | HZ-Zeilen | Ohne HZ eindeutig | HZ neu/erstmals | Erstimport-Schätzung |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 | 29 | 29 | 8 | 15 | 14 | 15 | 1.34 MiB (1.40 MB) |
| 2026-07-02 | 38 | 38 | 8 | 16 | 22 | 11 | 1.01 MiB (1.06 MB) |
| 2026-07-03 | 60 | 60 | 9 | 25 | 35 | 19 | 1.73 MiB (1.82 MB) |
| 2026-07-04 | 188 | 188 | 30 | 65 | 123 | 56 | 5.13 MiB (5.38 MB) |
| 2026-07-05 | 155 | 155 | 18 | 59 | 96 | 39 | 3.62 MiB (3.79 MB) |
| 2026-07-06 | 35 | 35 | 9 | 25 | 10 | 10 | 941.28 KiB (963.87 kB) |
| 2026-07-07 | 31 | 31 | 5 | 12 | 19 | 8 | 758.51 KiB (776.72 kB) |
| 2026-07-08 | 39 | 39 | 11 | 21 | 18 | 16 | 1.44 MiB (1.51 MB) |
| 2026-07-09 | 43 | 43 | 2 | 16 | 27 | 5 | 517.30 KiB (529.71 kB) |
| 2026-07-10 | 53 | 53 | 9 | 18 | 35 | 16 | 1.47 MiB (1.54 MB) |
| 2026-07-11 | 195 | 195 | 30 | 67 | 128 | 59 | 5.40 MiB (5.67 MB) |
| 2026-07-12 | 160 | 160 | 12 | 76 | 84 | 46 | 4.23 MiB (4.43 MB) |
| 2026-07-13 | 32 | 32 | 10 | 15 | 17 | 5 | 497.17 KiB (509.10 kB) |
| 2026-07-14 | 64 | 64 | 7 | 13 | 51 | 10 | 994.34 KiB (1018.21 kB) |
| 2026-07-15 | 58 | 58 | 15 | 22 | 36 | 19 | 1.73 MiB (1.82 MB) |
| 2026-07-16 | 76 | 76 | 12 | 33 | 43 | 19 | 1.76 MiB (1.85 MB) |
| 2026-07-17 | 112 | 112 | 28 | 50 | 62 | 39 | 3.54 MiB (3.71 MB) |
| 2026-07-18 | 250 | 250 | 35 | 107 | 143 | 84 | 7.64 MiB (8.01 MB) |
| 2026-07-19 | 113 | 113 | 12 | 61 | 52 | 30 | 2.77 MiB (2.91 MB) |
| 2026-07-20 | 44 | 44 | 7 | 20 | 24 | 13 | 1.19 MiB (1.25 MB) |
| 2026-07-21 | 86 | 86 | 16 | 24 | 62 | 23 | 2.12 MiB (2.23 MB) |
| 2026-07-22 | 110 | 110 | 37 | 58 | 52 | 48 | 4.31 MiB (4.52 MB) |
| 2026-07-23 | 113 | 113 | 14 | 66 | 47 | 31 | 2.86 MiB (3.00 MB) |
| 2026-07-24 | 127 | 127 | 29 | 59 | 68 | 46 | 4.17 MiB (4.37 MB) |
| 2026-07-25 | 436 | 436 | 74 | 143 | 293 | 125 | 11.49 MiB (12.05 MB) |
| 2026-07-26 | 327 | 327 | 29 | 166 | 161 | 111 | 10.09 MiB (10.58 MB) |
| 2026-07-27 | 91 | 91 | 14 | 46 | 45 | 26 | 2.39 MiB (2.51 MB) |
| 2026-07-28 | 88 | 88 | 22 | 29 | 59 | 22 | 2.04 MiB (2.14 MB) |
| 2026-07-29 | 117 | 117 | 28 | 45 | 72 | 33 | 3.04 MiB (3.18 MB) |
| 2026-07-30 | 116 | 116 | 18 | 51 | 65 | 32 | 2.95 MiB (3.09 MB) |
| 2026-07-31 | 159 | 159 | 32 | 63 | 96 | 51 | 4.65 MiB (4.88 MB) |
| 2026-08-01 | 459 | 459 | 70 | 159 | 300 | 142 | 12.98 MiB (13.62 MB) |
| 2026-08-02 | 341 | 341 | 27 | 165 | 176 | 113 | 10.29 MiB (10.79 MB) |
| 2026-08-03 | 118 | 118 | 18 | 45 | 73 | 28 | 2.61 MiB (2.74 MB) |
| 2026-08-04 | 86 | 86 | 20 | 34 | 52 | 26 | 2.38 MiB (2.50 MB) |
| 2026-08-05 | 131 | 131 | 35 | 58 | 73 | 44 | 4.00 MiB (4.20 MB) |
| 2026-08-06 | 111 | 111 | 17 | 60 | 51 | 35 | 3.20 MiB (3.35 MB) |
| 2026-08-07 | 187 | 187 | 36 | 76 | 111 | 65 | 5.90 MiB (6.19 MB) |
| 2026-08-08 | 665 | 665 | 67 | 214 | 451 | 195 | 17.89 MiB (18.76 MB) |
| 2026-08-09 | 401 | 401 | 26 | 199 | 202 | 156 | 14.08 MiB (14.76 MB) |
| 2026-08-10 | 93 | 93 | 14 | 43 | 50 | 24 | 2.22 MiB (2.33 MB) |
| 2026-08-11 | 139 | 139 | 22 | 36 | 103 | 30 | 2.82 MiB (2.96 MB) |
| 2026-08-12 | 103 | 103 | 31 | 48 | 55 | 35 | 3.18 MiB (3.34 MB) |
| 2026-08-13 | 103 | 103 | 17 | 50 | 53 | 31 | 2.84 MiB (2.98 MB) |
| 2026-08-14 | 204 | 204 | 27 | 83 | 121 | 73 | 6.62 MiB (6.94 MB) |
| 2026-08-15 | 695 | 695 | 84 | 244 | 451 | 229 | 20.86 MiB (21.87 MB) |
| 2026-08-16 | 479 | 479 | 38 | 213 | 266 | 157 | 14.31 MiB (15.00 MB) |
| 2026-08-17 | 134 | 134 | 18 | 60 | 74 | 36 | 3.32 MiB (3.48 MB) |
| 2026-08-18 | 138 | 138 | 15 | 48 | 90 | 40 | 3.67 MiB (3.85 MB) |
| 2026-08-19 | 147 | 147 | 47 | 64 | 83 | 53 | 4.80 MiB (5.04 MB) |
| 2026-08-20 | 141 | 141 | 19 | 75 | 66 | 35 | 3.25 MiB (3.41 MB) |
| 2026-08-21 | 218 | 218 | 28 | 93 | 125 | 83 | 7.50 MiB (7.86 MB) |
| 2026-08-22 | 722 | 722 | 81 | 272 | 450 | 255 | 23.14 MiB (24.26 MB) |
| 2026-08-23 | 454 | 454 | 32 | 240 | 214 | 180 | 16.23 MiB (17.02 MB) |
| 2026-08-24 | 131 | 131 | 19 | 67 | 64 | 46 | 4.17 MiB (4.38 MB) |
| 2026-08-25 | 164 | 164 | 14 | 56 | 108 | 46 | 4.23 MiB (4.44 MB) |
| 2026-08-26 | 208 | 208 | 25 | 56 | 152 | 48 | 4.48 MiB (4.70 MB) |
| 2026-08-27 | 100 | 100 | 13 | 64 | 36 | 48 | 4.29 MiB (4.50 MB) |
| 2026-08-28 | 232 | 232 | 32 | 107 | 125 | 100 | 8.98 MiB (9.42 MB) |
| 2026-08-29 | 697 | 697 | 67 | 262 | 435 | 243 | 22.06 MiB (23.13 MB) |
| 2026-08-30 | 518 | 518 | 34 | 228 | 290 | 174 | 15.83 MiB (16.60 MB) |
| 2026-08-31 | 246 | 246 | 21 | 83 | 163 | 59 | 5.49 MiB (5.76 MB) |

## Länder und Ligen mit gefundenen Daten

Im Feed wurden **89 Länder** und **256 Ligen** beobachtet. Die folgende Tabelle enthält alle gefundenen Liga-Schlüssel; `HZ eindeutig` bezeichnet Spiele, deren Detailantwort nutzbare Halbzeitdaten enthielt.

| Land | Liga | ID | Feed | Eindeutig | HZ eindeutig | Ohne HZ eindeutig | Next-Day |
| --- | --- | --- | --- | --- | --- | --- | --- |
| USA (USA) | MLS Next Pro | 10282 | 245 | 137 | 136 | 1 | 108 |
| USA (USA) | MLS | 130 | 218 | 111 | 111 | 0 | 107 |
| USA (USA) | USL Championship | 8972 | 214 | 108 | 106 | 2 | 105 |
| Argentinien (ARG) | Liga Profesional | 112 | 167 | 105 | 105 | 0 | 66 |
| Brasilien (BRA) | Serie B | 8814 | 159 | 100 | 100 | 0 | 60 |
| International (INT) | Vereins-Freundschaftsspiele | 489 | 529 | 523 | 92 | 431 | 6 |
| Ecuador (ECU) | Serie A | 246 | 133 | 89 | 89 | 0 | 44 |
| International (INT) | Conference League Qualifikation | 10615 | 260 | 260 | 83 | 177 | 0 |
| USA (USA) | NWSL | 9134 | 147 | 81 | 81 | 0 | 67 |
| USA (USA) | USL League One | 9296 | 163 | 82 | 78 | 4 | 81 |
| Bolivien (BOL) | Primera Division | 144 | 116 | 77 | 77 | 0 | 39 |
| China (CHN) | Super League | 120 | 74 | 72 | 69 | 3 | 0 |
| Schweden (SWE) | Allsvenskan | 67 | 69 | 69 | 69 | 0 | 0 |
| Brasilien (BRA) | Serie A | 268 | 109 | 72 | 68 | 4 | 38 |
| Südkorea (KOR) | K League 2 | 9116 | 72 | 72 | 68 | 4 | 0 |
| Südkorea (KOR) | K League 1 | 9080 | 65 | 65 | 65 | 0 | 0 |
| Peru (PER) | Liga 1 | 131 | 80 | 62 | 62 | 0 | 18 |
| Kolumbien (COL) | Primera A | 274 | 124 | 74 | 58 | 16 | 51 |
| England (ENG) | EFL Cup | 133 | 58 | 58 | 58 | 0 | 0 |
| International (INT) | Leagues Cup | 10043 | 115 | 58 | 56 | 2 | 57 |
| Norwegen (NOR) | Eliteserien | 59 | 55 | 55 | 55 | 0 | 0 |
| Rumänien (ROU) | Liga I | 189 | 55 | 55 | 55 | 0 | 0 |
| Mexiko (MEX) | Liga MX | 230 | 107 | 54 | 54 | 0 | 53 |
| Island (ISL) | Besta deildin | 215 | 51 | 51 | 51 | 0 | 0 |
| Finnland (FIN) | Veikkausliiga | 51 | 50 | 50 | 50 | 0 | 0 |
| Russland (RUS) | Premier League | 63 | 48 | 48 | 48 | 0 | 0 |
| Polen (POL) | Ekstraklasa | 196 | 52 | 52 | 47 | 5 | 0 |
| Chile (CHI) | Liga de Primera | 273 | 68 | 48 | 46 | 2 | 22 |
| Serbien (SRB) | Super Liga | 182 | 51 | 49 | 45 | 4 | 2 |
| International (INT) | Europa League Qualifikation | 10613 | 82 | 82 | 43 | 39 | 0 |
| Tschechien (CZE) | 1. Liga | 122 | 44 | 44 | 43 | 1 | 0 |
| Niederlande (NED) | Eerste Divisie | 111 | 42 | 42 | 42 | 0 | 0 |
| Paraguay (PAR) | Division Profesional | 199 | 42 | 42 | 42 | 0 | 0 |
| Venezuela (VEN) | Primera Division | 339 | 88 | 42 | 41 | 1 | 34 |
| Japan (JPN) | J. League | 223 | 40 | 40 | 40 | 0 | 0 |
| Türkei (TUR) | 1. Lig | 165 | 40 | 40 | 40 | 0 | 0 |
| Kanada (CAN) | Premier League | 9986 | 58 | 37 | 36 | 1 | 21 |
| Dänemark (DEN) | 1. Division | 85 | 36 | 36 | 36 | 0 | 0 |
| England (ENG) | Championship | 48 | 36 | 36 | 36 | 0 | 0 |
| England (ENG) | League Two | 109 | 36 | 36 | 36 | 0 | 0 |
| Frankreich (FRA) | Ligue 2 | 110 | 36 | 36 | 36 | 0 | 0 |
| England (ENG) | League One | 108 | 35 | 35 | 35 | 0 | 0 |
| Saudi-Arabien (KSA) | Saudi Pro League | 536 | 35 | 35 | 35 | 0 | 0 |
| International (INT) | Champions League Qualifikation | 10611 | 90 | 90 | 34 | 56 | 0 |
| Slowakei (SVK) | 1. liga | 176 | 36 | 36 | 34 | 2 | 0 |
| Dänemark (DEN) | Superligaen | 46 | 34 | 34 | 34 | 0 | 0 |
| International (INT) | Women's Africa Cup of Nations | 10371 | 34 | 34 | 34 | 0 | 0 |
| Lettland (LVA) | Virsliga | 226 | 35 | 34 | 33 | 1 | 1 |
| Niederlande (NED) | Eredivisie | 57 | 34 | 34 | 33 | 1 | 0 |
| Portugal (POR) | Liga Portugal | 61 | 34 | 34 | 33 | 1 | 0 |
| Belgien (BEL) | First Division A | 40 | 33 | 33 | 33 | 0 | 0 |
| Spanien (ESP) | LaLiga2 | 140 | 33 | 33 | 33 | 0 | 0 |
| International (INT) | Copa Sudamericana | 299 | 64 | 32 | 32 | 0 | 32 |
| Südafrika (RSA) | Premier Soccer League | 537 | 32 | 32 | 32 | 0 | 0 |
| Deutschland (GER) | DFB Pokal | 209 | 30 | 30 | 30 | 0 | 0 |
| Irland (IRL) | Premier Liga | 126 | 30 | 30 | 30 | 0 | 0 |
| Spanien (ESP) | LaLiga | 87 | 30 | 30 | 30 | 0 | 0 |
| England (ENG) | Premier League 2 | 9084 | 30 | 30 | 29 | 1 | 0 |
| Schottland (SCO) | SWPL Cup | 11019 | 30 | 30 | 28 | 2 | 0 |
| Australien (AUS) | Australia Cup | 9471 | 29 | 28 | 28 | 0 | 1 |
| Kanada (CAN) | Northern Super League | 10872 | 40 | 28 | 28 | 0 | 12 |
| Deutschland (GER) | 3. Liga | 208 | 30 | 30 | 27 | 3 | 0 |
| Schweiz (SUI) | Super League | 69 | 28 | 28 | 27 | 1 | 0 |
| Deutschland (GER) | 2. Bundesliga | 146 | 27 | 27 | 27 | 0 | 0 |
| Türkei (TUR) | Süper Lig | 71 | 27 | 27 | 27 | 0 | 0 |
| Österreich (AUT) | Bundesliga | 38 | 27 | 27 | 27 | 0 | 0 |
| International (INT) | ASEAN Championship | 9265 | 26 | 26 | 26 | 0 | 0 |
| International (INT) | WM | 77 | 34 | 26 | 26 | 0 | 8 |
| Schweiz (SUI) | Challenge League | 163 | 26 | 26 | 26 | 0 | 0 |
| International (INT) | CONCACAF Championship U20 | 9656 | 41 | 25 | 25 | 0 | 16 |
| Kroatien (CRO) | HNL | 252 | 25 | 25 | 24 | 1 | 0 |
| USA (USA) | USL Cup | 10654 | 47 | 24 | 24 | 0 | 23 |
| Ägypten (EGY) | Premier League | 519 | 23 | 23 | 23 | 0 | 0 |
| Belgien (BEL) | First Division B | 264 | 21 | 21 | 21 | 0 | 0 |
| Saudi-Arabien (KSA) | Saudi First Division | 10721 | 21 | 21 | 21 | 0 | 0 |
| Vereinigte Arabische Emirate (UAE) | Pro League | 538 | 21 | 21 | 21 | 0 | 0 |
| Brasilien (BRA) | Copa do Brasil | 9067 | 36 | 20 | 20 | 0 | 16 |
| England (ENG) | Premier League | 47 | 20 | 20 | 20 | 0 | 0 |
| Italien (ITA) | Serie A | 55 | 20 | 20 | 20 | 0 | 0 |
| Italien (ITA) | Serie B | 86 | 20 | 20 | 20 | 0 | 0 |
| Schottland (SCO) | Premiership | 64 | 21 | 21 | 19 | 2 | 0 |
| Frankreich (FRA) | Ligue 1 | 53 | 18 | 18 | 18 | 0 | 0 |
| Italien (ITA) | Coppa Italia | 141 | 20 | 20 | 16 | 4 | 0 |
| International (INT) | Copa Libertadores | 45 | 32 | 16 | 16 | 0 | 16 |
| Saudi-Arabien (KSA) | King's Cup | 9942 | 16 | 16 | 15 | 1 | 0 |
| Schottland (SCO) | SWPL 1 | 10791 | 15 | 15 | 15 | 0 | 0 |
| Deutschland (GER) | Frauen Bundesliga | 9676 | 14 | 14 | 14 | 0 | 0 |
| England (ENG) | National League Cup | 10705 | 16 | 16 | 13 | 3 | 0 |
| Griechenland (GRE) | Super League 1 | 135 | 14 | 14 | 13 | 1 | 0 |
| Argentinien (ARG) | Copa Argentina | 9305 | 20 | 12 | 12 | 0 | 8 |
| Israel (ISR) | Ligat ha'Al | 127 | 12 | 12 | 12 | 0 | 0 |
| Katar (QAT) | Qatar Stars League | 535 | 12 | 12 | 12 | 0 | 0 |
| International (INT) | UEFA U19 Championship | 287 | 10 | 10 | 10 | 0 | 0 |
| Deutschland (GER) | Bundesliga | 54 | 9 | 9 | 9 | 0 | 0 |
| England (ENG) | EFL Trophy | 142 | 9 | 9 | 8 | 1 | 0 |
| Kanada (CAN) | Canadian Championship | 9837 | 14 | 8 | 8 | 0 | 6 |
| Spanien (ESP) | Liga F | 9907 | 8 | 8 | 8 | 0 | 0 |
| Südafrika (RSA) | South Africa 8 Cup | 9474 | 8 | 8 | 8 | 0 | 0 |
| Katar (QAT) | Second Division | 11030 | 10 | 10 | 6 | 4 | 0 |
| Chile (CHI) | Copa de la Liga | 11697 | 7 | 4 | 4 | 0 | 3 |
| England (ENG) | EFL Cup Qualifikation | 12889 | 2 | 2 | 2 | 0 | 0 |
| Wales (WAL) | UEFA U19 Championship | 287 | 2 | 2 | 2 | 0 | 0 |
| Belgien (BEL) | Super Cup | 266 | 1 | 1 | 1 | 0 | 0 |
| Deutschland (GER) | Super Cup | 8924 | 1 | 1 | 1 | 0 | 0 |
| Deutschland (GER) | Supercup der Frauen | 11034 | 1 | 1 | 1 | 0 | 0 |
| England (ENG) | Community Shield | 247 | 1 | 1 | 1 | 0 | 0 |
| Frankreich (FRA) | Trophée des champions | 207 | 1 | 1 | 1 | 0 | 0 |
| International (INT) | FIFA Intercontinental Cup | 10703 | 1 | 1 | 1 | 0 | 0 |
| International (INT) | UEFA Super Cup | 74 | 1 | 1 | 1 | 0 | 0 |
| Israel (ISR) | Super Cup | 9862 | 1 | 1 | 1 | 0 | 0 |
| Mexiko (MEX) | Campeón de Campeones | 11039 | 2 | 1 | 1 | 0 | 1 |
| Niederlande (NED) | Super Cup | 237 | 1 | 1 | 1 | 0 | 0 |
| Polen (POL) | Superpuchar Polski | 200 | 1 | 1 | 1 | 0 | 0 |
| Portugal (POR) | Super Cup | 188 | 1 | 1 | 1 | 0 | 0 |
| Russland (RUS) | Super Cup | 195 | 1 | 1 | 1 | 0 | 0 |
| England (ENG) | Premier Liga | 8947 | 283 | 283 | 0 | 283 | 0 |
| Argentinien (ARG) | Primera B Metropolitana & Torneo Federal A | 9213 | 301 | 274 | 0 | 274 | 28 |
| Deutschland (GER) | Regionalliga | 512 | 248 | 247 | 0 | 247 | 0 |
| Norwegen (NOR) | Norsk Tipping-ligaen | 205 | 206 | 206 | 0 | 206 | 0 |
| Schottland (SCO) | Highland / Lowland | 9545 | 175 | 175 | 0 | 175 | 0 |
| Argentinien (ARG) | Primera B Nacional | 8965 | 196 | 163 | 0 | 163 | 27 |
| England (ENG) | National North & South | 8944 | 142 | 142 | 0 | 142 | 0 |
| Schottland (SCO) | League Cup | 180 | 88 | 88 | 0 | 88 | 0 |
| Schweden (SWE) | Ettan | 169 | 80 | 80 | 0 | 80 | 0 |
| Russland (RUS) | 1. Division | 338 | 76 | 76 | 0 | 76 | 0 |
| Brasilien (BRA) | Serie C | 8971 | 98 | 73 | 0 | 73 | 25 |
| China (CHN) | China League One | 9137 | 72 | 72 | 0 | 72 | 0 |
| International (INT) | Women's Champions League Qualifikation | 10612 | 71 | 71 | 0 | 71 | 0 |
| Norwegen (NOR) | PostNord-ligaen | 204 | 71 | 71 | 0 | 71 | 0 |
| Chile (CHI) | Liga de Ascenso | 9126 | 90 | 64 | 0 | 64 | 27 |
| Usbekistan (UZB) | Superliga | 540 | 64 | 64 | 0 | 64 | 0 |
| Brasilien (BRA) | Serie D | 9464 | 70 | 62 | 0 | 62 | 8 |
| Kasachstan (KAZ) | Premier League | 225 | 63 | 62 | 0 | 62 | 0 |
| Norwegen (NOR) | Norgesmesterskapet | 206 | 62 | 62 | 0 | 62 | 0 |
| Finnland (FIN) | Ykkonen | 8969 | 60 | 60 | 0 | 60 | 0 |
| Island (ISL) | 1. Deild | 216 | 60 | 60 | 0 | 60 | 0 |
| Island (ISL) | 2. Deild | 10226 | 60 | 60 | 0 | 60 | 0 |
| Italien (ITA) | Serie C | 147 | 60 | 60 | 0 | 60 | 0 |
| England (ENG) | National League | 117 | 59 | 59 | 0 | 59 | 0 |
| Uruguay (URU) | Liga AUF Uruguaya | 161 | 77 | 58 | 0 | 58 | 15 |
| Belarus (BLR) | Premier League | 263 | 56 | 56 | 0 | 56 | 0 |
| Norwegen (NOR) | OBOS-ligaen | 203 | 56 | 56 | 0 | 56 | 0 |
| Uruguay (URU) | Segunda Division | 9122 | 74 | 56 | 0 | 56 | 14 |
| Rumänien (ROU) | Liga II | 9113 | 55 | 55 | 0 | 55 | 0 |
| Schweden (SWE) | Superettan | 168 | 55 | 55 | 0 | 55 | 0 |
| Bulgarien (BUL) | Second Professional League | 9096 | 54 | 54 | 0 | 54 | 0 |
| Polen (POL) | I Liga | 197 | 54 | 54 | 0 | 54 | 0 |
| Polen (POL) | II Liga | 8935 | 54 | 54 | 0 | 54 | 0 |
| Brasilien (BRA) | Copa Paulista | 11646 | 67 | 52 | 0 | 52 | 17 |
| Südkorea (KOR) | Cup | 9551 | 51 | 51 | 0 | 51 | 0 |
| Bulgarien (BUL) | First Professional League | 270 | 49 | 49 | 0 | 49 | 0 |
| Kolumbien (COL) | Primera B | 9125 | 78 | 48 | 0 | 48 | 25 |
| Slowakei (SVK) | 2. Liga | 8973 | 48 | 48 | 0 | 48 | 0 |
| Tschechien (CZE) | FNL | 253 | 48 | 48 | 0 | 48 | 0 |
| Ungarn (HUN) | NB II | 9117 | 48 | 48 | 0 | 48 | 0 |
| Wales (WAL) | Cymru Premier | 116 | 48 | 48 | 0 | 48 | 0 |
| Mexiko (MEX) | Liga de Expansión MX | 8976 | 89 | 47 | 0 | 47 | 42 |
| Mexiko (MEX) | Liga MX Femenil | 9906 | 82 | 46 | 0 | 46 | 37 |
| Finnland (FIN) | Ykkosliiga | 251 | 45 | 45 | 0 | 45 | 0 |
| England (ENG) | Premier League U18 | 10068 | 43 | 43 | 0 | 43 | 0 |
| Indien (IND) | Durand Cup | 10309 | 43 | 43 | 0 | 43 | 0 |
| Schweden (SWE) | Eliteettan (W) | 10308 | 42 | 42 | 0 | 42 | 0 |
| El Salvador (SLV) | Primera Division | 335 | 65 | 41 | 0 | 41 | 24 |
| Chile (CHI) | Copa Chile | 9091 | 58 | 40 | 0 | 40 | 18 |
| International (INT) | CONCACAF Central American Cup | 9682 | 80 | 40 | 0 | 40 | 40 |
| Irak (IRQ) | Stars League | 524 | 40 | 40 | 0 | 40 | 0 |
| Irland (IRL) | First Division | 218 | 40 | 40 | 0 | 40 | 0 |
| Japan (JPN) | J. League 2 | 8974 | 40 | 40 | 0 | 40 | 0 |
| Luxemburg (LUX) | National Liga | 229 | 40 | 40 | 0 | 40 | 0 |
| Schottland (SCO) | Challenge Cup | 179 | 40 | 40 | 0 | 40 | 0 |
| Österreich (AUT) | 2. Liga | 119 | 40 | 40 | 0 | 40 | 0 |
| Irland (IRL) | Women's Premier Liga | 10210 | 40 | 39 | 0 | 39 | 0 |
| Japan (JPN) | J. League 3 | 9136 | 39 | 39 | 0 | 39 | 0 |
| Estland (EST) | Regular Season | 248 | 36 | 36 | 0 | 36 | 0 |
| Frankreich (FRA) | Ligue 3 | 8970 | 36 | 36 | 0 | 36 | 0 |
| Iran (IRN) | Persian Gulf | 523 | 36 | 36 | 0 | 36 | 0 |
| Moldau (MDA) | National Division | 231 | 36 | 36 | 0 | 36 | 0 |
| Panama (PAN) | LPF | 9039 | 65 | 36 | 0 | 36 | 30 |
| Portugal (POR) | Liga Portugal 2 | 185 | 36 | 36 | 0 | 36 | 0 |
| Guatemala (GUA) | Liga Nacional | 336 | 57 | 35 | 0 | 35 | 22 |
| Schweden (SWE) | Damallsvenskan (W) | 9089 | 35 | 35 | 0 | 35 | 0 |
| Slowenien (SVN) | Prva Liga | 173 | 35 | 35 | 0 | 35 | 0 |
| Tansania (TAN) | Premier League | 9066 | 35 | 35 | 0 | 35 | 0 |
| Ungarn (HUN) | Nemzeti Bajnokság I | 212 | 34 | 34 | 0 | 34 | 0 |
| Japan (JPN) | Emperor Cup | 9011 | 32 | 32 | 0 | 32 | 0 |
| Litauen (LTU) | Toplyga | 228 | 32 | 32 | 0 | 32 | 0 |
| Österreich (AUT) | Austrian Cup | 278 | 32 | 32 | 0 | 32 | 0 |
| Schweden (SWE) | Svenska Cupen | 171 | 31 | 31 | 0 | 31 | 0 |
| Ukraine (UKR) | Premier League | 441 | 31 | 31 | 0 | 31 | 0 |
| Armenien (ARM) | Premier League | 118 | 30 | 30 | 0 | 30 | 0 |
| Costa Rica (CRC) | Primera Division | 121 | 57 | 30 | 0 | 30 | 27 |
| Dänemark (DEN) | 2. Division | 239 | 30 | 30 | 0 | 30 | 0 |
| Dänemark (DEN) | 3. Division | 240 | 30 | 30 | 0 | 30 | 0 |
| Färöer-Inseln (FRO) | Premier League | 250 | 30 | 30 | 0 | 30 | 0 |
| Honduras (HON) | Liga Nacional | 337 | 50 | 30 | 0 | 30 | 20 |
| Norwegen (NOR) | 1. Division Kvinner | 332 | 30 | 30 | 0 | 30 | 0 |
| Portugal (POR) | Liga 3 | 9112 | 30 | 30 | 0 | 30 | 0 |
| Finnland (FIN) | Kansallinen (Women) | 10174 | 28 | 28 | 0 | 28 | 0 |
| Südkorea (KOR) | K League 3 | 9537 | 28 | 28 | 0 | 28 | 0 |
| Iran (IRN) | Azadegan League | 9372 | 27 | 27 | 0 | 27 | 0 |
| Niederlande (NED) | Tweede Divisie | 9195 | 27 | 27 | 0 | 27 | 0 |
| Kolumbien (COL) | Copa Colombia | 9490 | 50 | 26 | 0 | 26 | 23 |
| Tschechien (CZE) | Czech Cup | 254 | 26 | 26 | 0 | 26 | 0 |
| Montenegro (MNE) | 1. CFL | 232 | 25 | 25 | 0 | 25 | 0 |
| Nordmazedonien (MKD) | Prva Liga | 249 | 25 | 25 | 0 | 25 | 0 |
| Schottland (SCO) | League Two | 125 | 25 | 25 | 0 | 25 | 0 |
| Dänemark (DEN) | A-Liga | 256 | 24 | 24 | 0 | 24 | 0 |
| Dänemark (DEN) | DBU Pokalen | 242 | 24 | 24 | 0 | 24 | 0 |
| Irland (IRL) | FAI Cup | 219 | 24 | 24 | 0 | 24 | 0 |
| Israel (ISR) | Leumit League | 128 | 24 | 24 | 0 | 24 | 0 |
| Schottland (SCO) | League One | 124 | 24 | 24 | 0 | 24 | 0 |
| Nordirland (NIR) | Premiership | 129 | 23 | 23 | 0 | 23 | 0 |
| Schottland (SCO) | Championship | 123 | 20 | 20 | 0 | 20 | 0 |
| Spanien (ESP) | Primera Federación | 8968 | 20 | 20 | 0 | 20 | 0 |
| Bosnien und Herzegowina (BIH) | Premier League | 267 | 19 | 19 | 0 | 19 | 0 |
| Georgien (GEO) | Erovnuli Liga | 439 | 19 | 19 | 0 | 19 | 0 |
| Aserbaidschan (AZE) | Premier League | 262 | 18 | 18 | 0 | 18 | 0 |
| Deutschland (GER) | DFB Pokal Frauen | 10650 | 16 | 16 | 0 | 16 | 0 |
| Indonesien (IDN) | President's Cup | 10059 | 16 | 16 | 0 | 16 | 0 |
| Kroatien (CRO) | Croatian Cup | 275 | 16 | 16 | 0 | 16 | 0 |
| Marokko (MAR) | Botola Pro | 530 | 16 | 16 | 0 | 16 | 0 |
| Paraguay (PAR) | Copa Paraguay | 10230 | 16 | 16 | 0 | 16 | 0 |
| Russland (RUS) | Russian Cup | 193 | 16 | 16 | 0 | 16 | 0 |
| Tunesien (TUN) | Ligue I | 544 | 16 | 16 | 0 | 16 | 0 |
| Norwegen (NOR) | Toppserien | 331 | 15 | 15 | 0 | 15 | 0 |
| Irland (IRL) | FAI Women's Cup (W) | 10307 | 14 | 14 | 0 | 14 | 0 |
| Malaysia (MAS) | Liga Super | 8985 | 14 | 14 | 0 | 14 | 0 |
| Schweiz (SUI) | Swiss Cup | 164 | 13 | 13 | 0 | 13 | 0 |
| Finnland (FIN) | Kansallinen Qualification (Women) | 10186 | 12 | 12 | 0 | 12 | 0 |
| International (INT) | UEFA Women's Europa Cup | 11129 | 12 | 12 | 0 | 12 | 0 |
| Japan (JPN) | WE League | 9500 | 12 | 12 | 0 | 12 | 0 |
| Griechenland (GRE) | Greece Cup | 145 | 11 | 11 | 0 | 11 | 0 |
| USA (USA) | USL Super League Women | 10699 | 21 | 11 | 0 | 11 | 10 |
| Albanien (ALB) | Kategoria Superiore | 260 | 10 | 10 | 0 | 10 | 0 |
| Kuwait (KUW) | Premier League | 529 | 10 | 10 | 0 | 10 | 0 |
| Niederlande (NED) | Eredivisie Vrouwen | 10289 | 10 | 10 | 0 | 10 | 0 |
| Nigeria (NGA) | Professional Football League | 533 | 10 | 10 | 0 | 10 | 0 |
| China (CHN) | Chinese FA Cup | 9550 | 8 | 8 | 0 | 8 | 0 |
| Israel (ISR) | Toto Cup Leumit | 9098 | 8 | 8 | 0 | 8 | 0 |
| Zypern (CYP) | 1. Division | 136 | 7 | 7 | 0 | 7 | 0 |
| Tansania (TAN) | Premier League Qualifikation | 10028 | 5 | 5 | 0 | 5 | 0 |
| International (INT) | AFC Champions League Elite Qualifikation | 10622 | 4 | 4 | 0 | 4 | 0 |
| Italien (ITA) | Coppa Italia Women | 11014 | 2 | 2 | 0 | 2 | 0 |
| Argentinien (ARG) | Super Copa International | 9381 | 1 | 1 | 0 | 1 | 0 |
| Costa Rica (CRC) | Primera Liga Qualifikation | 12888 | 2 | 1 | 0 | 1 | 1 |
| Costa Rica (CRC) | Super cup | 10223 | 2 | 1 | 0 | 1 | 1 |
| Finnland (FIN) | Suomen Cup | 143 | 1 | 1 | 0 | 1 | 0 |
| Griechenland (GRE) | Super Cup | 8816 | 1 | 1 | 0 | 1 | 0 |
| International (INT) | AFC Champions League Two | 9469 | 1 | 1 | 0 | 1 | 0 |
| International (INT) | Freundschaftsspiele | 114 | 1 | 1 | 0 | 1 | 0 |
| International (INT) | MLS All-Star Game | 13169 | 2 | 1 | 0 | 1 | 1 |
| Island (ISL) | Icelandic Cup | 217 | 1 | 1 | 0 | 1 | 0 |
| Marokko (MAR) | Throne Cup | 11840 | 1 | 1 | 0 | 1 | 0 |
| Niederlande (NED) | Super Cup | 11029 | 1 | 1 | 0 | 1 | 0 |
| Rumänien (ROU) | Supercupa | 192 | 1 | 1 | 0 | 1 | 0 |
| Vietnam (VIE) | Super Cup | 10737 | 1 | 1 | 0 | 1 | 0 |

Vollständige maschinenlesbare Liga-Liste: [outputs/V0562_TWO_MONTH_LEAGUES.csv](outputs/V0562_TWO_MONTH_LEAGUES.csv)

## Events mit Halbzeitdaten

Es wurden **3,896 eindeutige Events** mit HZ-Daten gespeichert. Die vollständige Eventliste mit Datum, Land, Liga, Saison, Match-ID, Teams und Detailstatus liegt hier: [outputs/V0562_TWO_MONTH_EVENTS.csv](outputs/V0562_TWO_MONTH_EVENTS.csv). Sie enthält keine Spiele ohne Halbzeitdaten.

Beispiele der ersten 20 gespeicherten Events:

| Datum | Land | Liga | Spiel | Match-ID | HZ-Status |
| --- | --- | --- | --- | --- | --- |
| 2026-07-01 | Brasilien | Serie B | Botafogo SP – CRB | 5190579 | FETCHED |
| 2026-07-01 | International | WM | Mexico – Ecuador | 4653713 | FETCHED |
| 2026-07-01 | Lettland | Virsliga | FK Tukums 2000 – SK Super Nova | 5162021 | FETCHED |
| 2026-07-01 | International | WM | England – DR Congo | 4653714 | FETCHED |
| 2026-07-01 | Kanada | Premier League | Forge FC – Vancouver FC | 1000008670 | FETCHED |
| 2026-07-01 | Wales | UEFA U19 Championship | Denmark U19 – Spain U19 | 5344657 | FETCHED |
| 2026-07-01 | Wales | UEFA U19 Championship | Wales U19 – Germany U19 | 5344658 | FETCHED |
| 2026-07-01 | Kanada | Premier League | HFX Wanderers FC – Atlético Ottawa | 1000008671 | FETCHED |
| 2026-07-01 | International | WM | Belgium – Senegal | 4653710 | FETCHED |
| 2026-07-01 | USA | USL League One | Charlotte Independence – Corpus Christi | 5109439 | FETCHED |
| 2026-07-01 | Ecuador | Serie A | LDU de Quito – Orense | 1000008999 | FETCHED |
| 2026-07-01 | USA | USL League One | Chattanooga Red Wolves SC – Westchester SC | 5109440 | FETCHED |
| 2026-07-01 | International | WM | USA – Bosnia and Herzegovina | 4653709 | FETCHED |
| 2026-07-01 | USA | USL League One | Spokane Velocity FC – Forward Madison FC | 5109442 | FETCHED |
| 2026-07-01 | USA | USL League One | Union Omaha – AV Alta | 5109441 | FETCHED |
| 2026-07-02 | International | UEFA U19 Championship | Croatia U19 – Italy U19 | 5344663 | FETCHED |
| 2026-07-02 | Island | Besta deildin | Thor Akureyri – KR Reykjavik | 5225672 | FETCHED |
| 2026-07-02 | International | UEFA U19 Championship | Serbia U19 – Ukraine U19 | 5344664 | FETCHED |
| 2026-07-02 | International | WM | Spain – Austria | 4653708 | FETCHED |
| 2026-07-02 | Island | Besta deildin | Vikingur Reykjavik – KA Akureyri | 5225673 | FETCHED |

## Prüfergebnis

| Check | Ergebnis |
| --- | --- |
| 62/62 Tagesfeed-Runs vorhanden | PASS |
| Alle Tagesfeed-Runs COMPLETE | PASS |
| Tagesfeed-Zeilen = 12.110 | PASS |
| Eindeutige Spiele = 10.484 | PASS |
| Detailstatus ohne Fehler/Unknown | PASS |
| HTTP/API | PASS – 100% HTTP 200, keine Fehler |

## Reproduzierbarkeit und Ablage

Verwendeter Lauf:

```text
python -m fotmob.history_cli --root . dates --from-date 2026-07-01 --to-date 2026-08-31 --workers 10
```

Tagesindex und Detailstatus liegen in `data/tipico.db`; die kanonischen Match-/Perioden-/Schuss-/Eventdaten liegen unter `data/archive/fotmob`. Die tägliche CSV-Auswertung ist hier abgelegt: [outputs/V0562_TWO_MONTH_DAILY.csv](outputs/V0562_TWO_MONTH_DAILY.csv).

Hinweis: `includeNextDayLateNight=true` bleibt aktiv. Die 1.604 entsprechenden Feed-Zeilen sind markiert, nicht verworfen; ein Event wird für die Archiv-Hochrechnung trotzdem nur einmal gezählt.

