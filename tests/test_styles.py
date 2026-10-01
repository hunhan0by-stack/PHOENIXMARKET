"""
The compiled stylesheet must contain every Tailwind class the templates use.
Fails when a template gets a new class but static/css/app.css was not rebuilt
(see "Styles" in README.md).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
# Class names that are not Tailwind utilities (defined in input.css or used as hooks)
NOT_UTILITIES = {"group", "peer", "pm-fade-in", "pm-menu-panel", "bg-grid", "line-clamp-2", "line-clamp-3"}


def css_escape(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "\\" + ch for ch in name)


def template_classes():
    found = set()
    for path in list((ROOT / "templates").rglob("*.html")):
        if "emails" in path.parts:
            continue  # emails use inline styles
        text = path.read_text(encoding="utf-8")
        for attr in re.findall(r'class="([^"]*)"', text):
            for token in re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr).split():
                if re.fullmatch(r"[a-z0-9!:\[\]\(\)/.%#_-]+", token) and not token.startswith("-"):
                    found.add(token)
    return found


def test_compiled_css_covers_template_classes():
    missing = sorted(
        c for c in template_classes() - NOT_UTILITIES
        if "." + css_escape(c) not in CSS
    )
    assert not missing, f"rebuild static/css/app.css; missing: {missing[:20]}"
