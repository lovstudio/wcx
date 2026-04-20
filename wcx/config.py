"""Configuration: token/cookie persistence in ~/.config/wcx/."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "wcx"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
DATA_DIR = Path(user_data_dir(APP_NAME))
CONFIG_PATH = CONFIG_DIR / "config.json"
CACHE_DB = DATA_DIR / "cache.db"


@dataclass
class Credentials:
    token: str
    cookie: str

    def to_dict(self) -> dict:
        return asdict(self)


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_credentials() -> Optional[Credentials]:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text())
    if "token" not in data or "cookie" not in data:
        return None
    return Credentials(token=data["token"], cookie=data["cookie"])


def save_credentials(creds: Credentials) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(creds.to_dict(), ensure_ascii=False, indent=2))
    CONFIG_PATH.chmod(0o600)


def clear_credentials() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
