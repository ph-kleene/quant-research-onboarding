from pathlib import Path

import pandas as pd

from quant_onboarding.real_case import (
    _frame_fingerprint,
    _freeze_reproduction_state,
    _month_end_trading_days,
)


def test_month_end_calendar_and_frame_fingerprint_are_deterministic():
    days = ["20250102", "20250131", "20250203", "20250228"]
    assert _month_end_trading_days(days, "20250101", "20250228") == [
        "20250131",
        "20250228",
    ]
    frame = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20250131"], "close": [10.0]})
    assert _frame_fingerprint(frame) == _frame_fingerprint(frame.copy())


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
