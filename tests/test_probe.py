from quant_onboarding.data import FetchResult, PermissionDeniedError
from quant_onboarding.probe import (
    PROBE_ENDPOINTS,
    CapabilityProbeRunner,
    discover_csi300_candidates,
)


def fetched(endpoint, records):
    fields = sorted({key for row in records for key in row})
    dates = [str(row.get("trade_date") or row.get("cal_date") or "") for row in records]
    dates = [date for date in dates if date]
    codes = sorted(
        {
            str(row.get("ts_code") or row.get("index_code") or "")
            for row in records
            if row.get("ts_code") or row.get("index_code")
        }
    )
    manifest = {
        "response_fields": fields,
        "row_count": len(records),
        "date_range": {"min": min(dates) if dates else None, "max": max(dates) if dates else None},
        "actual_codes": codes,
    }
    return FetchResult(records, "hash", endpoint, False, manifest)


class FakeAccess:
    def __init__(self, deny=None):
        self.deny = set(deny or [])
        self.calls = []

    def fetch(self, endpoint, *, params=None, fields=()):
        self.calls.append((endpoint, dict(params or {})))
        if endpoint in self.deny:
            raise PermissionDeniedError(endpoint, 403)
        if endpoint == "index_basic":
            return fetched(
                endpoint,
                [
                    {
                        "ts_code": "H00300.CSI",
                        "name": "沪深300全收益",
                        "fullname": "沪深300全收益指数",
                        "market": "CSI",
                        "publisher": "中证",
                        "category": "全收益",
                        "base_date": "20041231",
                        "list_date": "20050104",
                    },
                    {
                        "ts_code": "000300.SH",
                        "name": "沪深300",
                        "fullname": "沪深300指数",
                        "market": "SSE",
                        "publisher": "中证",
                        "category": "规模指数",
                        "base_date": "20041231",
                        "list_date": "20050408",
                    },
                ],
            )
        row = {
            field: ("20251231" if field in {"trade_date", "cal_date"} else 1) for field in fields
        }
        if "ts_code" in row:
            row["ts_code"] = str((params or {}).get("ts_code", "000001.SZ"))
        if "index_code" in row:
            row["index_code"] = str((params or {}).get("index_code", "000300.SH"))
        if "con_code" in row:
            row["con_code"] = "000001.SZ"
        return fetched(endpoint, [row])


def test_candidate_discovery_uses_actual_codes_and_prefers_total_return():
    candidates = discover_csi300_candidates(
        [
            {"ts_code": "H00300.CSI", "name": "沪深300全收益"},
            {"ts_code": "000300.SH", "name": "沪深300"},
            {"ts_code": "000300CAD14.CSI", "name": "沪深300加元对冲全收益"},
            {"ts_code": "399001.SZ", "name": "深证成指"},
        ]
    )
    assert [(item.ts_code, item.kind) for item in candidates] == [
        ("H00300.CSI", "total_return"),
        ("000300.SH", "price"),
    ]


def test_runner_covers_nine_endpoints_and_selects_verified_candidate():
    access = FakeAccess()
    report = CapabilityProbeRunner(access).run()
    assert report["endpoint_count"] == 9
    assert set(report["all_endpoints"]) == set(PROBE_ENDPOINTS)
    assert report["selected_benchmark"]["ts_code"] == "H00300.CSI"
    assert report["selected_benchmark"]["includes_dividends"] is True
    index_calls = [params for endpoint, params in access.calls if endpoint == "index_daily"]
    assert index_calls[0]["ts_code"] == "H00300.CSI"


def test_single_endpoint_permission_failure_does_not_abort_probe():
    report = CapabilityProbeRunner(FakeAccess(deny={"suspend_d"})).run()
    results = {item["endpoint"]: item for item in report["results"]}
    assert results["suspend_d"]["status"] == "permission_denied"
    assert results["daily"]["status"] == "success"
