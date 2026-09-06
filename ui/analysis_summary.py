"""Presentation model: source-backed scenarios, freshness and honest market labels."""
from datetime import datetime, timezone
import math

from intelligence.strategy import calculate_zero_or_2plus
from ui.time_format import parse_datetime


def analysis_summary(details, analysis, *, stake: float, max_age: float, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    probability = analysis.probability
    def valid_quote(value):
        return value if value is not None and math.isfinite(value) and value > 1 else None

    # A display-only stake change must not fetch, persist, or redate quotes.
    p1 = probability.p1
    strategy = calculate_zero_or_2plus(valid_quote(analysis.strategy.q_zero), valid_quote(analysis.strategy.q_two_plus),
                                       total_stake=stake, p1_tipico=p1 if probability.status == "OK" and p1 is not None and math.isfinite(p1) else None)
    selected = [market.best_odds.selected if market.best_odds else None
                for market in (analysis.zero_equivalence, analysis.two_plus_equivalence)]
    ages = []
    for value in [analysis.observed_at, *[quote.observed_at for quote in selected if quote]]:
        try:
            moment = parse_datetime(value)
            ages.append((now - moment).total_seconds() if moment else None)
        except (ValueError, TypeError):
            ages.append(None)
    age_valid = all(age is not None and age >= 0 for age in ages)
    age = max(ages) if age_valid else None
    stale = age is None or age > max_age
    quotes_ok = all(quote is not None and quote.is_open and quote.odds is not None
                    and math.isfinite(quote.odds) and quote.odds > 1 for quote in selected)
    scope_ok = details.event.extra_time is False and details.event.penalties is False
    halftime = details.event.period.upper() in {"HT", "HALF_TIME", "HALFTIME"} or details.event.display_minute.upper() == "HZ"
    distribution = [probability.p0, probability.p1, probability.p2_plus]
    probability_ok = probability.status == "OK" and all(
        value is not None and math.isfinite(value) and 0 <= value <= 1 for value in distribution
    ) and abs(sum(distribution) - 1) < .001
    ev = None
    if probability_ok and strategy.payout_zero is not None and strategy.payout_two_plus is not None:
        ev = probability.p0 * strategy.payout_zero + probability.p2_plus * strategy.payout_two_plus - stake
    if not scope_ok:
        tone, title, description = "warning", "Spielzeit nicht eindeutig", "Verlängerung oder Elfmeterschießen sind nicht sicher ausgeschlossen. Kein verlässlicher HZ2-Einstieg."
    elif not quotes_ok:
        tone, title, description = "warning", "Zielquoten fehlen oder sind gesperrt", "Für das Szenario werden zwei offene, passende Quoten benötigt."
    elif stale:
        tone, title, description = "warning", "Quoten aktualisieren", "Die Werte unten sind ein gespeichertes Szenario, kein aktuelles Einstiegssignal."
    elif strategy.status != "OK":
        tone, title, description = "negative", "Kein positiver Gewinnfall", "Die beiden Quoten decken den Gesamteinsatz im Gewinnfall nicht mit positivem Puffer ab."
    elif not probability_ok:
        tone, title, description = "neutral", "Nur Quotenstruktur berechenbar", "Die Gewinnfälle sind berechenbar. Für einen Marktvergleich fehlen belastbare Über-/Unter-Quoten."
    elif strategy.p1_buffer is not None and strategy.p1_buffer > 0:
        tone, title, description = "positive", "Positiver Puffer im Marktmodell", "Die Markt-Schätzung liegt unter der Verlustschwelle. Das ist noch kein unabhängig bestätigter Vorteil."
    else:
        tone, title, description = "negative", "Kein Vorteil laut Marktmodell", "Genau ein Tor wird vom Markt häufiger erwartet, als die Quoten zum Ausgleich erlauben."
    return {"strategy": strategy, "age": age, "stale": stale, "quotes_ok": quotes_ok,
            "probability_ok": probability_ok, "halftime": halftime, "ev": ev,
            "ev_roi": ev / stake if ev is not None and stake > 0 else None,
            "tone": tone, "title": title, "description": description}
