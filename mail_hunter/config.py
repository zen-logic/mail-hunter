import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path) as f:
        return json.load(f)
