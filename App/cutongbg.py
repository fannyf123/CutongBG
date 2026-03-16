import os
import sys
from pathlib import Path
from typing import List

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QFileDialog, QTextEdit,
    QTabWidget, QFrame, QSizePolicy, QCheckBox, QSpinBox
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QFont, QColor, QPalette

from .background_process import ImageProcessor, ProgressSignal, FileUpdateSignal
from .config_manager import ConfigManager
from .logger import logger


APP_NAME = "CutongBG"
APP_VERSION = "1.0.0"
ACCENT_COLOR = "#00C853"  # Green - representing clean/removed background
BG_COLOR = "#1E1E2E"
SURFACE_COLOR = "#2A2A3E"
TEXT_COLOR = "#FFFFFF"
MUTED_COLOR = "#888899"


class WorkerThread(QThread):
    finished = Signal()
    error = Signal(str)

    def __init__(self, processor: ImageProcessor, paths: List[str]):
        super().__init__()
        self.processor = processor
        self.paths = paths

    def run(self):
        try:
            self.processor.start_processing(self.paths)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class DropArea(QLabel):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(180)
        self.setText("🗑️  Seret gambar / folder ke sini\n\natau gunakan tombol di bawah")
        self.setStyleSheet(f"""
            QLabel {{
                border: 2px dashed {ACCENT_COLOR};
                border-radius: 12px;
                color: {MUTED_COLOR};
                font-size: 14px;
                background: {SURFACE_COLOR};
                padding: 20px;
            }}
            QLabel:hover {{
                border-color: #00FF6E;
                color: {TEXT_COLOR};
            }}
        """)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self.styleSheet().replace(ACCENT_COLOR, "#00FF6E"))

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self.styleSheet().replace("#00FF6E", ACCENT_COLOR))

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        self.files_dropped.emit(paths)
        self.setStyleSheet(self.styleSheet().replace("#00FF6E", ACCENT_COLOR))


class MainWindow(QMainWindow):
    def __init__(self, base_dir: str, icon_path: str = None):
        super().__init__()
        self.base_dir = base_dir
        self.config_manager = ConfigManager(base_dir)
        self.processor = None
        self.worker_thread = None
        self.selected_paths = []

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} - Background Remover")
        self.setMinimumSize(700, 580)
        self.resize(800, 640)

        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self._setup_palette()
        self._build_ui()

    def _setup_palette(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(BG_COLOR))
        palette.setColor(QPalette.WindowText, QColor(TEXT_COLOR))
        palette.setColor(QPalette.Base, QColor(SURFACE_COLOR))
        palette.setColor(QPalette.AlternateBase, QColor(BG_COLOR))
        palette.setColor(QPalette.ToolTipBase, QColor(TEXT_COLOR))
        palette.setColor(QPalette.ToolTipText, QColor(TEXT_COLOR))
        palette.setColor(QPalette.Text, QColor(TEXT_COLOR))
        palette.setColor(QPalette.Button, QColor(SURFACE_COLOR))
        palette.setColor(QPalette.ButtonText, QColor(TEXT_COLOR))
        palette.setColor(QPalette.Highlight, QColor(ACCENT_COLOR))
        palette.setColor(QPalette.HighlightedText, QColor("#000000"))
        QApplication.setPalette(palette)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel(f"✂️  {APP_NAME}  —  Hapus Background Otomatis via Picsart")
        header.setAlignment(Qt.AlignCenter)
        header.setFont(QFont("Segoe UI", 14, QFont.Bold))
        header.setStyleSheet(f"color: {ACCENT_COLOR}; padding: 6px;")
        main_layout.addWidget(header)

        # Tabs
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid #444; border-radius: 8px; background: {SURFACE_COLOR}; }}
            QTabBar::tab {{ background: {BG_COLOR}; color: {MUTED_COLOR}; padding: 8px 18px; border-radius: 6px; margin: 2px; }}
            QTabBar::tab:selected {{ background: {ACCENT_COLOR}; color: #000; font-weight: bold; }}
        """)

        # --- Tab: Drop / Open ---
        drop_tab = QWidget()
        drop_layout = QVBoxLayout(drop_tab)
        drop_layout.setSpacing(10)

        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self._on_paths_received)
        drop_layout.addWidget(self.drop_area)

        btn_row = QHBoxLayout()
        self.btn_open_files = QPushButton("📂 Open File(s)")
        self.btn_open_folder = QPushButton("📁 Open Folder")
        self.btn_clear = QPushButton("🗑️ Clear")
        for btn in (self.btn_open_files, self.btn_open_folder, self.btn_clear):
            btn.setFixedHeight(36)
            btn.setStyleSheet(self._btn_style())
            btn_row.addWidget(btn)
        self.btn_open_files.clicked.connect(self._open_files)
        self.btn_open_folder.clicked.connect(self._open_folder)
        self.btn_clear.clicked.connect(self._clear_paths)
        drop_layout.addLayout(btn_row)

        self.lbl_selected = QLabel("Belum ada file dipilih")
        self.lbl_selected.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
        self.lbl_selected.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(self.lbl_selected)

        tabs.addTab(drop_tab, "🖼️ Pilih Gambar")

        # --- Tab: Settings ---
        settings_tab = QWidget()
        s_layout = QVBoxLayout(settings_tab)
        s_layout.setSpacing(12)
        s_layout.setAlignment(Qt.AlignTop)

        self.chk_headless = QCheckBox("Headless mode (browser tidak terlihat)")
        self.chk_headless.setChecked(self.config_manager.get_headless())
        self.chk_headless.setStyleSheet(f"color: {TEXT_COLOR};")
        self.chk_headless.toggled.connect(lambda v: self.config_manager.set_headless(v))
        s_layout.addWidget(self.chk_headless)

        self.chk_incognito = QCheckBox("Incognito mode")
        self.chk_incognito.setChecked(self.config_manager.get_incognito())
        self.chk_incognito.setStyleSheet(f"color: {TEXT_COLOR};")
        self.chk_incognito.toggled.connect(lambda v: self.config_manager.set_incognito(v))
        s_layout.addWidget(self.chk_incognito)

        batch_row = QHBoxLayout()
        batch_lbl = QLabel("Batch size (file diproses serentak):")
        batch_lbl.setStyleSheet(f"color: {TEXT_COLOR};")
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 5)
        self.spin_batch.setValue(self.config_manager.get_batch_size())
        self.spin_batch.setStyleSheet(f"background: {SURFACE_COLOR}; color: {TEXT_COLOR}; border: 1px solid #555; border-radius: 4px; padding: 4px;")
        self.spin_batch.valueChanged.connect(lambda v: self.config_manager.set_batch_size(v))
        batch_row.addWidget(batch_lbl)
        batch_row.addWidget(self.spin_batch)
        batch_row.addStretch()
        s_layout.addLayout(batch_row)

        note = QLabel("Output selalu PNG (transparan) — background dihapus.")
        note.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px; font-style: italic;")
        s_layout.addWidget(note)

        tabs.addTab(settings_tab, "⚙️ Pengaturan")
        main_layout.addWidget(tabs)

        # Action button
        self.btn_start = QPushButton("✂️  Hapus Background")
        self.btn_start.setFixedHeight(48)
        self.btn_start.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.btn_start.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT_COLOR};
                color: #000;
                border-radius: 10px;
            }}
            QPushButton:hover {{ background: #00FF6E; }}
            QPushButton:disabled {{ background: #444; color: #888; }}
        """)
        self.btn_start.clicked.connect(self._start_processing)
        main_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("⏹️  Stop")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setStyleSheet(f"""
            QPushButton {{
                background: #E53935;
                color: white;
                border-radius: 8px;
            }}
            QPushButton:hover {{ background: #FF5252; }}
            QPushButton:disabled {{ background: #444; color: #888; }}
        """)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_processing)
        main_layout.addWidget(self.btn_stop)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ border-radius: 9px; background: {SURFACE_COLOR}; text-align: center; color: {TEXT_COLOR}; }}
            QProgressBar::chunk {{ border-radius: 9px; background: {ACCENT_COLOR}; }}
        """)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Siap.")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(f"color: {MUTED_COLOR}; font-size: 11px;")
        main_layout.addWidget(self.lbl_status)

        # Log area
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        self.log_area.setStyleSheet(f"""
            QTextEdit {{
                background: {SURFACE_COLOR};
                color: #AAFFC3;
                font-family: Consolas, monospace;
                font-size: 10px;
                border-radius: 6px;
                border: 1px solid #333;
            }}
        """)
        main_layout.addWidget(self.log_area)

    def _btn_style(self):
        return f"""
            QPushButton {{
                background: {SURFACE_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid #555;
                border-radius: 8px;
                font-size: 12px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background: #3A3A5E; }}
        """

    def _on_paths_received(self, paths: List[str]):
        self.selected_paths.extend(paths)
        self.selected_paths = list(dict.fromkeys(self.selected_paths))  # dedupe
        count = len(self.selected_paths)
        self.lbl_selected.setText(f"{count} item dipilih")
        self._log(f"Ditambahkan: {', '.join(Path(p).name for p in paths)}")

    def _open_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Pilih Gambar", "",
            "Images (*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp *.avif)"
        )
        if files:
            self._on_paths_received(files)

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder")
        if folder:
            self._on_paths_received([folder])

    def _clear_paths(self):
        self.selected_paths = []
        self.lbl_selected.setText("Belum ada file dipilih")
        self._log("Daftar file dibersihkan.")

    def _log(self, message: str):
        self.log_area.append(message)
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def _start_processing(self):
        if not self.selected_paths:
            self.lbl_status.setText("⚠️ Pilih file atau folder terlebih dahulu!")
            return

        if self.worker_thread and self.worker_thread.isRunning():
            return

        driver_filename = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
        driver_path = os.path.join(self.base_dir, 'driver', driver_filename)

        self.progress_signal = ProgressSignal()
        self.progress_signal.progress.connect(self._on_progress)
        self.file_signal = FileUpdateSignal()
        self.file_signal.file_update.connect(self._on_file_update)

        self.processor = ImageProcessor(
            chromedriver_path=driver_path,
            progress_signal=self.progress_signal,
            file_update_signal=self.file_signal,
            config_manager=self.config_manager,
            headless=self.chk_headless.isChecked(),
            incognito=self.chk_incognito.isChecked(),
        )
        self.processor.batch_size = self.spin_batch.value()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Memproses...")
        self._log(f"Mulai proses {len(self.selected_paths)} item...")

        self.worker_thread = WorkerThread(self.processor, list(self.selected_paths))
        self.worker_thread.finished.connect(self._on_finished)
        self.worker_thread.error.connect(self._on_error)
        self.worker_thread.start()

    def _stop_processing(self):
        if self.processor:
            self.processor.stop_processing()
            self.lbl_status.setText("Dihentikan.")
            self._log("Proses dihentikan oleh user.")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_progress(self, message: str, percentage: int):
        self.progress_bar.setValue(percentage)
        self.lbl_status.setText(message)
        self._log(message)

    def _on_file_update(self, file_path: str, done: bool):
        if done:
            self._log("✅ Semua file selesai diproses.")
        elif file_path:
            self._log(f"⏳ Memproses: {Path(file_path).name}")

    def _on_finished(self):
        if self.processor:
            stats = self.processor.get_statistics()
            msg = f"✅ Selesai! Berhasil: {stats['total_processed']}, Gagal: {stats['total_failed']}"
            self.lbl_status.setText(msg)
            self._log(msg)
            self._log(f"Durasi: {stats['total_duration']:.1f} detik")
        self.progress_bar.setValue(100)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_error(self, error_msg: str):
        self.lbl_status.setText(f"❌ Error: {error_msg}")
        self._log(f"Error: {error_msg}")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def closeEvent(self, event):
        if self.processor:
            self.processor.stop_processing()
        event.accept()


def run_app(base_dir: str, icon_path: str = None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(base_dir, icon_path)
    window.show()
    sys.exit(app.exec())
