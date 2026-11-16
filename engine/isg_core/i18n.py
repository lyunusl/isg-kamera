"""User-visible text in two languages: English (the source language of the code) and Turkish.

The English source text is the key. ``T("English text")`` returns it unchanged when the language is ``en`` and looks it up
in ``locales/tr.json`` when the language is ``tr``. Texts that contain values are templates with named placeholders:
``T("{n} events", n=3)``; the Turkish entry uses the same placeholder names. ``N_("text")`` only marks a text for the
catalogue (data tables that are translated later, at the point where they are shown).

The same catalogue is read by the panel's browser code (served by the panel) and by the engine inside AugeLab Studio
(the engine learns the language from the ISG_LANG environment variable). Without a setting the language is Turkish,
the language of every installation before English was added.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

LANGUAGES = ("en", "tr")
DEFAULT = "tr"
ENV_VAR = "ISG_LANG"

_state = {"lang": None}
_catalog: dict[str, str] | None = None
_lock = threading.Lock()


def catalog_path() -> Path:
    """Location of the Turkish catalogue (next to this module; in the packaged exe under engine/isg_core)."""
    here = Path(__file__).resolve().parent / "locales" / "tr.json"
    if here.exists():
        return here
    base = getattr(sys, "_MEIPASS", None)
    if base:
        packaged = Path(base) / "engine" / "isg_core" / "locales" / "tr.json"
        if packaged.exists():
            return packaged
    return here


def catalog() -> dict[str, str]:
    """The Turkish catalogue: {English source text: Turkish text}. Read once."""
    global _catalog
    if _catalog is None:
        with _lock:
            if _catalog is None:
                try:
                    data = json.loads(catalog_path().read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                _catalog = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    return _catalog


def valid(lang) -> str | None:
    """Normalizes a language code ("en", "en-US", "tr-TR" ...) to "en" or "tr"; None if it is neither."""
    if not isinstance(lang, str):
        return None
    lang = lang.strip().lower()[:2]
    return lang if lang in LANGUAGES else None


def set_language(lang) -> str:
    """Sets the language of this process (the panel calls this at start and when the setting changes)."""
    _state["lang"] = valid(lang) or DEFAULT
    return _state["lang"]


def language() -> str:
    """Current language: the one set in this process, else ISG_LANG (engine inside Studio), else Turkish."""
    return _state["lang"] or valid(os.environ.get(ENV_VAR)) or DEFAULT


def T(text: str, lang: str | None = None, **values) -> str:
    """Translates an English source text into the current (or the given) language and fills its placeholders."""
    if (valid(lang) or language()) == "tr":
        text = catalog().get(text, text)
    return text.format(**values) if values else text


def N_(text: str) -> str:
    """Marks a text for the catalogue without translating it (it is translated where it is shown)."""
    return text


# Display and zone names of the sample cameras (samples/cameras/*.json are in English). The panel and the labels the
# engine draws into clips show them in the current language; names the user typed are shown as they are.
SAMPLE_NAMES = (
    N_("Warehouse · Aisle"), N_("Press shop"), N_("Rack area"), N_("Loading area"),
    N_("Forklift aisle"), N_("Around the press"), N_("Press operator area"), N_("Rack face"), N_("Aisle in front of the rack"),
    N_("Pedestrian walkway"), N_("Forklift lane"),
)


def sample_name(name):
    """A sample camera or zone name in the current language; any other value unchanged."""
    return T(name) if isinstance(name, str) and name in SAMPLE_NAMES else name
