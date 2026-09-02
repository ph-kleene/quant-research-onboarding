"""Command-line entry points behind the single project script."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data import ContentAddressedCache, ManifestWriter, TushareDataAccess, load_tushare_token
from .probe import CapabilityProbeRunner

ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = Path("~/.cache/shangchen-quant-research-onboarding").expanduser()


class TushareTransport:
    """Convert SDK frames to records before immutable caching."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._token_loaded = False

    def __call__(
        self, *, endpoint: str, token: str, params: dict[str, Any], fields: list[str]
    ) -> Any:
        if self._client is None:
            import tushare as ts

            self._client = ts.pro_api(token)
            self._token_loaded = True
        frame = self._client.query(endpoint, fields=",".join(fields), **params)
        if frame is None:
            return []
        return frame.to_dict(orient="records")


def build_access() -> TushareDataAccess:
    token = load_tushare_token()
    raw_root = CACHE_ROOT / "tushare"
    manifest = CACHE_ROOT / "manifests" / "requests.jsonl"
    return TushareDataAccess(
        transport=TushareTransport(),
        cache=ContentAddressedCache(raw_root, repository_root=ROOT),
        token=token,
        manifest_writer=ManifestWriter(manifest),
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _public_probe_report(report: dict[str, Any]) -> dict[str, Any]:
    candidate_codes = {item["ts_code"] for item in report.get("benchmark_candidates", [])}
    selected = report.get("selected_benchmark")
    if selected:
        candidate_codes.add(selected["ts_code"])
    clean_results = []
    for item in report["results"]:
        clean = dict(item)
        codes = list(clean.get("actual_codes", []))
        if item["endpoint"] == "index_basic":
            clean["actual_codes"] = sorted(candidate_codes)
        else:
            clean["actual_codes"] = codes[:10]
        clean_results.append(clean)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_target": {"start": "2018-01-01", "end": "2025-12-31"},
        "rate_limit_per_minute": 180,
        "environment": {"python": platform.python_version(), "platform": platform.system()},
        "endpoint_count": report["endpoint_count"],
        "results": clean_results,
        "benchmark_candidates": report.get("benchmark_candidates", []),
        "benchmark_attempts": report.get("benchmark_attempts", []),
        "selected_benchmark": selected,
        "contains_raw_responses": False,
        "contains_credentials": False,
    }


def command_probe() -> int:
    report = CapabilityProbeRunner(build_access()).run()
    public = _public_probe_report(report)
    _atomic_json(ROOT / "evidence" / "capability-probe.json", public)
    print("Tushare capability probe (secret-free summary)")
    for result in public["results"]:
        print(
            f"- {result['endpoint']}: {result['status']} rows={result['row_count']} fit={result['fit_for_purpose']}"
        )
    selected = public.get("selected_benchmark")
    if selected:
        dividend = (
            "includes dividends"
            if selected["includes_dividends"]
            else "price index, excludes dividends"
        )
        print(f"- benchmark: {selected['ts_code']} ({dividend})")
    else:
        print("- benchmark: no verified candidate")
    return 0


def command_fetch() -> int:
    from .real_case import fetch_and_evaluate_real_case

    result = fetch_and_evaluate_real_case(build_access(), root=ROOT, cache_root=CACHE_ROOT)
    print("Real-data workflow completed; public output contains aggregate evidence only.")
    print(f"- status: {result['status']}")
    print(f"- benchmark: {result.get('benchmark_code', 'unavailable')}")
    return 0 if result["status"] in {"complete", "credible_stop"} else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quant research onboarding project")
    parser.add_argument("command", choices=("probe", "fetch"))
    args = parser.parse_args(argv)
    try:
        return command_probe() if args.command == "probe" else command_fetch()
    except Exception as exc:
        print(
            f"{exc.__class__.__name__}: operation failed; inspect the secret-free evidence and retry",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
