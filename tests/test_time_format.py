from ui.time_format import format_local_datetime, parse_datetime


def test_format_utc_timestamp_in_munich_time() -> None:
    value = "2026-08-29T15:12:14+00:00"

    assert format_local_datetime(value) == "29.08.2026 17:12:14"
    assert format_local_datetime(value, include_seconds=False) == "29.08.2026 17:12"


def test_naive_timestamp_is_treated_as_utc_and_invalid_value_is_retained() -> None:
    parsed = parse_datetime("2026-08-29T15:12:14")

    assert parsed is not None
    assert parsed.hour == 17
    assert format_local_datetime("not-a-timestamp") == "not-a-timestamp"
