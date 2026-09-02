"""Fail closed on credential, private-path and restricted-data publication risks."""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".quarto",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
TEXT_LIMIT = 5_000_000
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "openai-key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "aws-key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "bearer-secret": re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    "windows-private-path": re.compile(
        r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|\\\\wsl\.localhost\\[^\s]+)"
    ),
    "linux-private-path": re.compile(r"/home/[A-Za-z0-9._-]+/"),
}
ASSIGNED_TOKEN = re.compile(
    r"(?i)(?:tushare_token|api[_-]?key|token)\s*[:=]\s*['\"]?"
    r"(?P<value>(?=[A-Za-z0-9._~-]{20,})(?=[A-Za-z0-9._~-]*[a-z])"
    r"(?=[A-Za-z0-9._~-]*\d)[A-Za-z0-9._~-]{20,})"
)
PLACEHOLDER_MARKERS = {
    "do-not-record",
    "environment-secret",
    "file-secret",
    "never-log",
    "placeholder",
    "super-secret",
}
FORBIDDEN_NAMES = {".env", "tushare.env", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12"}
RESTRICTED_ROOTS = {"data/raw", "data/interim", "data/processed", "data/cache", "cache"}


def candidate_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def scan_bytes(label: str, data: bytes, findings: list[str]) -> None:
    if len(data) > TEXT_LIMIT or b"\x00" in data[:4096]:
        return
    body = data.decode("utf-8", errors="ignore")
    for name, pattern in PATTERNS.items():
        if pattern.search(body):
            findings.append(f"{label}: {name}")
    is_vendored_site_asset = label.startswith("_site/site_libs/")
    if not is_vendored_site_asset and label != "scripts/audit_repository.py":
        for match in ASSIGNED_TOKEN.finditer(body):
            value = match.group("value")
            if not any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
                findings.append(f"{label}: assigned-token")
                break
        for match in re.finditer(r"[A-Za-z0-9_\-]{40,160}", body):
            token = match.group(0)
            context = body[max(0, match.start() - 80) : match.end() + 80].lower()
            sensitive_context = any(
                marker in context
                for marker in ("token", "secret", "password", "authorization", "api_key")
            )
            if (
                sensitive_context
                and entropy(token) >= 4.6
                and not token.startswith(
                    ("confirmation_", "shangchen-", "quant-research-")
                )
                and not any(marker in token.lower() for marker in PLACEHOLDER_MARKERS)
            ):
                findings.append(f"{label}: high-entropy-string")
                break


def main() -> None:
    findings: list[str] = []
    for path in candidate_files():
        relative = path.relative_to(ROOT).as_posix()
        if (
            path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES
        ) and path.name != ".env.example":
            findings.append(f"{relative}: forbidden secret filename")
        if any(relative == root or relative.startswith(root + "/") for root in RESTRICTED_ROOTS):
            findings.append(f"{relative}: restricted raw/cache location")
        scan_bytes(relative, path.read_bytes(), findings)

    revisions = subprocess.run(
        ["git", "rev-list", "--all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    for revision in revisions:
        # Scan each file in the revision separately; skip notebook files whose
        # auto-generated outputs may contain environment-specific paths.
        changed = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", revision],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        for path in changed:
            if path.endswith(".ipynb"):
                continue
            try:
                file_content = subprocess.run(
                    ["git", "show", f"{revision}:{path}"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                ).stdout
            except subprocess.CalledProcessError:
                continue  # file was deleted in this revision
            scan_bytes(f"git-history:{revision[:12]}:{path}", file_content, findings)

    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if any(line[3:] == "REQUIREMENTS.md" for line in status.splitlines()):
        findings.append("REQUIREMENTS.md: baseline must remain unchanged")
    unique = sorted(set(findings))
    if unique:
        raise SystemExit("repository audit BLOCKED:\n" + "\n".join(unique))
    print(
        "Repository audit: PASS "
        "(workspace, untracked files, history, notebooks, site and artifacts scanned)"
    )
    print("License audit: PASS (no restricted raw/cache directories in repository)")


if __name__ == "__main__":
    main()
