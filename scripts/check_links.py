"""Check internal HTML links and anchors in a rendered Quarto site."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    site = args.site.resolve()
    if not (site / "content" / "index.html").exists():
        raise SystemExit("link check failed: rendered homepage is missing")

    failures: list[str] = []
    html_files = sorted(site.rglob("*.html"))
    for page in html_files:
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for node in soup.find_all(href=True):
            href = str(node.get("href", "")).strip()
            parsed = urlsplit(href)
            if (
                not href
                or parsed.scheme in {"http", "https", "mailto", "data", "javascript"}
                or href.startswith("//")
            ):
                continue
            target_path = unquote(parsed.path)
            if not target_path:
                target = page
            elif target_path.startswith("/"):
                target = site / target_path.lstrip("/")
            else:
                target = (page.parent / target_path).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                failures.append(f"{page.relative_to(site)} -> {href} (missing target)")
                continue
            if parsed.fragment and target.suffix.lower() in {".html", ".htm"}:
                target_soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
                if target_soup.find(id=unquote(parsed.fragment)) is None:
                    failures.append(f"{page.relative_to(site)} -> {href} (missing anchor)")
    if failures:
        raise SystemExit("internal link check failed:\n" + "\n".join(failures[:50]))
    print(f"Internal links: PASS ({len(html_files)} HTML pages)")


if __name__ == "__main__":
    main()
