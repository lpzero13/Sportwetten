# FotMob Final Validation – V0.5.1

Stand: 30.08.2026, Europe/Berlin

## Abschlussentscheidung

```text
FOTMOB_PROVIDER_DECISION = LIMITED_USE
AUTOMATED_USAGE = UNCLEAR
FOTMOB_V051_STATUS = PASS
```

`PASS` bedeutet in diesem Meilenstein, dass das FotMob-Thema mit einer
begründeten Provider-Entscheidung abgeschlossen ist. Es bedeutet ausdrücklich
nicht, dass FotMob für einen produktiven automatischen Collector freigegeben
wurde.

## 1. Quellen- und Nutzungsprüfung

Die öffentliche FotMob-Seite wurde am 30.08.2026 im Browser ohne Login
geprüft. Die Startseite zeigt Spiele nach Land und Wettbewerb; die
Bundesliga-Ansicht zeigt `Bundesliga` und `Deutschland`. Auf einer beendeten
Bundesliga-Partie waren Teams, Anstoß, Wettbewerb, Endstand, Halbzeitstand,
Timeline und Statistiken sichtbar.

Geprüfte Seiten:

- [FotMob Startseite](https://www.fotmob.com/)
- [FotMob Bundesliga](https://www.fotmob.com/de/leagues/54/overview/bundesliga)
- [Bayern München – VfB Stuttgart](https://www.fotmob.com/de/matches/bayern-munchen-vs-vfb-stuttgart/3c6nc5#5881143)

Auf der öffentlichen Seite steht außerdem der Hinweis, dass automatische
Dienste und systematische oder regelmäßige Nutzung nicht erlaubt sind. Damit
ist eine dauerhafte automatisierte Nutzung für dieses Projekt nicht
hinreichend geklärt. Es wurden keine Login-, Cookie-, Token-, CAPTCHA- oder
Rate-Limit-Umgehungen vorgenommen.

## 2. Technische Validierung

| Bereich | Ergebnis | Einordnung |
|---|---|---|
| Browser-Zugriff ohne Login | PASS | öffentliche Seiten lesbar |
| Liga/Land/Saison im UI | PASS für geprüfte Ansicht | Bundesliga / Deutschland / Saisonwahl sichtbar |
| Match-Metadaten und FT/HT-Score | PASS für Beispielmatch | 5:1, HZ 1:0 und Timeline sichtbar |
| HT-Statistiken | PASS für Beispielmatch | Periodenfilter `1.` zeigt eigene HT-Werte |
| stabiler öffentlicher JSON-Vertrag | NICHT BESTÄTIGT | sichtbares UI ist kein API-Vertrag |
| strukturierter Serienabruf | NICHT AUSGEFÜHRT | wegen `AUTOMATED_USAGE=UNCLEAR` |
| 60-Minuten-Stabilitätstest | NICHT AUSGEFÜHRT | keine Freigabe für regelmäßige Automation |
| HTTP-/Rate-Limit-Serie | NICHT AUSGEFÜHRT | daher keine Aussage zu 403/429-Raten |
| Offline-Parser/Matcher/Storage | PASS | durch lokale Regressionstests und Parquet-Roundtrip |

Der lokale Client bleibt als bewusst begrenzter Adapter vorhanden. Er führt
keinen Netzwerklauf aus, solange `FOTMOB_ENABLED=false` ist; der periodische
Worker wird zusätzlich nur bei `PRODUCTION_READY` plus
`ACCEPTABLE_FOR_PROJECT` freigegeben.

## 3. Matching-Validierung

Die reale Browser-Stichprobe umfasst ein manuell geprüftes FotMob-Match. Eine
Tipico-Paarung dieses Beispiels wurde in dieser Abschlussprüfung nicht als
automatischer Serienlauf ausgeführt. Die automatische Matching-Coverage ist
daher `NOT_RUN_BY_POLICY`, nicht ein künstlich als 0/0 interpretierter
Produktionswert.

Die lokale Engine wurde gegen exakte Namen-/ID-Varianten, Länder- und
Kickoff-Konflikte, Heim-/Auswärts-Reversal, Reserve-/Jugendschutz,
Ambiguität, persistente Aliase und manuelle Bestätigung geprüft. Nur
`EXACT`/`HIGH_CONFIDENCE` dürfen automatisch verlinken; unklare Kandidaten
bleiben `AMBIGUOUS` oder `UNMATCHED`.

## 4. Halbzeit- und historische Daten

Für das Browser-Beispiel Bayern München – VfB Stuttgart waren im Filter `1.`
unter anderem sichtbar:

- HT-Score `1:0`
- Ballbesitz `56:44`
- xG `1,34:0,40`
- Schüsse `9:4`
- Schüsse aufs Tor `5:0`
- Großchancen `3:1`
- Ecken `5:2`
- Gelbe Karten `0:0`

Die Werte wurden nicht aus dem Endstand oder aus Vollzeitwerten abgeleitet.
Für alle übrigen Ligen, Saisons, Live-Fälle, Frauen- und Jugendkategorien
liegt in V0.5.1 keine belastbare strukturierte Stichprobe vor. Die Details
stehen in [FOTMOB_HISTORICAL_COVERAGE.md](FOTMOB_HISTORICAL_COVERAGE.md).

Bewertung der Frage, ob HT-Daten voraussichtlich zusätzliche Information
liefern: **PARTIALLY**. Das UI zeigt für das geprüfte Spiel informative
Halbzeitwerte, aber Reichweite, Vollständigkeit und Stabilität über die
geforderten Wettbewerbe und Saisons sind nicht ausreichend validiert.

## 5. Antworten auf die sieben Abschlussfragen

1. **Technisch strukturiert nutzbar?** Teilweise: Das UI ist lesbar und der
   Parser/Adapter ist vorbereitet; ein stabiler, freigegebener strukturierter
   Vertrag ist nicht bewiesen.
2. **Bedeutung und Nutzung akzeptabel?** Die Daten sind fachlich relevant,
   systematische oder regelmäßige Automation ist aber unklar und deshalb
   nicht produktiv freigegeben.
3. **Matching zuverlässig?** Für deterministische Einzelprüfung mit Team,
   Land und Zeit sind Schutzregeln vorhanden; reale Serien-Coverage wurde
   nicht freigegeben.
4. **Live-HT-Stats ausreichend?** **NO / nicht belastbar validiert**. Der
   geprüfte Fall war historisch, nicht ein stabiler Live-Serienlauf.
5. **Historische HT-Stats und Zeitraum?** **PARTIALLY**: ein sichtbares
   Bundesliga-Beispiel in Saison 2026/2027; kein belastbarer Nachweis für die
   gesamte geforderte Saison-/Ligenmatrix.
6. **Welche Ligen/Jahre produktiv nutzbar?** Keine für einen automatischen
   Produktionscollector. Manuell geprüft: Bundesliga, Deutschland,
   2026/2027, ein Match.
7. **Produktiv, eingeschränkt oder nicht nutzen?** **Eingeschränkt**:
   manuelle Recherche bzw. ausdrücklich ausgewähltes Einzelspiel, kein
   dauerhafter Worker und kein Bulk-Historical-Import.

## 6. Auswirkungen auf das Projekt

- `FOTMOB_ENABLED=false` bleibt Standard.
- Die Tipico-Sammlung, Analyse, Strategie, Paper-Trades und Settlement bleiben
  vollständig FotMob-unabhängig.
- Der FotMob-Tab darf eine explizit eingegebene Match-ID lesen, wenn die
  lokale Nutzung und die aktuellen Bedingungen das zulassen.
- `scripts/run_fotmob.py` beendet sich bei dieser Entscheidung ohne
  periodische Requests.
- Es wird kein weiterer FotMob-Discovery-Milestone aus V0.5.1 abgeleitet.
- Es gibt keine Profit-, Signal- oder Wettstrategie-Aussage aus diesen Daten.
