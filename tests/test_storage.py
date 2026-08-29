from __future__ import annotations

import json
from pathlib import Path

from models.market import EventDetails
from storage.database import Database
from storage.raw_storage import RawStorage
from storage.repositories import MarketRepository
from tipico.parser import parse_event_details


FIXTURES = Path(__file__).parent / "fixtures"


def load_details(name: str = "event_detail.json") -> EventDetails:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return parse_event_details(payload)


def test_odds_history_deduplicates_identical_values_and_records_changes(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "tipico.db")
    repository = MarketRepository(database)
    details = load_details()
    first_observed = "2026-08-29T10:00:00+00:00"
    second_observed = "2026-08-29T10:00:05+00:00"

    first_changes = repository.save_details(details, first_observed)
    assert first_changes == details.outcome_count
    assert repository.save_details(details, second_observed) == 0
    assert database.count_rows("event_states") == 1

    changed = details.markets[0].outcomes[0]
    changed.quote_float_value = 12.0
    changed.odds = 12.0
    changed.quote_raw = "12.00"
    assert repository.save_details(details, "2026-08-29T10:00:10+00:00") == 1

    paused = details.markets[0].outcomes[1]
    paused.status = "paused"
    paused.is_available = False
    paused.odds = None
    assert repository.save_details(details, "2026-08-29T10:00:15+00:00") == 1

    assert database.count_rows("odds_history") == details.outcome_count + 2
    database.close()


def test_raw_storage_writes_only_changed_payloads(tmp_path: Path) -> None:
    raw = RawStorage(tmp_path / "raw")
    timestamp = "2026-08-29T10:00:00+00:00"

    first = raw.store("events", "721621110", {"value": 1}, observed_at=timestamp)
    duplicate = raw.store("events", "721621110", {"value": 1}, observed_at=timestamp)
    changed = raw.store("events", "721621110", {"value": 2}, observed_at=timestamp)

    assert first.changed is True
    assert first.path is not None and first.path.exists()
    assert duplicate.changed is False
    assert duplicate.path is None
    resolved = raw.path_for_hash(
        "events",
        "721621110",
        duplicate.content_hash,
        observed_at=timestamp,
    )
    assert resolved == first.path
    assert changed.changed is True
    assert len(list((tmp_path / "raw").rglob("*.json"))) == 2
