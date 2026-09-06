"""Small, pre-registered Tipico pattern hypotheses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class Variant:
    variant_id: str
    label: str
    description: str
    bet_form: str = "COMBINATION"
    lower: float | None = None
    upper: float | None = None
    field: str | None = None
    paper_compatible: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def applies(self, row: dict[str, Any]) -> bool:
        if not row.get("entry_eligible"):
            return False
        return self.matches(row)

    def matches(self, row: dict[str, Any]) -> bool:
        """Evaluate only this variant's immutable feature condition."""

        if self.variant_id in {"R00", "R01", "R02"}:
            return True
        value = row.get(self.field or "")
        if value is None:
            return False
        value = float(value)
        if self.lower is not None and value < self.lower:
            return False
        if self.upper is not None and value >= self.upper:
            return False
        return True


def variant_config_hash(variant: Variant) -> str:
    return hashlib.sha256(
        json.dumps(variant.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def evaluate_variant_entry(
    row: dict[str, Any],
    variant: Variant,
    *,
    execution_mode: str = "backtest",
) -> dict[str, Any]:
    """Shared deterministic decision core for replay and paper dry-runs.

    The function deliberately reads only entry-time fields.  Result/label
    fields may be present in a historical row, but they are never consulted
    for the decision.
    """

    base = {
        "variant_id": variant.variant_id,
        "variant_version": "1",
        "config_hash": variant_config_hash(variant),
        "execution_mode": execution_mode,
        "entry_policy": "HT_STABLE_ONCE",
    }
    if execution_mode == "paper" and not variant.paper_compatible:
        return {**base, "decision": "INVALID", "accepted": False, "reason": "WET_FORM_NOT_PAPER_SUPPORTED"}
    if not row.get("entry_eligible"):
        return {**base, "decision": "INVALID", "accepted": False, "reason": "ENTRY_NOT_ELIGIBLE"}
    if not variant.matches(row):
        return {**base, "decision": "NO_BET", "accepted": False, "reason": "VARIANT_FILTER_NOT_MET"}
    return {**base, "decision": "BET", "accepted": True, "reason": "VARIANT_ACCEPTED"}


def registered_variants() -> tuple[Variant, ...]:
    # The ranges are intentionally fixed in source.  They are hypotheses for
    # the first study, not quantiles learned from the same outcome data.
    return (
        Variant("R00", "Kombination 0 oder 2+", "Kein zusätzlicher Filter."),
        Variant("R01", "Nur 0 Tore", "Gesamter Einsatz auf q0.", "SINGLE_ZERO"),
        Variant("R02", "Nur 2+ Tore", "Gesamter Einsatz auf q2+.", "SINGLE_TWO_PLUS"),
        Variant("P10", "P1 < 20 %", "0 <= P1 < 0,20", lower=0.00, upper=0.20, field="p1_market", paper_compatible=True),
        Variant("P11", "P1 20–25 %", "0,20 <= P1 < 0,25", lower=0.20, upper=0.25, field="p1_market", paper_compatible=True),
        Variant("P12", "P1 25–30 %", "0,25 <= P1 < 0,30", lower=0.25, upper=0.30, field="p1_market", paper_compatible=True),
        Variant("P13", "P1 30–35 %", "0,30 <= P1 < 0,35", lower=0.30, upper=0.35, field="p1_market", paper_compatible=True),
        Variant("P14", "P1 35–40 %", "0,35 <= P1 < 0,40", lower=0.35, upper=0.40, field="p1_market", paper_compatible=True),
        Variant("P15", "P1 >= 40 %", "0,40 <= P1 <= 1", lower=0.40, upper=1.01, field="p1_market", paper_compatible=True),
        Variant("B10", "Break-even < 20 %", "0 < P1-Break-even < 0,20", lower=0.00, upper=0.20, field="p1_break_even"),
        Variant("B11", "Break-even 20–30 %", "0,20 <= P1-Break-even < 0,30", lower=0.20, upper=0.30, field="p1_break_even"),
        Variant("B12", "Break-even >= 30 %", "0,30 <= P1-Break-even < 1", lower=0.30, upper=1.00, field="p1_break_even"),
        Variant("G10", "Puffer unter −10 pp", "P1-Puffer < −0,10", upper=-0.10, field="p1_buffer"),
        Variant("G11", "Puffer −10 bis −5 pp", "−0,10 <= P1-Puffer < −0,05", lower=-0.10, upper=-0.05, field="p1_buffer"),
        Variant("G12", "Puffer ab −5 pp", "P1-Puffer >= −0,05", lower=-0.05, upper=1.00, field="p1_buffer"),
        Variant("H10", "HT-Torsumme 0", "HT-Torsumme = 0", lower=0.0, upper=1.0, field="ht_goals"),
        Variant("H11", "HT-Torsumme 1", "HT-Torsumme = 1", lower=1.0, upper=2.0, field="ht_goals"),
        Variant("H12", "HT-Torsumme >= 2", "HT-Torsumme >= 2", lower=2.0, upper=100.0, field="ht_goals"),
    )


def enrich_variant_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["ht_goals"] = (
        int(row["ht_score_home"]) + int(row["ht_score_away"])
        if row.get("ht_score_home") is not None and row.get("ht_score_away") is not None
        else None
    )
    return result
