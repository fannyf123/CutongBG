import os
import json


class ConfigManager:
    DEFAULT_CONFIG = {
        "output_format": "png",
        "headless": False,
        "incognito": False,
        "batch_size": 1,
        "max_wait_seconds": 120,
        "processing_hang_timeout": 300,
    }

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.config_path = os.path.join(base_dir, "config.json")
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    merged = dict(self.DEFAULT_CONFIG)
                    merged.update(cfg)
                    return merged
            except Exception:
                pass
        return dict(self.DEFAULT_CONFIG)

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Warning: Could not save config: {e}")

    def get_output_format(self) -> str:
        # Background remover always outputs PNG (transparent)
        return "png"

    def get_headless(self) -> bool:
        return bool(self.config.get("headless", False))

    def set_headless(self, value: bool):
        self.config["headless"] = value
        self.save_config()

    def get_incognito(self) -> bool:
        return bool(self.config.get("incognito", False))

    def set_incognito(self, value: bool):
        self.config["incognito"] = value
        self.save_config()

    def get_batch_size(self) -> int:
        try:
            return int(self.config.get("batch_size", 1))
        except Exception:
            return 1

    def set_batch_size(self, value: int):
        self.config["batch_size"] = max(1, int(value))
        self.save_config()

    def get_max_wait_seconds(self) -> int:
        try:
            v = int(self.config.get("max_wait_seconds", 120))
            return v if v > 0 else 120
        except Exception:
            return 120

    def get_processing_hang_timeout(self) -> int:
        try:
            v = int(self.config.get("processing_hang_timeout", 300))
            return v if v > 0 else 300
        except Exception:
            return 300
