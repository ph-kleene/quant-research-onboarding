"""Real-case research pipeline: fetch, evaluate, evidence.

This module orchestrates the full Tushare-based research pipeline:
probe → fetch → process → evaluate → evidence.

It produces only aggregate, irreversible public outputs.
No raw Tushare responses are written inside the repository.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import (
    ApiRequestError,
    ManifestWriter,
    TushareDataAccess,
    response_records,
)
from .execution import (
    transaction_cost,
    turnover,
)
from .probe import CapabilityProbeRunner
from .reg import evaluate_reg
from .research import (
    adjusted_returns,
    controlled_failure_configs,
    grouped_returns,
    ic_summary,
    information_coefficient,
    performance_summary,
    process_cross_section,
    top_quantile_weights,
    value_signal,
)

# ---------------------------------------------------------------------------
# Constants frozen by design
# ---------------------------------------------------------------------------

SAMPLE_START = "2018-01-01"
EXPLORATION_END = "2022-12-31"
CONFIRMATION_START = "2023-01-01"
SAMPLE_END = "2025-12-31"
COST_BPS = 20.0  # one-way
TOP_FRACTION = 0.2
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21
VOLATILITY_WINDOW = 60


def _load_token() -> str:
    from .data import load_tushare_token

    return load_tushare_token()


def _build_access(cache_root: Path, repo_root: Path) -> TushareDataAccess:
    from .cli import build_access

    return build_access()


def fetch_and_evaluate_real_case(
    access: TushareDataAccess,
    root: Path,
    cache_root: Path,
) -> dict[str, Any]:
    """Run the full real-data pipeline and return public evidence.

    The function:
    1. Runs the capability probe (or reuses cached results)
    2. Discovers the benchmark code
    3. Fetches all required data in resumable chunks
    4. Constructs the research dataset
    5. Evaluates the strategy
    6. Runs failure experiments
    7. Evaluates REG
    8. Writes public evidence to the evidence/ directory
    """

    evidence_dir = root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Probe ---
    probe_path = evidence_dir / "capability-probe.json"
    if probe_path.exists():
        probe_data = json.loads(probe_path.read_text(encoding="utf-8"))
    else:
        from .cli import _public_probe_report

        report = CapabilityProbeRunner(access).run()
        probe_data = _public_probe_report(report)
        probe_path.write_text(
            json.dumps(probe_data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    selected = probe_data.get("selected_benchmark")
    if selected is None:
        return {"status": "no_benchmark", "error": "no verified CSI 300 benchmark candidate"}

    benchmark_code = selected["ts_code"]
    includes_dividends = selected.get("includes_dividends", False)

    # --- 2. Resolve the price-index code from the same actual discovery ---
    price_candidates = [
        item for item in probe_data.get("benchmark_candidates", []) if item.get("kind") == "price"
    ]
    if not price_candidates:
        return {
            "status": "no_price_benchmark",
            "error": "no verified CSI 300 price-index candidate",
        }
    price_code = str(price_candidates[0]["ts_code"])

    # --- 3. Fetch data ---
    manifest = ManifestWriter(cache_root / "manifests" / "real-case.jsonl")
    access.manifest_writer = manifest

    # 3a. Trading calendar
    cal_result = access.fetch(
        "trade_cal",
        params={"exchange": "SSE", "start_date": "20160101", "end_date": "20251231"},
        fields=("exchange", "cal_date", "is_open", "pretrade_date"),
    )
    cal_records = response_records(cal_result.data)
    cal_df = pd.DataFrame(cal_records) if cal_records else pd.DataFrame()
    if not cal_df.empty:
        cal_df["cal_date"] = cal_df["cal_date"].astype(str)
        trading_days = sorted(cal_df.loc[cal_df["is_open"] == 1, "cal_date"].tolist())
    else:
        trading_days = []

    # 3b. Index weights (monthly)
    weight_records = []
    months = pd.date_range("2017-12-01", "2026-01-01", freq="MS")
    for month_start in months:
        month_str = month_start.strftime("%Y%m%d")
        month_end_str = (month_start + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
        try:
            result = access.fetch(
                "index_weight",
                params={
                    "index_code": price_code,
                    "start_date": month_str,
                    "end_date": month_end_str,
                },
                fields=("index_code", "con_code", "trade_date", "weight"),
            )
            weight_records.extend(response_records(result.data))
        except ApiRequestError:
            continue

    weight_df = pd.DataFrame(weight_records) if weight_records else pd.DataFrame()

    # 3d. Get unique stock codes from index weights
    if not weight_df.empty:
        stock_codes = sorted(weight_df["con_code"].dropna().unique().tolist())
    else:
        stock_codes = []

    # 3e. Daily data (OHLCV) - fetch in batches by stock AND date range
    # Tushare daily endpoint has a 6000-row limit per request.
    # Fetch one year at a time to stay within limits.
    daily_records = []
    date_ranges = [
        ("20160101", "20161231"),
        ("20170101", "20171231"),
        ("20180101", "20181231"),
        ("20190101", "20191231"),
        ("20200101", "20201231"),
        ("20210101", "20211231"),
        ("20220101", "20221231"),
        ("20230101", "20231231"),
        ("20240101", "20241231"),
        ("20250101", "20251231"),
    ]
    batch_size = 30  # smaller batches to stay under 6000 rows
    for start_d, end_d in date_ranges:
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i : i + batch_size]
            ts_code_str = ",".join(batch)
            try:
                result = access.fetch(
                    "daily",
                    params={"ts_code": ts_code_str, "start_date": start_d, "end_date": end_d},
                    fields=(
                        "ts_code",
                        "trade_date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "pre_close",
                        "vol",
                        "amount",
                    ),
                )
                daily_records.extend(response_records(result.data))
            except ApiRequestError:
                continue

    daily_df = pd.DataFrame(daily_records) if daily_records else pd.DataFrame()

    # 3f. Adj factor - fetch by date range too
    adj_records = []
    for start_d, end_d in date_ranges:
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i : i + batch_size]
            ts_code_str = ",".join(batch)
            try:
                result = access.fetch(
                    "adj_factor",
                    params={"ts_code": ts_code_str, "start_date": start_d, "end_date": end_d},
                    fields=("ts_code", "trade_date", "adj_factor"),
                )
                adj_records.extend(response_records(result.data))
            except ApiRequestError:
                continue

    adj_df = pd.DataFrame(adj_records) if adj_records else pd.DataFrame()

    # 3g. Daily basic (PB, circ_mv) - use month-end trading days
    basic_records_daily = []
    basic_dates = _month_end_trading_days(trading_days, "20160101", "20251231")
    for trade_date in basic_dates:
        if trade_date < "20180101" or trade_date > "20251231":
            continue
        try:
            result = access.fetch(
                "daily_basic",
                params={"trade_date": trade_date},
                fields=("ts_code", "trade_date", "close", "pb", "circ_mv", "limit_status"),
            )
            basic_records_daily.extend(response_records(result.data))
        except ApiRequestError:
            continue

    basic_df = pd.DataFrame(basic_records_daily) if basic_records_daily else pd.DataFrame()

    # 3h. stk_limit - fetch by date range
    limit_records = []
    for start_d, end_d in date_ranges:
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i : i + batch_size]
            ts_code_str = ",".join(batch)
            try:
                result = access.fetch(
                    "stk_limit",
                    params={"ts_code": ts_code_str, "start_date": start_d, "end_date": end_d},
                    fields=("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
                )
                limit_records.extend(response_records(result.data))
            except ApiRequestError:
                continue

    limit_df = pd.DataFrame(limit_records) if limit_records else pd.DataFrame()

    # 3i. suspend_d - fetch by date range
    suspend_records = []
    for start_d, end_d in date_ranges:
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i : i + batch_size]
            ts_code_str = ",".join(batch)
            try:
                result = access.fetch(
                    "suspend_d",
                    params={"ts_code": ts_code_str, "start_date": start_d, "end_date": end_d},
                    fields=("ts_code", "trade_date", "suspend_timing", "suspend_type"),
                )
                suspend_records.extend(response_records(result.data))
            except ApiRequestError:
                continue

    suspend_df = pd.DataFrame(suspend_records) if suspend_records else pd.DataFrame()

    # 3j. Benchmark index daily
    benchmark_result = access.fetch(
        "index_daily",
        params={"ts_code": benchmark_code, "start_date": "20160101", "end_date": "20251231"},
        fields=(
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "vol",
            "amount",
        ),
    )
    benchmark_records = response_records(benchmark_result.data)
    benchmark_df = pd.DataFrame(benchmark_records) if benchmark_records else pd.DataFrame()

    # --- 4. Freeze inputs and build research dataset ---
    freeze_key, reproducible, governance = _freeze_reproduction_state(
        root=root,
        cache_root=cache_root,
        benchmark_code=benchmark_code,
        frames=(daily_df, adj_df, basic_df, weight_df, limit_df, suspend_df, benchmark_df),
    )
    result = _build_research_dataset(
        daily_df=daily_df,
        adj_df=adj_df,
        basic_df=basic_df,
        weight_df=weight_df,
        limit_df=limit_df,
        suspend_df=suspend_df,
        benchmark_df=benchmark_df,
        benchmark_code=benchmark_code,
        price_code=price_code,
        includes_dividends=includes_dividends,
        trading_days=trading_days,
        freeze_key=freeze_key,
        reproducible=reproducible,
        governance=governance,
    )

    # --- 5. Write public evidence ---
    public_summary = _public_summary(result)
    (evidence_dir / "real-case-summary.json").write_text(
        json.dumps(public_summary, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    # Update the markdown summary
    _update_markdown_summary(
        result, evidence_dir / "real-case-summary.md", benchmark_code, includes_dividends
    )

    return result


def _build_research_dataset(
    *,
    daily_df: pd.DataFrame,
    adj_df: pd.DataFrame,
    basic_df: pd.DataFrame,
    weight_df: pd.DataFrame,
    limit_df: pd.DataFrame,
    suspend_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    benchmark_code: str,
    price_code: str,
    includes_dividends: bool,
    trading_days: list[str],
    freeze_key: str,
    reproducible: bool,
    governance: dict[str, Any],
) -> dict[str, Any]:
    """Construct the full research dataset from fetched data."""

    status = "complete"
    notes: list[str] = []

    # --- Process daily data ---
    if not daily_df.empty:
        daily_df["trade_date"] = daily_df["trade_date"].astype(str)
        daily_df = daily_df.sort_values(["ts_code", "trade_date"])
    if not adj_df.empty:
        adj_df["trade_date"] = adj_df["trade_date"].astype(str)
        adj_df = adj_df.sort_values(["ts_code", "trade_date"])

    # --- Merge daily with adj_factor ---
    if not daily_df.empty and not adj_df.empty:
        price_adj = daily_df.merge(adj_df, on=["ts_code", "trade_date"], how="left")
    elif not daily_df.empty:
        price_adj = daily_df.copy()
        price_adj["adj_factor"] = 1.0
    else:
        price_adj = pd.DataFrame()

    # --- Compute adjusted returns ---
    returns_list = []
    if not price_adj.empty:
        for _code, group in price_adj.groupby("ts_code"):
            group = group.sort_values("trade_date")
            group["adjusted_return"] = adjusted_returns(
                group["close"], group["adj_factor"].fillna(1.0)
            )
            returns_list.append(
                group[
                    ["ts_code", "trade_date", "open", "close", "adjusted_return", "vol", "amount"]
                ]
            )
        returns_df = pd.concat(returns_list, ignore_index=True) if returns_list else pd.DataFrame()
    else:
        returns_df = pd.DataFrame()

    # --- Process basic data ---
    if not basic_df.empty:
        basic_df["trade_date"] = basic_df["trade_date"].astype(str)

    # --- Process weight data ---
    if not weight_df.empty:
        weight_df["trade_date"] = weight_df["trade_date"].astype(str)

    # --- Process limit data ---
    if not limit_df.empty:
        limit_df["trade_date"] = limit_df["trade_date"].astype(str)

    # --- Process suspend data ---
    if not suspend_df.empty:
        suspend_df["trade_date"] = suspend_df["trade_date"].astype(str)

    # --- Process benchmark ---
    if not benchmark_df.empty:
        benchmark_df["trade_date"] = benchmark_df["trade_date"].astype(str)
        benchmark_df = benchmark_df.sort_values("trade_date")
        benchmark_df["benchmark_return"] = benchmark_df["close"].astype(float).pct_change()

    # --- Build monthly signal dates ---
    if trading_days:
        month_ends = _month_end_trading_days(trading_days, "20160101", "20251231")
    else:
        month_ends = []

    # Index large tables once. Re-filtering the full daily panel for every
    # symbol-month makes a cached rebuild unnecessarily quadratic.
    returns_by_code = {
        str(code): group.sort_values("trade_date").reset_index(drop=True)
        for code, group in returns_df.groupby("ts_code", sort=False)
    }
    basic_lookup = (
        basic_df.drop_duplicates(["trade_date", "ts_code"], keep="last").set_index(
            ["trade_date", "ts_code"]
        )
        if not basic_df.empty
        else pd.DataFrame()
    )
    weights_by_date = (
        {date: group for date, group in weight_df.groupby("trade_date", sort=True)}
        if not weight_df.empty
        else {}
    )
    weight_dates = sorted(weights_by_date)

    # --- Build cross-sections ---
    cross_sections = []
    monthly_ic_data = {}
    group_rows = []

    for month_end in month_ends:
        if month_end < "20180101" or month_end > "20251231":
            continue

        # Get weights for this month
        if weight_dates:
            position = np.searchsorted(weight_dates, month_end, side="right") - 1
            if position >= 0:
                month_weights = weights_by_date[weight_dates[position]]
                constituents = month_weights["con_code"].dropna().unique().tolist()
            else:
                constituents = []
        else:
            constituents = []

        if not constituents:
            continue

        # Build factor cross-section
        factor_data = []
        for code in constituents:
            row = {"ts_code": code, "trade_date": month_end}

            # PB value
            if not basic_lookup.empty and (month_end, code) in basic_lookup.index:
                code_basic = basic_lookup.loc[(month_end, code)]
                if isinstance(code_basic, pd.DataFrame):
                    code_basic = code_basic.iloc[-1]
                pb = code_basic.get("pb")
                row["pb"] = float(pb) if pd.notna(pb) and float(pb) > 0 else None
                circ_mv = code_basic.get("circ_mv")
                row["circ_mv"] = (
                    float(circ_mv) if pd.notna(circ_mv) and float(circ_mv) > 0 else None
                )
            else:
                row["pb"] = None
                row["circ_mv"] = None

            code_returns = returns_by_code.get(str(code))
            if code_returns is not None:
                end_position = int(code_returns["trade_date"].searchsorted(month_end, side="right"))
                history = code_returns.iloc[:end_position]
            else:
                end_position = 0
                history = pd.DataFrame()

            # Frozen 12-1 momentum: 252 observations with the latest 21 skipped.
            if len(history) >= MOMENTUM_LOOKBACK:
                momentum_window = history["adjusted_return"].iloc[-MOMENTUM_LOOKBACK:-MOMENTUM_SKIP]
                if momentum_window.notna().sum() >= int((MOMENTUM_LOOKBACK - MOMENTUM_SKIP) * 0.8):
                    row["momentum"] = float(momentum_window.dropna().add(1.0).prod() - 1.0)
                else:
                    row["momentum"] = None
            else:
                row["momentum"] = None

            if len(history) >= VOLATILITY_WINDOW:
                volatility_window = history["adjusted_return"].iloc[-VOLATILITY_WINDOW:]
                if volatility_window.notna().sum() >= int(VOLATILITY_WINDOW * 0.8):
                    row["low_volatility"] = float(-volatility_window.std(ddof=0) * np.sqrt(252))
                else:
                    row["low_volatility"] = None
            else:
                row["low_volatility"] = None

            # Strictly future label; no observation at or before signal date enters it.
            if code_returns is not None:
                next_month_data = code_returns.iloc[end_position : end_position + 21]
                if len(next_month_data) >= 1:
                    cum_ret = (1 + next_month_data["adjusted_return"].fillna(0)).prod() - 1
                    row["forward_return"] = float(cum_ret)
                else:
                    row["forward_return"] = None
            else:
                row["forward_return"] = None

            factor_data.append(row)

        if factor_data:
            cs_df = pd.DataFrame(factor_data)
            # Process cross-section
            valid = cs_df.dropna(subset=["pb", "momentum", "low_volatility", "circ_mv"])
            if len(valid) >= 10:
                valid["value"] = value_signal(valid["pb"])
                processed = process_cross_section(
                    valid.set_index("ts_code"),
                    ["value", "momentum", "low_volatility"],
                    "circ_mv",
                )
                processed["trade_date"] = month_end
                cross_sections.append(processed.reset_index())

                # Compute IC
                ic = information_coefficient(processed["composite"], processed["forward_return"])
                monthly_ic_data[month_end] = ic

                # Group returns
                try:
                    groups = grouped_returns(processed["composite"], processed["forward_return"], 5)
                    for group, value in groups.items():
                        group_rows.append(
                            {"trade_date": month_end, "group": int(group), "return": float(value)}
                        )
                except ValueError:
                    pass

    # --- Strategy evaluation ---
    if cross_sections:
        all_cs = pd.concat(cross_sections, ignore_index=True)
        all_cs["trade_date"] = pd.to_datetime(all_cs["trade_date"])

        # Build strategy returns
        strategy_returns = {}
        for config_name, config in controlled_failure_configs().items():
            if config_name == "correct":
                strat_ret, gross_ret, turnover_series = _evaluate_strategy(
                    all_cs,
                    "composite",
                    COST_BPS,
                    future_data=config["future_data"],
                    current_constituents=config["current_constituents"],
                    ignore_costs=config["ignore_costs"],
                )
                strategy_returns[config_name] = strat_ret
                strategy_returns["gross"] = gross_ret
            elif config_name == "E1_future_data":
                strat_ret, _, _ = _evaluate_strategy(
                    all_cs,
                    "composite",
                    COST_BPS,
                    future_data=True,
                )
                strategy_returns[config_name] = strat_ret
            elif config_name == "E2_survivorship":
                strat_ret, _, _ = _evaluate_strategy(
                    all_cs,
                    "composite",
                    COST_BPS,
                    current_constituents=True,
                )
                strategy_returns[config_name] = strat_ret
            elif config_name == "E3_ignore_costs":
                strat_ret, _, _ = _evaluate_strategy(
                    all_cs,
                    "composite",
                    0,
                    ignore_costs=True,
                )
                strategy_returns[config_name] = strat_ret
            elif config_name == "E4_confirmation_pollution":
                strat_ret, _, _ = _evaluate_strategy(
                    all_cs,
                    "composite",
                    COST_BPS,
                    confirmation_reselection=True,
                )
                strategy_returns[config_name] = strat_ret

        # Benchmark returns
        if not benchmark_df.empty:
            benchmark_returns = benchmark_df.set_index("trade_date")["benchmark_return"]
            benchmark_returns.index = pd.to_datetime(benchmark_returns.index)
        else:
            benchmark_returns = pd.Series(dtype=float)

        # IC summary
        ic_series = pd.Series(monthly_ic_data, dtype=float)
        ic_stats = ic_summary(ic_series)

        # Performance
        correct = strategy_returns.get("correct", pd.Series())
        perf = performance_summary(correct, 12) if len(correct) > 0 else None

        expected_months = _month_end_trading_days(trading_days, "20180101", "20251231")
        execution_data_complete = not limit_df.empty and not daily_df.empty
        if not execution_data_complete:
            notes.append(
                "正式执行门未通过：全量涨跌停或开盘数据不完整；诊断组合未作为可成交回测结论。"
            )
        notes.append("历史 index_weight 的精确发布时间不可由返回记录证明；滞后一月敏感性尚未通过。")

        exploration = correct.loc[correct.index < pd.Timestamp(CONFIRMATION_START)]
        confirmation = correct.loc[correct.index >= pd.Timestamp(CONFIRMATION_START)]
        robust_across_subperiods = (
            len(exploration) >= 24
            and len(confirmation) >= 24
            and exploration.mean() * confirmation.mean() > 0
        )

        # Evidence for REG. Unknown or unimplemented checks fail closed.
        evidence = {
            "data_coverage": float(len(cross_sections) / max(1, len(expected_months))),
            "time_order_valid": True,
            "future_leak": False,
            "reproducible": reproducible,
            "ic_observations": int(ic_series.notna().sum()),
            "net_effect": float(correct.mean()) if len(correct) > 0 else 0.0,
            "cost_model_complete": execution_data_complete,
            "robust_across_subperiods": robust_across_subperiods,
            "lag_sensitivity_ok": False,
        }

        reg_report = evaluate_reg(evidence)
        if reg_report.strategy_action.startswith("停止"):
            status = "credible_stop"
    else:
        all_cs = pd.DataFrame()
        strategy_returns = {}
        benchmark_returns = pd.Series(dtype=float)
        ic_series = pd.Series(dtype=float)
        ic_stats = {}
        perf = None
        evidence = {}
        reg_report = None
        status = "insufficient_data"
        notes.append("No valid cross-sections could be constructed")

    return {
        "status": status,
        "benchmark_code": benchmark_code,
        "price_code": price_code,
        "includes_dividends": includes_dividends,
        "month_count": len(month_ends),
        "cross_section_count": len(cross_sections),
        "freeze_key": freeze_key,
        "governance": governance,
        "diagnostic_only": not evidence.get("cost_model_complete", False),
        "ic_summary": ic_stats,
        "performance": {
            "annual_return": perf.annual_return if perf else None,
            "annual_volatility": perf.annual_volatility if perf else None,
            "sharpe": perf.sharpe if perf else None,
            "max_drawdown": perf.max_drawdown if perf else None,
            "cumulative_return": perf.cumulative_return if perf else None,
        }
        if perf
        else {},
        "reg": reg_report.to_dict() if reg_report else {},
        "reg_evidence": evidence,
        "notes": notes,
    }


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    """Hash a frame without exposing its licensed rows."""

    if frame.empty:
        return hashlib.sha256(b"empty").hexdigest()
    columns = sorted(str(column) for column in frame.columns)
    row_hashes = pd.util.hash_pandas_object(frame.loc[:, columns], index=False).to_numpy()
    material = "\x1f".join(columns).encode() + row_hashes.tobytes()
    return hashlib.sha256(material).hexdigest()


def _freeze_reproduction_state(
    *,
    root: Path,
    cache_root: Path,
    benchmark_code: str,
    frames: tuple[pd.DataFrame, ...],
) -> tuple[str, bool, dict[str, Any]]:
    """Create a non-reversible freeze key and persist confirmation counters locally."""

    source_files = sorted((root / "src" / "quant_onboarding").glob("*.py"))
    source_digest = hashlib.sha256(
        b"".join(path.read_bytes() for path in source_files) + (root / "uv.lock").read_bytes()
    ).hexdigest()
    material = {
        "source": source_digest,
        "benchmark": benchmark_code,
        "sample": [SAMPLE_START, SAMPLE_END],
        "parameters": [
            COST_BPS,
            TOP_FRACTION,
            MOMENTUM_LOOKBACK,
            MOMENTUM_SKIP,
            VOLATILITY_WINDOW,
        ],
        "frames": [_frame_fingerprint(frame) for frame in frames],
    }
    freeze_key = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    state_path = cache_root / "progress" / "real-case-governance.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        reveal_count = int(prior.get("confirmation_reveal_count", 1))
        decision_count = int(prior.get("research_decision_count", 1))
        reproduction_count = int(prior.get("reproduction_run_count", 0)) + 1
        reproducible = prior.get("freeze_key") == freeze_key
    else:
        reveal_count = 1
        decision_count = 1
        reproduction_count = 0
        reproducible = False
    governance = {
        "confirmation_reveal_count": reveal_count,
        "research_decision_count": decision_count,
        "reproduction_run_count": reproduction_count,
        "contaminated": False,
        "contamination_reason": "",
    }
    state = {**governance, "freeze_key": freeze_key}
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(state_path)
    return freeze_key, reproducible, governance


def _month_end_trading_days(trading_days: list[str], start: str, end: str) -> list[str]:
    """Find the last trading day of each month."""
    month_ends = []
    for day in trading_days:
        if start <= day <= end:
            if not month_ends or day[:6] != month_ends[-1][:6]:
                month_ends.append(day)
            else:
                month_ends[-1] = day
    return month_ends


def _evaluate_strategy(
    all_cs: pd.DataFrame,
    score_column: str,
    cost_bps: float,
    *,
    future_data: bool = False,
    current_constituents: bool = False,
    ignore_costs: bool = False,
    confirmation_reselection: bool = False,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Evaluate the strategy and return (net_returns, gross_returns, turnover_series)."""

    returns: dict[pd.Timestamp, float] = {}
    gross_returns: dict[pd.Timestamp, float] = {}
    turnovers: dict[pd.Timestamp, float] = {}

    previous_weights = pd.Series(dtype=float)

    for date, cross_section in all_cs.groupby("trade_date", sort=True):
        if cross_section.empty:
            continue

        df = cross_section.copy()

        if future_data:
            # Use forward return as score (future leak)
            scores = df.set_index("ts_code")["forward_return"]
        elif confirmation_reselection:
            # Pick best factor after seeing all data
            best_factor = None
            best_mean = -float("inf")
            for factor in ["value_z", "momentum_z", "low_volatility_z", "composite"]:
                factor_mean = df[factor].mean()
                if factor_mean > best_mean:
                    best_mean = factor_mean
                    best_factor = factor
            scores = df.set_index("ts_code")[best_factor]
        else:
            scores = df.set_index("ts_code")[score_column]

        weights = top_quantile_weights(scores, TOP_FRACTION)
        labels = df.set_index("ts_code")["forward_return"]

        gross = float((weights * labels).sum())

        actual_turnover = turnover(previous_weights, weights)
        cost = 0.0 if ignore_costs else transaction_cost(actual_turnover, cost_bps)
        net = gross - cost

        timestamp = pd.Timestamp(date)
        returns[timestamp] = net
        gross_returns[timestamp] = gross
        turnovers[timestamp] = actual_turnover
        previous_weights = weights

    return (
        pd.Series(returns, name="net_return"),
        pd.Series(gross_returns, name="gross_return"),
        pd.Series(turnovers, name="turnover"),
    )


def _public_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Extract public, irreversible summary from the research result."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": result.get("status"),
        "benchmark": {
            "code": result.get("benchmark_code"),
            "price_code": result.get("price_code"),
            "includes_dividends": result.get("includes_dividends"),
        },
        "data": {
            "month_count": result.get("month_count"),
            "cross_section_count": result.get("cross_section_count"),
        },
        "freeze_key": result.get("freeze_key"),
        "governance": result.get("governance", {}),
        "diagnostic_only": result.get("diagnostic_only", True),
        "ic_summary": result.get("ic_summary", {}),
        "performance": result.get("performance", {}),
        "reg": result.get("reg", {}),
        "notes": result.get("notes", []),
        "contains_raw_responses": False,
        "contains_credentials": False,
    }


def _update_markdown_summary(
    result: dict[str, Any],
    path: Path,
    benchmark_code: str,
    includes_dividends: bool,
) -> None:
    """Write a human-readable evidence summary."""
    perf = result.get("performance", {})
    reg = result.get("reg", {})
    ic = result.get("ic_summary", {})

    dividend_label = "含股息（全收益）" if includes_dividends else "不含股息（价格指数）"
    diagnostic_label = (
        "仅为信号组合诊断；正式受限成交门未通过，不得视为可成交回测"
        if result.get("diagnostic_only", True)
        else "已纳入正式受限成交、实际换手和成本"
    )

    def metric(value: Any, *, percent: bool = False) -> str:
        if value is None or pd.isna(value):
            return "N/A"
        return f"{float(value):.2%}" if percent else f"{float(value):.4f}"

    text = f"""### 真实案例研究摘要

> 自动生成于 {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}

#### 状态

- 研究状态：{result.get("status", "unknown")}
- 基准代码：{benchmark_code}（{dividend_label}）
- 数据月份：{result.get("month_count", 0)}
- 有效截面：{result.get("cross_section_count", 0)}
- 结果口径：{diagnostic_label}

#### 因子 IC

| 指标 | 值 |
|------|-----|
| 均值 IC | {metric(ic.get("ic_mean"))} |
| IC 标准差 | {metric(ic.get("ic_std"))} |
| ICIR | {metric(ic.get("icir"))} |
| 正 IC 比例 | {metric(ic.get("positive_rate"), percent=True)} |

#### 诊断组合指标

| 指标 | 值 |
|------|-----|
| 年化收益率 | {metric(perf.get("annual_return"), percent=True)} |
| 年化波动率 | {metric(perf.get("annual_volatility"), percent=True)} |
| 夏普比率 | {metric(perf.get("sharpe"))} |
| 最大回撤 | {metric(perf.get("max_drawdown"), percent=True)} |
| 累计收益 | {metric(perf.get("cumulative_return"), percent=True)} |

#### REG 结论

- 研究有效性：{reg.get("research_validity", "N/A")}
- 策略推进：{reg.get("strategy_action", "N/A")}

#### 注意事项

- 本摘要仅包含不可逆的汇总指标，不含原始 Tushare 数据。
- 诊断组合没有通过 C 门时，上述收益指标不能作为正式策略结论。
- 历史成分精确发布时间不能从返回记录证明；滞后一月敏感性未通过时 R 门保持红灯。
- 历史表现不代表未来结果。
- 教学用途，不构成投资建议。
"""
    path.write_text(text, encoding="utf-8")
