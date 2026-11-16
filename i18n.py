"""Panel side of the translations: the same module and catalogue as the engine (engine/isg_core/i18n.py)."""
import sys
from pathlib import Path

_engine = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "engine"
if str(_engine) not in sys.path:
    sys.path.insert(0, str(_engine))

from isg_core.i18n import *  # noqa: E402,F401,F403
from isg_core.i18n import N_, T, catalog, language, set_language, valid  # noqa: E402,F401
