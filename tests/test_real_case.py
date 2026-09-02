from pathlib import Path

import pandas as pd

from quant_onboarding.real_case import (
    _evaluate_strategy,
    _frame_fingerprint,
    _freeze_reproduction_state,
    _month_end_trading_days,
    _next_trading_day,
    _validate_research_clock,
)


def test_month_end_calendar_and_frame_fingerprint_are_deterministic():
    days = ["20250102", "20250131", "20250203", "20250228"]
    assert _month_end_trading_days(days, "20250101", "20250228") == [
        "20250131",
        "20250228",
    ]
    frame = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20250131"], "close": [10.0]})
    assert _frame_fingerprint(frame) == _frame_fingerprint(frame.copy())


def test_next_trading_day_finds_first_strictly_after():
    days = ["20250102", "20250131", "20250203"]
    assert _next_trading_day("20250101", days) == "20250102"
    assert _next_trading_day("20250102", days) == "20250131"
    assert _next_trading_day("20250203", days) is None
    assert _next_trading_day("20250101", []) is None


def test_validate_research_clock_detects_violations():
    valid = pd.DataFrame({
        "usable_from": ["2025-01-31T17:30:00+08:00"],
        "signal_at": ["2025-01-31T18:00:00+08:00"],
        "execution_at": ["2025-02-03T09:30:00+08:00"],
    })
    assert _validate_research_clock([valid])
    invalid_signal = valid.copy()
    invalid_signal["execution_at"] = invalid_signal["signal_at"]
    assert not _validate_research_clock([invalid_signal])
    assert not _validate_research_clock([])
    # Missing columns
    assert not _validate_research_clock([pd.DataFrame({"x": [1]})])


def test_evaluate_strategy_current_constituents_changes_result():
    """E2 survivorship bias: filtering to last-period survivors alters returns."""
    dates = pd.date_range("2020-01-31", periods=6, freq="ME")
    rows = []
    for i, d in enumerate(dates):
        for sym in ["A", "B", "C"]:
            # Symbol C drops out after period 3 (simulates delisted stock)
            if sym == "C" and i > 3:
                continue
            # C has the highest composite score when present
            score = {"A": 0.0, "B": 1.0, "C": 3.0}[sym]
            rows.append({
                "trade_date": d,
                "ts_code": sym,
                "composite": score,
                "forward_return": 0.02 if sym == "C" else 0.0,
                "value_z": 0.0,
                "momentum_z": 0.0,
                "low_volatility_z": 0.0,
            })
    cs = pd.DataFrame(rows)
    cs["trade_date"] = pd.to_datetime(cs["trade_date"])

    ret_correct, _, _, _ = _evaluate_strategy(cs, "composite", 20.0)
    ret_survivor, _, _, _ = _evaluate_strategy(
        cs, "composite", 20.0, current_constituents=True
    )
    # The two series should differ because survivorship removes the high-scoring C
    assert not ret_correct.equals(ret_survivor)
    # Early periods: correct picks C (high return), survivorship picks B (lower return)
    assert ret_correct.iloc[0] > ret_survivor.iloc[0]


def test_identical_freeze_key_counts_as_reproduction_not_new_decision(tmp_path: Path):
    root = tmp_path / "repo"
    package = root / "src" / "quant_onboarding"
    package.mkdir(parents=True)
    (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    frame = pd.DataFrame({"value": [1, 2]})
    first_key, first_reproducible, first = _freeze_reproduction_state(
        root=root,
        cache_root=tmp_path / "cache",
        benchmark_code="H00300.CSI",
        frames=(frame,),
    )
    second_key, second_reproducible, second = _freeze_reproduction_state(
        root=root,
        cache_root=tmp_path / "cache",
        benchmark_code="H00300.CSI",
        frames=(frame,),
    )
    assert first_key == second_key
    assert not first_reproducible and second_reproducible
    assert first["research_decision_count"] == second["research_decision_count"] == 1
    assert second["reproduction_run_count"] == 1
