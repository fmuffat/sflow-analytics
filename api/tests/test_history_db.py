"""Long-term history: rollup of interface counters and period comparisons."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app import history

from .conftest import EXP_A, T0, exp_id

IF48 = f"{exp_id(EXP_A)}/48"
IN_BYTES = 29 * 20 * 1_000_000_000 // 8   # 1 Gb/s, 29 valid polls (the reset poll is ignored)
OUT_BYTES = 3 * 20 * 5_000_000_000 // 8   # 5 Gb/s during 3 polls


@pytest.fixture(scope="module")
def rolled(testdb):
    r = history.rollup(testdb, now=T0 + timedelta(hours=2))
    assert r["complete"]
    return testdb


def test_rollup_hourly_volumes(rolled):
    rows = rolled.query(f"SELECT * FROM {rolled.name}.interface_hourly FINAL WHERE ifindex = 48")
    assert len(rows) == 1
    r = rows[0]
    assert r["exporter_id"] == exp_id(EXP_A)
    assert r["in_bytes"] == IN_BYTES and r["out_bytes"] == OUT_BYTES
    assert r["polls"] == 29 and r["covered_seconds"] == 580
    assert r["in_max_bps"] == pytest.approx(1e9) and r["out_max_bps"] == pytest.approx(5e9)
    assert r["out_discards"] == 3 and r["in_discards"] == 0


def test_rollup_is_idempotent(rolled):
    history.rollup(rolled, now=T0 + timedelta(hours=2))
    assert rolled.scalar(f"SELECT count() FROM {rolled.name}.interface_hourly FINAL WHERE ifindex = 48") == 1
    assert rolled.scalar(f"SELECT sum(in_bytes) FROM {rolled.name}.interface_hourly FINAL") == IN_BYTES


def test_period_bounds():
    assert history.period_dates("week", date(2026, 10, 1)) == (date(2026, 9, 28), date(2026, 10, 5))
    assert history.period_dates("month", date(2026, 12, 15)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert history.reference_dates("month", "previous", date(2026, 1, 1)) == (date(2025, 12, 1), date(2026, 1, 1))
    assert history.reference_dates("day", "week", date(2026, 10, 1)) == (date(2026, 9, 24), date(2026, 9, 25))
    # a year before keeps the weekday for days and weeks (52 weeks)
    s, _ = history.reference_dates("day", "year", date(2026, 10, 1))
    assert s.weekday() == date(2026, 10, 1).weekday() and s.year == 2025
    assert history.reference_dates("year", "previous", date(2026, 1, 1)) == (date(2025, 1, 1), date(2026, 1, 1))
    with pytest.raises(history.HistoryError):
        history.reference_dates("month", "week", date(2026, 1, 1))


def test_compare_day(client, rolled):
    r = client.get("/api/v1/history/compare", params={"period": "day", "at": "2026-01-01", "tz": "UTC",
                                                      "interface": IF48})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["points"]) == 24 and d["bucket"] == "hour"
    p10 = d["points"][10]
    assert p10["label"] == "10:00" and p10["current"]["in_bytes"] == IN_BYTES
    assert p10["current"]["in_avg_bps"] == pytest.approx(1e9)
    assert p10["current"]["in_peak_pct"] == pytest.approx(10)
    assert p10["reference"] is None
    assert d["current"]["totals"]["in_bytes"] == IN_BYTES
    assert d["reference"]["label"].startswith("Wed 2025-12-31")
    assert d["delta_pct"]["in_bytes"] is None  # nothing to compare with
    assert d["interfaces"][0]["label"]


def test_compare_time_zone_shifts_buckets(client, rolled):
    d = client.get("/api/v1/history/compare", params={"period": "day", "at": "2026-01-01", "tz": "Europe/Paris",
                                                      "interface": IF48}).json()
    assert d["points"][11]["current"]["in_bytes"] == IN_BYTES  # 10:00 UTC = 11:00 in Paris (winter)


def test_compare_month_and_year(client, rolled):
    m = client.get("/api/v1/history/compare", params={"period": "month", "at": "2026-01-15", "tz": "UTC",
                                                      "interface": IF48}).json()
    assert len(m["points"]) == 31 and m["points"][0]["current"]["in_bytes"] == IN_BYTES
    y = client.get("/api/v1/history/compare", params={"period": "year", "at": "2026-06-01", "tz": "UTC",
                                                      "interface": IF48, "compare": "year"}).json()
    assert len(y["points"]) == 12 and y["points"][0]["label"] == "Jan"
    assert y["points"][0]["current"]["out_bytes"] == OUT_BYTES
    assert y["reference"]["from"].startswith("2025-01-01")


def test_compare_running_period_uses_same_elapsed_time(rolled):
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    d = history.compare(rolled, "day", None, "UTC", "previous", [IF48], None, now=now)
    assert d["current"]["running"] and d["delta_basis"] == "same elapsed time"
    assert d["reference"]["totals_same_elapsed"]["in_bytes"] == IN_BYTES  # 10:00 < 12:00
    assert d["delta_pct"]["in_bytes"] == -100.0
    early = history.compare(rolled, "day", None, "UTC", "previous", [IF48], None,
                            now=datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc))
    assert early["reference"]["totals_same_elapsed"]["in_bytes"] == 0


def test_interfaces_table(client, rolled):
    d = client.get("/api/v1/history/interfaces", params={"period": "day", "at": "2026-01-02", "tz": "UTC"}).json()
    item = next(i for i in d["items"] if i["id"] == IF48)
    assert item["current"]["total_bytes"] == 0
    assert item["reference"]["total_bytes"] == IN_BYTES + OUT_BYTES
    assert item["delta_pct"] == -100.0
    assert d["coverage"]["rows"] >= 1


def test_compare_validation(client, rolled):
    base = {"period": "day", "interface": IF48}
    assert client.get("/api/v1/history/compare", params={**base, "tz": "Mars/Olympus"}).status_code == 422
    assert client.get("/api/v1/history/compare", params={**base, "tz": "'; DROP"}).status_code == 422
    assert client.get("/api/v1/history/compare", params={"period": "day"}).status_code == 422
    assert client.get("/api/v1/history/compare", params={**base, "interface": "x"}).status_code == 422
    assert client.get("/api/v1/history/compare", params={**base, "if_group": "nope"}).status_code == 422
    assert client.get("/api/v1/history/compare", params={"period": "month", "compare": "week",
                                                         "interface": IF48}).status_code == 422
