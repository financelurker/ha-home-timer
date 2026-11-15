"""README image URLs carry the hash of their file (scripts/readme_images.py), and every image exists."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
RAW = "https://raw.githubusercontent.com/financelurker/ha-home-timer/main/"

_spec = importlib.util.spec_from_file_location("readme_images", ROOT / "scripts" / "readme_images.py")
readme_images = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readme_images)


def test_readme_image_urls_up_to_date():
    text = README.read_text(encoding="utf-8")
    updated, missing = readme_images.versioned(text)
    assert not missing
    assert updated == text, "run: python scripts/readme_images.py"
    assert "?v=" in text


def test_readme_image_urls_are_stamped(tmp_path):
    (tmp_path / "docs" / "images").mkdir(parents=True)
    (tmp_path / "docs" / "images" / "a.png").write_bytes(b"one")
    text = f"![a]({RAW}docs/images/a.png?v=0000000000) ![b]({RAW}docs/images/b.png)"
    updated, missing = readme_images.versioned(text, tmp_path)
    assert missing == {"docs/images/b.png"}
    assert f"{RAW}docs/images/a.png?v=7692c3ad35" in updated  # sha256("one")[:10]


def test_images_of_the_docs_exist():
    for page in (ROOT / "docs").glob("*.md"):
        for src in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)|<img [^>]*src=\"([^\"]+)\"", page.read_text(encoding="utf-8")):
            path = next(p for p in src if p)
            assert (page.parent / path).is_file(), f"{page.name}: {path}"
