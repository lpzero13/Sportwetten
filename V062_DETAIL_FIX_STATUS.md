# V0.6.2 – Spielansichten: Erstklick und FotMob-Zuordnung

Stand: 05.09.2026, lokal geprüft. Kein GitHub-Push / kein Container-Deployment.

## Ursachen und Korrekturen

1. Die beim Öffnen aller Spielansichten aufgerufene Scroll-Hilfe übergab
   `height=0` an das neue `st.iframe`. Streamlit 1.62 lehnt das mit
   `StreamlitInvalidHeightError` ab. Dadurch brach der erste Durchlauf vor
   Quoten, Analyse und FotMob ab. Der nächste Auto-Refresh konnte den Fehler
   verdecken, weil der Navigations-Intent dann bereits verbraucht war.
   Die Höhe ist jetzt 1 Pixel; der Hilfsframe ist nicht per Tab fokussierbar.
   Auch die Rücknavigation nutzt diese korrigierte Hilfe.
2. Bei Hoffenheim–Dortmund war der FotMob-Tagesindex vorhanden, aber das Matching
   lehnte die Kurzformen ab (`team_fuzzy_below_cutoff`). Ergänzt wurden genau
   die beobachteten Paare TSG Hoffenheim/Hoffenheim, Borussia Dortmund/Dortmund
   und Bayer Leverkusen/Leverkusen. Keine abgesenkten Matching-Schwellen;
   Heim/Auswärts, Wettbewerb, Land, Anstoßzeit und Teamkategorien bleiben geprüft.

## Reproduzierbare Tests

- Vier neue Navigationstests schlugen vor dem Fix mit dem konkreten
  Höhenfehler fehl; nachher erfolgreich. Testet alle drei Erstklicks bei
  deaktiviertem Auto-Refresh, Quoten-Refresh, Tab-Reihenfolge und Rückweg.
- Zehn Namensvarianten-/Schutztests: beobachtete Kurzformen, vertauschte Teams,
  Reserven, U19, Frauen, anderes Land, andere Liga, falscher Anstoß und
  mehrdeutige Kandidaten. Keine Verknüpfung bei verletzten Identitätsprüfungen.
- Vollständige Suite nach beiden Korrekturen: **200 passed, 1 skipped**, 83,40 s.
- Die Navigationstests verwenden synthetische Provider-Ergebnisse;
  die folgenden Browserprüfungen verwenden echte Live-Daten.

## Browserprüfung mit echten Daten

TSG Hoffenheim – Borussia Dortmund, Tipico `720968910`, FotMob `5881156`:

- Quoten direkt aus der Spielübersicht geöffnet: 34 Märkte / 108 Auswahlen
  beim Prüfabschnitt, sichtbare offene Quoten und gesperrte Auswahlen.
- FotMob direkt aus der Spielübersicht geöffnet: automatisch bestätigte
  Zuordnung und sichtbare Statistiktabelle, kein manueller ID-Eintrag nötig.
- Gesamt-xG beim Prüfabschnitt: 1,01 : 2,02; Schüsse 14 : 11.
- Periodenauswahl auf 1. Halbzeit: xG 0,60 : 0,59; Schüsse 9 : 4.
- Fehlende Shotmap-Daten werden ausdrücklich als nicht verfügbar angezeigt,
  nicht durch erfundene Werte ersetzt.
- Rückweg zeigt wieder die Spielübersicht.

Werte sind Momentaufnahmen und ändern sich während eines Live-Spiels.
Ein erfolgreicher Test dieses Spiels belegt keine universelle FotMob-Abdeckung.
Insbesondere unterschiedliche Provider-Spielminuten wurden hier nicht validiert.

Die lokale Laufzeit wurde regulär neu gestartet, damit alte negative
Resolver-Caches nicht weiterverwendet werden. Datenbank und Portfolio-Regeln
wurden nicht gelöscht oder verändert; automatische Provider-Zuordnungen werden
wie bisher durch den normalen Anwendungspfad gespeichert.

## Abschlussprüfung am 06.09.2026

- Um 09:19 Münchner Zeit: direkter Analyse-Button bei Leichhardt Tigers –
  NWS Spirit erfolgreich im Browser. Szenarien, Einsatzaufteilung, Quoten
  3,40 / 2,20 und Markt-Schätzungen sichtbar; kein Navigationsabbruch.
- Die 14 neuen Navigation-/Namensvarianten-Tests erneut bestanden (2,63 s).
- Oberfläche, Collector und Paper-Worker melden aktuelle Lebenszeichen.
- Weiterhin nur lokal geändert, nicht auf GitHub veröffentlicht.
