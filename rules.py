"""The rule catalog is the same in the panel and in the engine in AugeLab Studio: engine/isg_core/catalog.py."""
import sys
from pathlib import Path

_engine = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "engine"
if str(_engine) not in sys.path:
    sys.path.insert(0, str(_engine))

from isg_core import vault  # noqa: E402,F401  (rules.vault: model package status and errors)
from isg_core.catalog import *  # noqa: E402,F401,F403
from isg_core.catalog import RULE, RULES  # noqa: E402,F401
