import copy
import json
import os
from typing import Any


class ConfigError(Exception):
    pass


DEFAULT_CONFIG = {
    "adb_path": "",
    "scrcpy_path": "",
    "photo_dir": "photos",
    "log_dir": "logs",
    "auto_start_scrcpy": True,
    "debug": False,
    "timeouts": {
        "adb": 10,
        "device_check": 10,
        "camera_launch": 15,
        "ui_ready": 15,
        "capture": 10,
        "file_verify": 10,
        "pull": 120,
        "scrcpy_start": 10,
    },
    "camera": {
        "package": "",
        "activity": "",
        "strict_single_photo": True,
        "poll_interval": 1.0,
        "fallback_shutter": {
            "enabled": True,
            "use_if_ui_not_found": True,
            "reference_width": 1080,
            "reference_height": 2400,
            "shutter_x": 540,
            "shutter_y": 2200,
        },
    },
    "scrcpy": {
        "max_size": 1280,
        "window_title": "Redmi Note 13 Pro",
        "extra_args": [],
    },
}


def _deep_merge(default: dict, override: dict) -> dict:
    result = copy.deepcopy(default)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    def __init__(self, path: str = "config.json"):
        self.path = path
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.save()
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config.json is not valid JSON: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Could not read config.json: {exc}") from exc

        if not isinstance(loaded, dict):
            raise ConfigError("config.json must contain a JSON object.")

        self.data = _deep_merge(DEFAULT_CONFIG, loaded)

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            raise ConfigError(f"Could not save config.json: {exc}") from exc

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node = self.data
        for key in dotted_key.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        keys = dotted_key.split(".")
        node = self.data
        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        node[keys[-1]] = value

    def ensure_directories(self) -> None:
        photo_dir = self.get("photo_dir", "photos")
        log_dir = self.get("log_dir", "logs")
        os.makedirs(photo_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)