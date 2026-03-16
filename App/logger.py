import os
import sys
from datetime import datetime


class Logger:
    def __init__(self):
        self._logs = []
        self._max_logs = 500

    def _log(self, level: str, message: str, detail: str = None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{level}] {message}"
        if detail:
            entry += f" | {detail}"
        self._logs.append(entry)
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs:]
        print(entry)

    def info(self, message: str, detail: str = None):
        self._log("INFO", message, detail)

    def sukses(self, message: str, detail: str = None):
        self._log("SUKSES", message, detail)

    def peringatan(self, message: str, detail: str = None):
        self._log("PERINGATAN", message, detail)

    def kesalahan(self, message: str, detail: str = None):
        self._log("KESALAHAN", message, detail)

    def debug(self, message: str, detail: str = None):
        self._log("DEBUG", message, detail)

    def warning(self, message: str, detail: str = None):
        self._log("WARNING", message, detail)

    def clear_log(self):
        self._logs = []

    def get_logs(self):
        return list(self._logs)


logger = Logger()
