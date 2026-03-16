from PySide6.QtCore import QObject, Signal


class ProgressSignal(QObject):
    progress = Signal(str, int)


class FileUpdateSignal(QObject):
    file_update = Signal(str, bool)
