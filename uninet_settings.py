import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "UniNet Queue"
SETTINGS_PATH = DATA_DIR / "settings.json"
COURSE_CODE = "FUN1201"
COURSE_NAME = "Physics I"


@dataclass(frozen=True)
class Preferences:
    muted: bool = False
    one_video: bool = False


def load_preferences():
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Preferences()
    if not isinstance(saved, dict):
        raise ValueError("Settings must be a JSON object.")
    return Preferences(muted=saved.get("muted") is True, one_video=saved.get("one_video") is True)


def save_preferences(preferences):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent, suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(asdict(preferences), temporary, indent=2)
            temporary.write("\n")
        temporary_path.replace(SETTINGS_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
