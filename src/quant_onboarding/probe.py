"""Minimal capability probes for the nine frozen Tushare endpoints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .data import (
    ApiRequestError,
    FetchResult,
    ParameterError,
    PermissionDeniedError,
    RateLimitError,
    TransientApiError,
    response_records,
)

PROBE_START_DATE = "20251229"
PROBE_END_DATE = "20251231"


@dataclass(frozen=True)
class ProbeSpec:
    endpoint: str
    fields: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    purpose: str = ""
    expected_permission: str = "2000-point tier; verify with actual account"
    estimated_full_requests: str = "re-estimate after probe"


PROBE_SPECS: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        "index_basic",
        (
            "ts_code",
            "name",
            "fullname",
            "market",
            "publisher",
            "category",
            "base_date",
            "list_date",
        ),
        {"market": "CSI"},
        "discover actual CSI 300 price/total-return candidate codes",
        estimated_full_requests="2-3 markets",
    ),
    ProbeSpec(
        "trade_cal",
        ("exchange", "cal_date", "is_open", "pretrade_date"),
        {"exchange": "SSE", "start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "trading calendar and next valid execution date",
        estimated_full_requests="1-2 exchanges",
    ),
    ProbeSpec(
        "daily",
        ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"),
        {"ts_code": "000001.SZ", "start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "raw OHLCV and executable opening observations",
        estimated_full_requests="up to about 800 symbol ranges",
    ),
    ProbeSpec(
        "adj_factor",
        ("ts_code", "trade_date", "adj_factor"),
        {"ts_code": "000001.SZ", "start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "adjacent adjustment-factor return ratios",
        estimated_full_requests="up to about 800 symbol ranges",
    ),
    ProbeSpec(
        "daily_basic",
        ("ts_code", "trade_date", "close", "pb", "circ_mv", "limit_status"),
        {"ts_code": "000001.SZ", "start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "value factor, market value and close-status diagnostics",
        estimated_full_requests="about 96-192 month-end slices",
    ),
    ProbeSpec(
        "stk_limit",
        ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
        {"ts_code": "000001.SZ", "start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "official daily price limits for execution constraints",
        estimated_full_requests="up to about 800 symbol ranges",
    ),
    ProbeSpec(
        "suspend_d",
        ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
        {"trade_date": PROBE_END_DATE},
        "official suspension evidence",
        expected_permission="unknown until account probe",
        estimated_full_requests="16-800 depending on supported batching",
    ),
    ProbeSpec(
        "index_weight",
        ("index_code", "con_code", "trade_date", "weight"),
        {"start_date": "20251201", "end_date": PROBE_END_DATE},
        "historical constituents and contemporaneous weights",
        estimated_full_requests="about 96 monthly requests",
    ),
    ProbeSpec(
        "index_daily",
        ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"),
        {"start_date": PROBE_START_DATE, "end_date": PROBE_END_DATE},
        "aligned CSI 300 benchmark after code discovery",
        estimated_full_requests="one minimal call per candidate; 1-2 full-range calls",
    ),
)

PROBE_ENDPOINTS = tuple(spec.endpoint for spec in PROBE_SPECS)


@dataclass(frozen=True)
class BenchmarkCandidate:
    ts_code: str
    name: str
    kind: str  # total_return or price


def discover_csi300_candidates(records: Sequence[Mapping[str, Any]]) -> list[BenchmarkCandidate]:
    """Return only canonical CSI 300 price and total-return records.

    A substring search is unsafe here: the index catalogue contains hundreds of
    strategy, currency-hedged and customised indices whose names mention CSI 300.
    Candidates still have to be present in the actual ``index_basic`` response;
    no code is invented as a fallback.
    """

    candidates: list[BenchmarkCandidate] = []
    seen: set[str] = set()
    for record in records:
        code = str(record.get("ts_code", "")).strip()
        name = str(record.get("name") or "").strip()
        full_name = str(record.get("fullname") or "").strip()
        compact_name = name.lower().replace(" ", "")
        compact_full = full_name.lower().replace(" ", "")

        is_total_return = code.upper() == "H00300.CSI" and (
            compact_name in {"300收益", "沪深300全收益"}
            or compact_full in {"沪深300全收益", "沪深300全收益指数"}
        )
        is_price = code.upper() == "000300.SH" and (
            compact_name in {"沪深300", "csi300"}
            or compact_full in {"沪深300", "沪深300指数", "csi300", "csi300index"}
        )
        if not code or (not is_total_return and not is_price) or code in seen:
            continue
        candidates.append(
            BenchmarkCandidate(
                ts_code=code,
                name=name or full_name or code,
                kind="total_return" if is_total_return else "price",
            )
        )
        seen.add(code)
    return sorted(candidates, key=lambda item: (item.kind != "total_return", item.ts_code))


@dataclass(frozen=True)
class ProbeResult:
    endpoint: str
    status: str
    permission: bool | None
    api_status: str
    error_code: str | None
    requested_fields: tuple[str, ...]
    response_fields: tuple[str, ...]
    row_count: int
    min_date: str | None
    max_date: str | None
    actual_codes: tuple[str, ...]
    fit_for_purpose: bool
    estimated_full_requests: str
    purpose: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "status": self.status,
            "permission": self.permission,
            "api_status": self.api_status,
            "error_code": self.error_code,
            "requested_fields": list(self.requested_fields),
            "response_fields": list(self.response_fields),
            "row_count": self.row_count,
            "date_range": {"min": self.min_date, "max": self.max_date},
            "actual_codes": list(self.actual_codes),
            "fit_for_purpose": self.fit_for_purpose,
            "estimated_full_requests": self.estimated_full_requests,
            "purpose": self.purpose,
            "note": self.note,
        }


def _result_from_fetch(spec: ProbeSpec, fetched: FetchResult) -> ProbeResult:
    records = response_records(fetched.data)
    manifest = fetched.manifest
    response_fields = tuple(manifest.get("response_fields", ()))
    required = set(spec.fields)
    missing = sorted(required - set(response_fields)) if records else []
    if not records:
        status = "empty"
        fit = False
        note = "request succeeded but returned no rows; permission/coverage remains inconclusive"
    elif missing:
        status = "schema_mismatch"
        fit = False
        note = "missing required fields: " + ", ".join(missing)
    else:
        status = "success"
        fit = True
        note = ""
    date_range = manifest.get("date_range", {})
    return ProbeResult(
        endpoint=spec.endpoint,
        status=status,
        permission=True if records else None,
        api_status="ok",
        error_code=None,
        requested_fields=spec.fields,
        response_fields=response_fields,
        row_count=len(records),
        min_date=date_range.get("min"),
        max_date=date_range.get("max"),
        actual_codes=tuple(manifest.get("actual_codes", ())),
        fit_for_purpose=fit,
        estimated_full_requests=spec.estimated_full_requests,
        purpose=spec.purpose,
        note=note,
    )


def _result_from_error(spec: ProbeSpec, error: ApiRequestError) -> ProbeResult:
    if isinstance(error, PermissionDeniedError):
        status, permission, api_status = "permission_denied", False, "permission_error"
    elif isinstance(error, ParameterError):
        status, permission, api_status = "parameter_error", None, "request_error"
    elif isinstance(error, RateLimitError):
        status, permission, api_status = "rate_limited", None, "temporary_error"
    elif isinstance(error, TransientApiError):
        status, permission, api_status = "temporary_failure", None, "temporary_error"
    else:
        status, permission, api_status = "error", None, "api_error"
    return ProbeResult(
        endpoint=spec.endpoint,
        status=status,
        permission=permission,
        api_status=api_status,
        error_code=error.code,
        requested_fields=spec.fields,
        response_fields=(),
        row_count=0,
        min_date=None,
        max_date=None,
        actual_codes=(),
        fit_for_purpose=False,
        estimated_full_requests=spec.estimated_full_requests,
        purpose=spec.purpose,
        note="classified error; upstream message intentionally omitted",
    )


class CapabilityProbeRunner:
    """Run the frozen probes and select an actually verified benchmark code."""

    def __init__(self, data_access: Any) -> None:
        self.data_access = data_access
        self._specs = {spec.endpoint: spec for spec in PROBE_SPECS}

    def probe(self, spec: ProbeSpec) -> tuple[ProbeResult, FetchResult | None]:
        try:
            fetched = self.data_access.fetch(
                spec.endpoint,
                params=spec.params,
                fields=spec.fields,
            )
        except ApiRequestError as exc:
            return _result_from_error(spec, exc), None
        return _result_from_fetch(spec, fetched), fetched

    @staticmethod
    def _skipped(spec: ProbeSpec, note: str) -> ProbeResult:
        return ProbeResult(
            endpoint=spec.endpoint,
            status="skipped",
            permission=None,
            api_status="not_called",
            error_code=None,
            requested_fields=spec.fields,
            response_fields=(),
            row_count=0,
            min_date=None,
            max_date=None,
            actual_codes=(),
            fit_for_purpose=False,
            estimated_full_requests=spec.estimated_full_requests,
            purpose=spec.purpose,
            note=note,
        )

    def run(self) -> dict[str, Any]:
        """Return nine endpoint results plus benchmark discovery evidence.

        ``index_basic`` is always first.  ``index_weight`` uses an actually
        returned CSI 300 price code.  ``index_daily`` tries returned total-return
        candidates first, then returned price candidates.  No bare H00300 code
        is ever synthesized.
        """

        results: dict[str, ProbeResult] = {}
        basic_spec = self._specs["index_basic"]
        basic_fetches: list[FetchResult] = []
        basic_results: list[ProbeResult] = []
        for market in ("CSI", "SSE"):
            market_spec = replace(basic_spec, params={"market": market})
            market_result, market_fetch = self.probe(market_spec)
            basic_results.append(market_result)
            if market_fetch is not None:
                basic_fetches.append(market_fetch)
        successful_basic = [item for item in basic_results if item.fit_for_purpose]
        if successful_basic:
            basic_result = replace(
                successful_basic[0],
                row_count=sum(item.row_count for item in successful_basic),
                actual_codes=tuple(
                    sorted({code for item in successful_basic for code in item.actual_codes})
                ),
                note="searched actual index_basic catalogues for CSI and SSE markets",
            )
        else:
            basic_result = basic_results[0]
        results["index_basic"] = basic_result
        candidates = discover_csi300_candidates(
            [record for fetched in basic_fetches for record in response_records(fetched.data)]
        )
        for endpoint in (
            "trade_cal",
            "daily",
            "adj_factor",
            "daily_basic",
            "stk_limit",
            "suspend_d",
        ):
            result, _ = self.probe(self._specs[endpoint])
            results[endpoint] = result

        price_candidates = [item for item in candidates if item.kind == "price"]
        weight_spec = self._specs["index_weight"]
        if price_candidates:
            weight_spec = replace(
                weight_spec,
                params={**weight_spec.params, "index_code": price_candidates[0].ts_code},
            )
            results["index_weight"], _ = self.probe(weight_spec)
        else:
            results["index_weight"] = self._skipped(
                weight_spec,
                "no CSI 300 price-index code was discovered from index_basic",
            )

        daily_spec = self._specs["index_daily"]
        selected: BenchmarkCandidate | None = None
        candidate_attempts: list[dict[str, Any]] = []
        final_daily_result: ProbeResult | None = None
        for candidate in candidates:
            candidate_spec = replace(
                daily_spec,
                params={**daily_spec.params, "ts_code": candidate.ts_code},
            )
            probe_result, _ = self.probe(candidate_spec)
            candidate_attempts.append(
                {
                    "ts_code": candidate.ts_code,
                    "kind": candidate.kind,
                    "status": probe_result.status,
                    "error_code": probe_result.error_code,
                }
            )
            final_daily_result = probe_result
            if probe_result.fit_for_purpose:
                selected = candidate
                break
        if final_daily_result is None:
            final_daily_result = self._skipped(
                daily_spec,
                "no CSI 300 benchmark code was discovered from index_basic",
            )
        elif selected is not None:
            label = "total-return" if selected.kind == "total_return" else "price"
            final_daily_result = replace(
                final_daily_result,
                note=f"selected verified {label} benchmark {selected.ts_code}",
            )
        results["index_daily"] = final_daily_result

        ordered_results = [results[endpoint].to_dict() for endpoint in PROBE_ENDPOINTS]
        return {
            "endpoint_count": len(ordered_results),
            "all_endpoints": list(PROBE_ENDPOINTS),
            "results": ordered_results,
            "benchmark_candidates": [candidate.__dict__ for candidate in candidates],
            "benchmark_attempts": candidate_attempts,
            "selected_benchmark": None
            if selected is None
            else {
                "ts_code": selected.ts_code,
                "name": selected.name,
                "kind": selected.kind,
                "includes_dividends": selected.kind == "total_return",
            },
        }
