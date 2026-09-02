from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_onboarding.data import (  # noqa: E402
    CacheConflictError,
    ContentAddressedCache,
    ManifestWriter,
    PermissionDeniedError,
    ProgressConflictError,
    ProgressStore,
    RateLimiter,
    RetryPolicy,
    TushareDataAccess,
    UnsafeCacheLocationError,
    UnsafeSecretFileError,
    load_tushare_token,
    manifest_entry,
    normalized_parameters,
    snapshot_key,
)


class TokenTests(unittest.TestCase):
    def test_environment_has_precedence_and_value_is_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.env"
            token = load_tushare_token(
                environ={"TUSHARE_TOKEN": " environment-secret "},
                secret_file=missing,
            )
        self.assertEqual(token, "environment-secret")

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_secret_file_requires_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tushare.env"
            path.write_text("TUSHARE_TOKEN=file-secret\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(UnsafeSecretFileError) as caught:
                load_tushare_token(environ={}, secret_file=path)
            self.assertNotIn("file-secret", str(caught.exception))
            path.chmod(0o600)
            self.assertEqual(
                load_tushare_token(environ={}, secret_file=path),
                "file-secret",
            )

    def test_sensitive_parameters_are_removed(self):
        clean = normalized_parameters(
            {"ts_code": "000001.SZ", "token": "do-not-record", "api_key": "also-secret"}
        )
        self.assertEqual(clean, {"ts_code": "000001.SZ"})


class RateLimitAndRetryTests(unittest.TestCase):
    def test_rate_cannot_exceed_180_and_requests_are_spaced(self):
        now = [10.0]
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = RateLimiter(180, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(limiter.acquire(), 0.0)
        wait = limiter.acquire()
        self.assertAlmostEqual(wait, 1 / 3)
        self.assertAlmostEqual(waits[0], 1 / 3)
        with self.assertRaises(ValueError):
            RateLimiter(181)

    def _access(self, root, transport, *, retry=None, sleeps=None):
        class NoopLimiter:
            def acquire(self):
                return 0

        return TushareDataAccess(
            transport=transport,
            cache=ContentAddressedCache(root),
            token="never-log-this-token",
            limiter=NoopLimiter(),
            retry_policy=retry or RetryPolicy(max_attempts=4, jitter_ratio=0),
            sleep=(sleeps if sleeps is not None else []).append,
            random_value=lambda: 0.5,
        )

    def test_transient_errors_back_off_then_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = []
            sleeps = []

            def transport(**_kwargs):
                attempts.append(1)
                if len(attempts) < 3:
                    raise TimeoutError("temporary outage contains never-log-this-token")
                return [{"ts_code": "000001.SZ", "trade_date": "20251231"}]

            access = self._access(Path(directory) / "cache", transport, sleeps=sleeps)
            result = access.fetch("daily", fields=("ts_code", "trade_date"))
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleeps, [1.0, 2.0])
            self.assertFalse(result.from_cache)

    def test_permission_error_is_not_retried_or_leaked(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = []

            def transport(**_kwargs):
                attempts.append(1)
                return {"code": 403, "msg": "权限不足 never-log-this-token"}

            access = self._access(Path(directory) / "cache", transport)
            with self.assertRaises(PermissionDeniedError) as caught:
                access.fetch("index_weight")
            self.assertEqual(len(attempts), 1)
            self.assertNotIn("never-log-this-token", str(caught.exception))


class CacheProgressAndManifestTests(unittest.TestCase):
    def test_cache_must_be_repository_external_when_repository_is_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            with self.assertRaises(UnsafeCacheLocationError):
                ContentAddressedCache(repo / "cache", repository_root=repo)

    def test_success_cache_is_content_addressed_and_conflict_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ContentAddressedCache(Path(directory) / "cache")
            key = snapshot_key("daily", {"ts_code": "000001.SZ"}, ("close",))
            first = [{"close": 10.0}]
            first_hash = cache.put(key, first)
            self.assertEqual(cache.put(key, first), first_hash)
            with self.assertRaises(CacheConflictError) as caught:
                cache.put(key, [{"close": 10.1}])
            self.assertEqual(len(caught.exception.content_hashes), 2)
            self.assertTrue(all(cache._object_path(item).exists() for item in cache.hashes(key)))
            with self.assertRaises(CacheConflictError):
                cache.get(key)

    def test_progress_is_resumable_and_cannot_rewrite_a_completed_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json")
            progress.mark_completed("daily", "chunk-1", "abc")
            self.assertTrue(progress.is_completed("daily", "chunk-1"))
            progress.mark_completed("daily", "chunk-1", "abc")
            with self.assertRaises(ProgressConflictError):
                progress.mark_completed("daily", "chunk-1", "different")

    def test_fetch_uses_cache_and_manifest_contains_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            manifest_path = Path(directory) / "manifest.jsonl"

            def transport(**kwargs):
                calls.append(kwargs)
                return [{"ts_code": "000001.SZ", "trade_date": "20251231", "close": 10.0}]

            class NoopLimiter:
                def acquire(self):
                    return 0

            access = TushareDataAccess(
                transport=transport,
                cache=ContentAddressedCache(Path(directory) / "cache"),
                token="super-secret-token",
                limiter=NoopLimiter(),
                manifest_writer=ManifestWriter(manifest_path),
            )
            first = access.fetch(
                "daily",
                params={"ts_code": "000001.SZ", "token": "super-secret-token"},
                fields=("ts_code", "trade_date", "close"),
            )
            second = access.fetch(
                "daily",
                params={"ts_code": "000001.SZ"},
                fields=("ts_code", "trade_date", "close"),
            )
            self.assertEqual(len(calls), 1)
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            manifest_text = manifest_path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret-token", manifest_text)
            entries = [json.loads(line) for line in manifest_text.splitlines()]
            self.assertEqual(entries[0]["row_count"], 1)
            self.assertEqual(entries[0]["date_range"]["max"], "20251231")

    def test_resumable_chunk_fetch_skips_completed_work(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def transport(**kwargs):
                calls.append(kwargs["params"]["ts_code"])
                return [{"ts_code": kwargs["params"]["ts_code"], "trade_date": "20251231"}]

            class NoopLimiter:
                def acquire(self):
                    return 0

            access = TushareDataAccess(
                transport=transport,
                cache=ContentAddressedCache(Path(directory) / "cache"),
                token="secret",
                limiter=NoopLimiter(),
            )
            progress = ProgressStore(Path(directory) / "progress.json")
            requests = [
                {"endpoint": "daily", "chunk": "a", "params": {"ts_code": "000001.SZ"}},
                {"endpoint": "daily", "chunk": "b", "params": {"ts_code": "000002.SZ"}},
            ]
            access.fetch_chunks("prices", requests, progress=progress)
            access.fetch_chunks("prices", requests, progress=progress)
            self.assertEqual(calls, ["000001.SZ", "000002.SZ"])

    def test_manifest_entry_excludes_raw_rows_and_secret_keys(self):
        entry = manifest_entry(
            endpoint="daily",
            params={"ts_code": "000001.SZ", "authorization": "secret"},
            request_fields=("ts_code", "trade_date"),
            response=[{"ts_code": "000001.SZ", "trade_date": "20251231"}],
            digest="abc",
        )
        serialized = json.dumps(entry)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("records", entry)


if __name__ == "__main__":
    unittest.main()
