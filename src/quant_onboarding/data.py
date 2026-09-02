"""Safe, resumable access primitives for the Tushare data route.

The module deliberately does not import the Tushare SDK.  A small transport
callable is injected by the application, which keeps the request-governance
and cache contracts deterministic and completely mockable in CI.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import stat
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_CALLS_PER_MINUTE = 180
DEFAULT_SECRET_FILE = Path("~/.config/shangchen-quant-research-onboarding/tushare.env")
DEFAULT_CACHE_ROOT = Path("~/.cache/shangchen-quant-research-onboarding/tushare")

_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "token",
    "tushare_token",
    "api_key",
    "apikey",
}
_TOKEN_LINE = re.compile(r"^(?:export\s+)?TUSHARE_TOKEN\s*=\s*(.*)$")


class DataAccessError(RuntimeError):
    """Base class for intentionally secret-free data access errors."""


class TokenNotConfiguredError(DataAccessError):
    pass


class UnsafeSecretFileError(DataAccessError):
    pass


class UnsafeCacheLocationError(DataAccessError):
    pass


class CacheConflictError(DataAccessError):
    def __init__(self, snapshot_key: str, content_hashes: Sequence[str]):
        self.snapshot_key = snapshot_key
        self.content_hashes = tuple(content_hashes)
        super().__init__(
            f"immutable cache conflict for snapshot {snapshot_key}; "
            f"preserved {len(self.content_hashes)} content objects"
        )


class ProgressConflictError(DataAccessError):
    pass


class ApiRequestError(DataAccessError):
    """A classified API failure whose message never contains upstream text."""

    retryable = False

    def __init__(self, endpoint: str, code: str | int | None = None):
        self.endpoint = endpoint
        self.code = None if code is None else str(code)
        suffix = "" if self.code is None else f" (code={self.code})"
        super().__init__(f"{self.__class__.__name__} at {endpoint}{suffix}")


class PermissionDeniedError(ApiRequestError):
    pass


class ParameterError(ApiRequestError):
    pass


class RateLimitError(ApiRequestError):
    retryable = True


class TransientApiError(ApiRequestError):
    retryable = True


class PermanentApiError(ApiRequestError):
    pass


def _strip_optional_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_tushare_token(
    *,
    environ: Mapping[str, str] | None = None,
    secret_file: str | os.PathLike[str] | None = None,
) -> str:
    """Load the token from the environment or the fixed repository-external file.

    Environment wins.  On POSIX, a configured file must be a regular file and
    have no group/other permission bits (normally mode 0600).  Neither token
    values nor file contents are included in any error.
    """

    env = os.environ if environ is None else environ
    token = env.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token

    path = Path(secret_file or DEFAULT_SECRET_FILE).expanduser()
    if not path.exists():
        raise TokenNotConfiguredError(
            "TUSHARE_TOKEN is not configured in the environment or secret file"
        )
    if not path.is_file():
        raise UnsafeSecretFileError("the configured Tushare secret path is not a file")

    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise UnsafeSecretFileError(
                "the Tushare secret file must not be accessible by group or others"
            )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UnsafeSecretFileError("the Tushare secret file cannot be read") from exc

    for line in lines:
        match = _TOKEN_LINE.match(line.strip())
        if match:
            token = _strip_optional_quotes(match.group(1))
            if token:
                return token
            break
    raise TokenNotConfiguredError("TUSHARE_TOKEN is not configured in the secret file")


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith("_token")


def redact_secrets(value: Any, *, known_secrets: Iterable[str] = ()) -> Any:
    """Return a recursively redacted, JSON-compatible representation."""

    secrets = tuple(secret for secret in known_secrets if secret)
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>"
            if _is_sensitive_key(key)
            else redact_secrets(item, known_secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_secrets(item, known_secrets=secrets) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "<redacted>")
        return redacted
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def normalized_parameters(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize non-secret request parameters for keys and manifests."""

    clean = redact_secrets(dict(params or {}))
    return {key: clean[key] for key in sorted(clean) if clean[key] != "<redacted>"}


def _canonical_json(value: Any) -> bytes:
    safe = redact_secrets(value)
    return json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def snapshot_key(
    endpoint: str,
    params: Mapping[str, Any] | None = None,
    fields: Sequence[str] | None = None,
) -> str:
    material = {
        "endpoint": endpoint,
        "params": normalized_parameters(params),
        "fields": sorted(set(fields or ())),
    }
    return content_hash(material)


class RateLimiter:
    """Thread-safe minimum-interval limiter capped at 180 calls/minute."""

    def __init__(
        self,
        calls_per_minute: int = MAX_CALLS_PER_MINUTE,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= calls_per_minute <= MAX_CALLS_PER_MINUTE:
            raise ValueError(f"calls_per_minute must be between 1 and {MAX_CALLS_PER_MINUTE}")
        self.calls_per_minute = calls_per_minute
        self._interval = 60.0 / calls_per_minute
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed: float | None = None

    def acquire(self) -> float:
        """Wait until one request is allowed and return the wait in seconds."""

        with self._lock:
            now = self._clock()
            wait = 0.0 if self._next_allowed is None else max(0.0, self._next_allowed - now)
            if wait:
                self._sleep(wait)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed or now) + self._interval
            return wait


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay(self, failed_attempt: int, *, random_value: float | None = None) -> float:
        base = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, failed_attempt - 1)),
        )
        draw = random.random() if random_value is None else random_value
        return base * (1 + self.jitter_ratio * ((2 * draw) - 1))


def classify_api_error(endpoint: str, error: BaseException | Mapping[str, Any]) -> ApiRequestError:
    """Classify transport/API failures without propagating the upstream message."""

    if isinstance(error, ApiRequestError):
        return error
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("status") or error.get("status_code")
        raw_message = str(error.get("msg") or error.get("message") or "")
    else:
        code = getattr(error, "status_code", None) or getattr(error, "code", None)
        raw_message = str(error)
    message = raw_message.lower()
    code_text = "" if code is None else str(code).lower()

    if code_text == "429" or any(
        hint in message for hint in ("too many", "rate limit", "频率", "每分钟")
    ):
        return RateLimitError(endpoint, code or 429)
    if any(
        hint in message
        for hint in (
            "permission",
            "privilege",
            "forbidden",
            "unauthorized",
            "权限",
            "积分",
            "token不正确",
            "token invalid",
        )
    ) or code_text in {"401", "403"}:
        return PermissionDeniedError(endpoint, code)
    if (
        any(hint in message for hint in ("parameter", "invalid param", "参数"))
        or code_text == "400"
    ):
        return ParameterError(endpoint, code)
    if (
        isinstance(error, (TimeoutError, ConnectionError))
        or code_text.startswith("5")
        or any(
            hint in message
            for hint in ("timeout", "timed out", "temporary", "connection", "service unavailable")
        )
    ):
        return TransientApiError(endpoint, code)
    return PermanentApiError(endpoint, code)


def _response_error(response: Any) -> Mapping[str, Any] | None:
    if not isinstance(response, Mapping):
        return None
    code = response.get("code")
    if code in (None, 0, "0"):
        return None
    return response


class ContentAddressedCache:
    """Repository-external immutable object store with conflict detection."""

    def __init__(
        self,
        root: str | os.PathLike[str] = DEFAULT_CACHE_ROOT,
        *,
        repository_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if repository_root is not None:
            repo = Path(repository_root).resolve()
            if self.root == repo or repo in self.root.parents:
                raise UnsafeCacheLocationError("raw cache must be outside the repository")
        self.objects = self.root / "objects"
        self.snapshots = self.root / "snapshots"
        self.conflicts = self.root / "conflicts"
        for directory in (self.objects, self.snapshots, self.conflicts):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / f"{digest}.json"

    def _snapshot_path(self, key: str) -> Path:
        return self.snapshots / f"{key}.json"

    @staticmethod
    def _write_new(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError as error:
            if path.read_bytes() != payload:
                raise DataAccessError("content-addressed object does not match its hash") from error

    @staticmethod
    def _replace_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
        temporary.write_bytes(_canonical_json(value))
        os.replace(temporary, path)

    def put(self, key: str, value: Any) -> str:
        body = _canonical_json(value)
        digest = hashlib.sha256(body).hexdigest()
        object_path = self._object_path(digest)
        self._write_new(object_path, body)

        with self._lock:
            index_path = self._snapshot_path(key)
            if not index_path.exists():
                self._write_new(index_path, _canonical_json({"content_hashes": [digest]}))
                return digest
            index = json.loads(index_path.read_text(encoding="utf-8"))
            hashes = list(index.get("content_hashes", []))
            if digest in hashes:
                return digest
            hashes.append(digest)
            self._replace_json(index_path, {"content_hashes": hashes})
            event = self.conflicts / f"{key}-{len(hashes)}.json"
            self._write_new(
                event,
                _canonical_json({"snapshot_key": key, "content_hashes": hashes}),
            )
            raise CacheConflictError(key, hashes)

    def hashes(self, key: str) -> tuple[str, ...]:
        path = self._snapshot_path(key)
        if not path.exists():
            return ()
        index = json.loads(path.read_text(encoding="utf-8"))
        return tuple(index.get("content_hashes", ()))

    def get(self, key: str) -> Any | None:
        hashes = self.hashes(key)
        if not hashes:
            return None
        if len(hashes) != 1:
            raise CacheConflictError(key, hashes)
        path = self._object_path(hashes[0])
        return json.loads(path.read_text(encoding="utf-8"))


class ProgressStore:
    """Atomic, idempotent completion ledger for resumable request chunks."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tasks": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def completed_hash(self, task: str, chunk: str) -> str | None:
        return self._read().get("tasks", {}).get(task, {}).get(chunk)

    def is_completed(self, task: str, chunk: str) -> bool:
        return self.completed_hash(task, chunk) is not None

    def mark_completed(self, task: str, chunk: str, digest: str) -> None:
        with self._lock:
            state = self._read()
            tasks = state.setdefault("tasks", {})
            chunks = tasks.setdefault(task, {})
            existing = chunks.get(chunk)
            if existing is not None and existing != digest:
                raise ProgressConflictError(
                    f"completed chunk {task}/{chunk} cannot change content hash"
                )
            chunks[chunk] = digest
            temporary = self.path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
            temporary.write_bytes(_canonical_json(state))
            os.replace(temporary, self.path)


def response_records(response: Any) -> list[dict[str, Any]]:
    """Normalize common Tushare/fixture response shapes into record dictionaries."""

    if response is None:
        return []
    if hasattr(response, "to_dict"):
        try:
            converted = response.to_dict(orient="records")
            return [dict(record) for record in converted]
        except TypeError:
            pass
    if isinstance(response, list):
        return [dict(record) for record in response if isinstance(record, Mapping)]
    if isinstance(response, Mapping):
        data = response.get("data", response)
        if isinstance(data, list):
            return [dict(record) for record in data if isinstance(record, Mapping)]
        if isinstance(data, Mapping):
            fields = list(data.get("fields", ()))
            items = data.get("items", ())
            if fields and isinstance(items, Sequence):
                return [dict(zip(fields, row, strict=False)) for row in items]
            records = data.get("records")
            if isinstance(records, list):
                return [dict(record) for record in records if isinstance(record, Mapping)]
    return []


def _date_range(records: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    date_keys = ("trade_date", "cal_date", "effective_date", "list_date")
    dates: list[str] = []
    for record in records:
        for key in date_keys:
            value = record.get(key)
            if value not in (None, ""):
                dates.append(str(value))
                break
    return (min(dates), max(dates)) if dates else (None, None)


def manifest_entry(
    *,
    endpoint: str,
    params: Mapping[str, Any] | None,
    request_fields: Sequence[str],
    response: Any = None,
    digest: str | None = None,
    status: str = "success",
    error_code: str | int | None = None,
    fetched_at: datetime | None = None,
    license_classification: str = "restricted-raw",
) -> dict[str, Any]:
    records = response_records(response)
    response_fields = sorted({str(key) for record in records for key in record})
    earliest, latest = _date_range(records)
    actual_codes = sorted(
        {
            str(record[key])
            for record in records
            for key in ("ts_code", "index_code", "con_code")
            if record.get(key) not in (None, "")
        }
    )
    safe = {
        "endpoint": endpoint,
        "params": normalized_parameters(params),
        "request_fields": sorted(set(request_fields)),
        "response_fields": response_fields,
        "row_count": len(records),
        "date_range": {"min": earliest, "max": latest},
        "content_hash": digest,
        "fetched_at": (fetched_at or datetime.now(UTC)).isoformat(),
        "status": status,
        "error_code": None if error_code is None else str(error_code),
        "actual_codes": actual_codes,
        "cache_object": None if digest is None else f"sha256:{digest}",
        "license_classification": license_classification,
    }
    return redact_secrets(safe)


class ManifestWriter:
    """Append-only JSON Lines writer for already-redacted manifest entries."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, entry: Mapping[str, Any]) -> None:
        line = _canonical_json(redact_secrets(entry)) + b"\n"
        with self._lock, self.path.open("ab") as handle:
            handle.write(line)


@dataclass
class FetchResult:
    data: Any
    content_hash: str
    snapshot_key: str
    from_cache: bool
    manifest: dict[str, Any]


Transport = Callable[..., Any]


@dataclass
class TushareDataAccess:
    """Governed facade around an injected Tushare-compatible transport."""

    transport: Transport
    cache: ContentAddressedCache
    token: str
    limiter: RateLimiter = field(default_factory=RateLimiter)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep
    random_value: Callable[[], float] = random.random
    manifest_writer: ManifestWriter | None = None

    def _call(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        fields: Sequence[str],
    ) -> Any:
        last_error: ApiRequestError | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            self.limiter.acquire()
            try:
                response = self.transport(
                    endpoint=endpoint,
                    token=self.token,
                    params=dict(params),
                    fields=list(fields),
                )
                api_error = _response_error(response)
                if api_error is not None:
                    raise classify_api_error(endpoint, api_error)
                return response
            except Exception as exc:
                error = classify_api_error(endpoint, exc)
                last_error = error
                if not error.retryable or attempt >= self.retry_policy.max_attempts:
                    raise error from None
                self.sleep(self.retry_policy.delay(attempt, random_value=self.random_value()))
        assert last_error is not None
        raise last_error

    def fetch(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Sequence[str] = (),
        license_classification: str = "restricted-raw",
    ) -> FetchResult:
        safe_params = normalized_parameters(params)
        key = snapshot_key(endpoint, safe_params, fields)
        cached = self.cache.get(key)
        if cached is not None:
            digest = content_hash(cached)
            entry = manifest_entry(
                endpoint=endpoint,
                params=safe_params,
                request_fields=fields,
                response=cached,
                digest=digest,
                status="cache_hit",
                license_classification=license_classification,
            )
            if self.manifest_writer:
                self.manifest_writer.append(entry)
            return FetchResult(cached, digest, key, True, entry)

        try:
            response = self._call(endpoint, safe_params, fields)
            digest = self.cache.put(key, response)
        except ApiRequestError as exc:
            entry = manifest_entry(
                endpoint=endpoint,
                params=safe_params,
                request_fields=fields,
                status="error",
                error_code=exc.code,
                license_classification=license_classification,
            )
            if self.manifest_writer:
                self.manifest_writer.append(entry)
            raise

        entry = manifest_entry(
            endpoint=endpoint,
            params=safe_params,
            request_fields=fields,
            response=response,
            digest=digest,
            license_classification=license_classification,
        )
        if self.manifest_writer:
            self.manifest_writer.append(entry)
        return FetchResult(response, digest, key, False, entry)

    def fetch_chunks(
        self,
        task: str,
        requests: Iterable[Mapping[str, Any]],
        *,
        progress: ProgressStore,
    ) -> list[FetchResult]:
        """Fetch unfinished chunks and make successful completion resumable."""

        results: list[FetchResult] = []
        for request in requests:
            endpoint = str(request["endpoint"])
            params = request.get("params") or {}
            fields = tuple(request.get("fields") or ())
            chunk = str(request.get("chunk") or snapshot_key(endpoint, params, fields))
            if progress.is_completed(task, chunk):
                continue
            result = self.fetch(endpoint, params=params, fields=fields)
            progress.mark_completed(task, chunk, result.content_hash)
            results.append(result)
        return results
