import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_PROFILE_PATH = Path("config/profile.json")


def load_profile(path: str = str(DEFAULT_PROFILE_PATH)) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)

