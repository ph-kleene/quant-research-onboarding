"""Execute the source notebook in a temporary directory and assert key evidence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = ROOT / "notebooks" / "research-case.ipynb"
    notebook = nbformat.read(source, as_version=4)
    with tempfile.TemporaryDirectory() as directory:
        executed = NotebookClient(
            notebook,
            timeout=180,
            kernel_name="python3",
            resources={"metadata": {"path": str(ROOT)}},
        ).execute()
        target = Path(directory) / "executed.ipynb"
        nbformat.write(executed, target)
        text = json.dumps(executed, ensure_ascii=False)
        required = ("有效月度 IC", "D/T/P", "fixture")
        missing = [item for item in required if item not in text]
        if missing:
            raise RuntimeError(f"Notebook E2E missing expected evidence labels: {missing}")
    print("Notebook E2E: PASS (executed copy kept outside repository and discarded)")


if __name__ == "__main__":
    main()
