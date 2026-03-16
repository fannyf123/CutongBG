from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import tempfile
import re
import time
import requests
import os
import base64
from datetime import datetime
import glob
from pathlib import Path
from typing import List, Dict, Tuple, Callable
import threading
import sys
from PySide6.QtCore import QObject, Signal
from .logger import logger
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


def is_chrome_version_mismatch_exception(exc: Exception) -> bool:
    msg = str(exc) or ""
    if re.search(r"This version of ChromeDriver only supports Chrome version\s*\d+", msg):
        return True
    if re.search(r"Current browser version is\s*\d+\.\d+\.\d+\.\d+", msg):
        return True
    return False


def extract_chrome_version_from_error(exc: Exception) -> int:
    msg = str(exc) or ""
    match = re.search(r"Current browser version is\s*(\d+)", msg)
    if match:
        return int(match.group(1))
    match = re.search(r"This version of ChromeDriver only supports Chrome version\s*(\d+)", msg)
    if match:
        return int(match.group(1))
    return None


def attempt_chromedriver_fix(base_dir: str, chrome_major_version: int = None) -> bool:
    try:
        from .tools_checker import download_chromedriver_for_chrome_version
        return download_chromedriver_for_chrome_version(base_dir, chrome_major_version)
    except Exception as e:
        logger.kesalahan("Error saat mencoba memperbaiki ChromeDriver", str(e))
        return False


def initialize_chrome_driver_with_timeout(
    chromedriver_path: str, chrome_options,
    caps: dict = None, timeout: int = 30, max_retries: int = 3
) -> webdriver.Chrome:
    def _init_driver():
        try:
            if caps:
                driver = webdriver.Chrome(service=Service(chromedriver_path), desired_capabilities=caps)
            else:
                driver = webdriver.Chrome(service=Service(chromedriver_path), options=chrome_options)
        except TypeError:
            driver = webdriver.Chrome(service=Service(chromedriver_path), options=chrome_options)
        try:
            driver.chrome_pid = driver.service.process.pid
        except Exception:
            driver.chrome_pid = None
        return driver

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_init_driver)
                try:
                    driver = future.result(timeout=timeout)
                    return driver
                except FutureTimeoutError:
                    future.cancel()
                    last_error = TimeoutError(f"Chrome initialization timeout after {timeout}s")
                    if attempt < max_retries:
                        time.sleep(2)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        time.sleep(2)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2)

    error_msg = f"Gagal menginisialisasi Chrome setelah {max_retries} percobaan"
    if last_error:
        error_msg += f": {str(last_error)}"
    raise Exception(error_msg)


def compress_image_to_limit(image_path: str, max_size_mb: float = 10.0, output_path: str = None) -> str:
    try:
        from PIL import Image
        import io
    except ImportError:
        return image_path

    max_size_bytes = int(max_size_mb * 1024 * 1024)
    if os.path.getsize(image_path) <= max_size_bytes:
        return image_path

    img = Image.open(image_path)
    if img.mode in ('RGBA', 'LA', 'P'):
        bg = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(image_path),
            Path(image_path).stem + "_compressed.jpg"
        )

    min_q, max_q, best_q = 10, 95, 95
    while min_q <= max_q:
        mid_q = (min_q + max_q) // 2
        import io
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=mid_q, optimize=True)
        if buf.tell() <= max_size_bytes:
            best_q = mid_q
            min_q = mid_q + 1
        else:
            max_q = mid_q - 1

    img.save(output_path, format='JPEG', quality=best_q, optimize=True)
    return output_path


class ProgressSignal(QObject):
    progress = Signal(str, int)


class FileUpdateSignal(QObject):
    file_update = Signal(str, bool)


# ---------------------------------------------------------------------------
# JS helper: tunggu tombol download Picsart muncul, lalu ambil blob hasil
# ---------------------------------------------------------------------------
_JS_WAIT_AND_GRAB = """
const cb = arguments[0];

// ---- cari tombol download ----
function findDownloadBtn() {
    const selectors = [
        'a[data-testid="DownloadButton"]',
        'a[download]',
        'button[data-testid*="download" i]',
        'a[href*="blob:"]',
        'a[href*="picsart"][download]',
        'a[class*="download" i]',
        'button[class*="download" i]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) return el;
    }
    return null;
}

// ---- ambil URL gambar hasil dari elemen hasil (bukan input) ----
function findResultImageUrl() {
    // Prioritas 1: canvas (Picsart menggambar di canvas)
    const canvases = document.querySelectorAll('canvas');
    for (const c of canvases) {
        if (c.width > 50 && c.height > 50) {
            try { return c.toDataURL('image/png'); } catch(e) {}
        }
    }
    // Prioritas 2: img di dalam elemen result/preview setelah proses
    const resultSelectors = [
        '[data-testid="ResultImage"] img',
        '[data-testid="result-image"] img',
        '[class*="resultImage"] img',
        '[class*="result-image"] img',
        '[class*="ResultCanvas"] img',
        '[class*="preview"] img[src*="blob"]',
        '[class*="preview"] img[src*="data:"]',
    ];
    for (const sel of resultSelectors) {
        const img = document.querySelector(sel);
        if (img && img.src && img.src !== '' && !img.src.startsWith('data:image/gif')) {
            return img.src;
        }
    }
    // Prioritas 3: img blob yang bukan gambar asli (src berubah setelah upload)
    const allImgs = document.querySelectorAll('img[src^="blob:"]');
    if (allImgs.length > 0) {
        return allImgs[allImgs.length - 1].src;
    }
    return null;
}

// ---- tunggu download button muncul (max 90s) ----
let elapsed = 0;
const interval = setInterval(() => {
    elapsed += 500;
    const btn = findDownloadBtn();
    if (btn) {
        clearInterval(interval);
        // Coba ambil href dari tombol download
        const href = btn.href || btn.getAttribute('href') || '';
        if (href && (href.startsWith('blob:') || href.startsWith('http'))) {
            cb({url: href, source: 'button_href'});
            return;
        }
        // Fallback: ambil dari img/canvas
        const imgUrl = findResultImageUrl();
        cb({url: imgUrl, source: 'image_element'});
        return;
    }
    if (elapsed >= 90000) {
        clearInterval(interval);
        // Timeout: coba fallback ke canvas/img
        const imgUrl = findResultImageUrl();
        cb({url: imgUrl, source: 'timeout_fallback'});
    }
}, 500);
"""


class ImageProcessor:
    """
    Background Remover menggunakan Picsart Background Remover.
    URL target: https://picsart.com/background-remover/
    Output selalu PNG transparan.

    FIX v1.0.1:
    - Strategi deteksi hasil diganti: tunggu tombol Download Picsart muncul
      (bukan CSS selector generik), lalu ambil blob/canvas sebagai gambar hasil.
    - batch_size kini dibaca saat _process_files dijalankan, bukan saat __init__.
    """

    TARGET_URL = "https://picsart.com/background-remover/"
    OUTPUT_FOLDER = "BG_REMOVED"

    # Upload input selectors
    UPLOAD_SELECTORS = [
        "input[data-testid='input']",
        "input[accept*='image/jpeg']",
        "input[accept*='image/']",
        "div[class*='upload'] input[type='file']",
        "input[type='file']",
    ]

    def __init__(
        self,
        chromedriver_path: str = None,
        progress_callback: Callable = None,
        progress_signal: 'ProgressSignal' = None,
        file_update_signal: 'FileUpdateSignal' = None,
        config_manager=None,
        headless: bool | None = None,
        incognito: bool | None = None,
        batch_size: int = 1,
    ):
        if chromedriver_path:
            self.chromedriver_path = chromedriver_path
        else:
            driver_filename = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
            app_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = os.path.dirname(app_dir)
            self.chromedriver_path = os.path.join(base_dir, "driver", driver_filename)

        if not os.path.exists(self.chromedriver_path):
            raise FileNotFoundError(f"ChromeDriver tidak ditemukan di: {self.chromedriver_path}")

        if sys.platform != 'win32':
            import stat
            current_permissions = os.stat(self.chromedriver_path).st_mode
            if not (current_permissions & stat.S_IXUSR):
                os.chmod(
                    self.chromedriver_path,
                    current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )

        self.progress_callback = progress_callback
        self.progress_signal = progress_signal
        self.file_update_signal = file_update_signal
        self.should_stop = False
        self.processing_thread = None
        self.total_processed = 0
        self.total_failed = 0
        self.results = []
        self.start_time = None
        self.end_time = None
        self.polling_interval = 1
        self.config_manager = config_manager
        self.headless = headless
        self.incognito = incognito
        # FIX: batch_size diterima sebagai parameter constructor
        self.batch_size = max(1, int(batch_size or 1))
        self.actual_file_count = 0
        self.converted_files_to_cleanup = []
        self.chrome_init_timeout = 30
        self.chrome_init_retries = 3
        self.page_load_timeout = 60
        self.global_driver_tracker = []
        self.chrome_pids = []
        self.last_activity_time = time.time()

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def update_progress(
        self, message: str, percentage: int = None,
        current: int = None, total: int = None
    ):
        if current is not None and total is not None:
            message = f"{message} [{current}/{total}]"
        if self.progress_signal:
            self.progress_signal.progress.emit(message, percentage if percentage is not None else 0)
        elif self.progress_callback:
            self.progress_callback(message, percentage)
        try:
            self.last_activity_time = time.time()
        except Exception:
            pass
        if percentage in (0, 100) or (percentage is not None and percentage % 25 == 0):
            logger.info(message, f"{percentage}%" if percentage is not None else None)

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------
    def get_files_to_process(self, paths: List[str]) -> List[str]:
        all_files = []
        for path in paths:
            path_obj = Path(path)
            if path_obj.is_file() and self._is_image_file(path):
                all_files.append(str(path_obj))
            elif path_obj.is_dir():
                for ext in [
                    '*.jpg', '*.jpeg', '*.png', '*.bmp', '*.gif',
                    '*.tif', '*.tiff', '*.webp', '*.avif',
                    '*.ico', '*.pcx', '*.ppm', '*.sgi', '*.tga'
                ]:
                    all_files.extend(
                        glob.glob(os.path.join(path, '**', ext), recursive=True)
                    )
        return all_files

    def _is_image_file(self, file_path: str) -> bool:
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                _ = img.format
                return True
        except Exception:
            valid = [
                '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff',
                '.webp', '.avif', '.ico', '.pcx', '.ppm', '.sgi', '.tga'
            ]
            return Path(file_path).suffix.lower() in valid

    def _convert_to_standard_format(self, file_path: str) -> Tuple[str, bool]:
        try:
            from PIL import Image
        except ImportError:
            return file_path, False

        ext = Path(file_path).suffix.lower()
        if ext in ['.jpg', '.jpeg', '.png', '.gif']:
            return file_path, False

        try:
            img = Image.open(file_path)
            converted_path = os.path.join(
                os.path.dirname(file_path),
                Path(file_path).stem + "_converted.png"
            )
            if img.mode in ('RGBA', 'LA'):
                img.save(converted_path, format='PNG', optimize=True)
            elif img.mode == 'P':
                img.convert('RGBA').save(converted_path, format='PNG', optimize=True)
            elif img.mode in ('L', 'RGB'):
                img.save(converted_path, format='PNG', optimize=True)
            else:
                img.convert('RGB').save(converted_path, format='PNG', optimize=True)
            img.close()
            return converted_path, True
        except Exception as e:
            logger.kesalahan(f"Gagal konversi {os.path.basename(file_path)}", str(e))
            return file_path, False

    def _cleanup_converted_files(self):
        for fp in self.converted_files_to_cleanup:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
        self.converted_files_to_cleanup = []

    def _get_output_folder(self, file_path: str) -> str:
        return os.path.normpath(
            os.path.join(os.path.dirname(os.path.normpath(file_path)), self.OUTPUT_FOLDER)
        )

    def _get_base_name(self, file_path: str) -> str:
        return Path(file_path).stem

    # ------------------------------------------------------------------
    # Chrome options
    # ------------------------------------------------------------------
    def _build_chrome_options(self) -> Options:
        chrome_options = Options()
        if self.headless is True:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1366,768")
        chrome_options.add_argument("--log-level=3")
        if self.incognito:
            chrome_options.add_argument("--incognito")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--mute-audio")
        chrome_options.add_experimental_option(
            'excludeSwitches', ['enable-logging', 'enable-automation']
        )
        chrome_options.add_experimental_option('useAutomationExtension', False)
        return chrome_options

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_processing(self, paths: List[str]):
        self.should_stop = False
        self.total_processed = 0
        self.total_failed = 0
        self.results = []
        self.start_time = datetime.now()
        self.converted_files_to_cleanup = []
        self.global_driver_tracker.clear()

        files_to_process = self.get_files_to_process(paths)
        if not files_to_process:
            self.update_progress("Tidak ada file gambar ditemukan", 100)
            return

        converted = []
        for fp in files_to_process:
            cp, was = self._convert_to_standard_format(fp)
            converted.append(cp)
            if was:
                self.converted_files_to_cleanup.append(cp)
        files_to_process = converted
        self.actual_file_count = len(files_to_process)

        logger.info(f"Mulai remove background {len(files_to_process)} file (batch_size={self.batch_size})")
        self.processing_thread = threading.Thread(
            target=self._process_files, args=(files_to_process,)
        )
        self.processing_thread.daemon = True
        self.processing_thread.start()

    def stop_processing(self):
        if self.processing_thread and self.processing_thread.is_alive():
            self.should_stop = True
            for driver in self.global_driver_tracker:
                try:
                    if driver:
                        driver.quit()
                except Exception:
                    pass
            self.global_driver_tracker.clear()
            self.processing_thread.join(10)
            self.should_stop = False
            self._cleanup_converted_files()

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------
    def _process_files(self, files: List[str]):
        total_files = len(files)
        # FIX: baca self.batch_size (sudah di-set via constructor)
        batch_size = max(1, min(self.batch_size, 10))
        logger.info(f"Memproses {total_files} file, batch_size={batch_size}")

        for start in range(0, total_files, batch_size):
            if self.should_stop:
                break

            chunk = files[start:start + batch_size]
            drivers = [None] * len(chunk)
            chunk_results = [None] * len(chunk)
            batch_driver_tracker = []
            start_times = [datetime.now() for _ in chunk]

            # --- Buka browser untuk setiap file di chunk ---
            for idx, file_path in enumerate(chunk):
                if self.should_stop:
                    break

                current_num = start + idx + 1
                if self.file_update_signal:
                    self.file_update_signal.file_update.emit(file_path, False)

                self.update_progress(
                    f"Membuka browser",
                    percentage=int((start + idx) / total_files * 100),
                    current=current_num, total=total_files
                )

                try:
                    chrome_options = self._build_chrome_options()
                    driver = initialize_chrome_driver_with_timeout(
                        chromedriver_path=self.chromedriver_path,
                        chrome_options=chrome_options,
                        timeout=self.chrome_init_timeout,
                        max_retries=self.chrome_init_retries
                    )
                    drivers[idx] = driver
                    batch_driver_tracker.append(driver)
                    self.global_driver_tracker.append(driver)

                    # Set async script timeout lebih panjang (untuk JS polling)
                    driver.set_script_timeout(100)
                    driver.set_page_load_timeout(self.page_load_timeout)

                    url_loaded = False
                    for url_attempt in range(1, 4):
                        try:
                            driver.get(self.TARGET_URL)
                            url_loaded = True
                            break
                        except Exception as nav_err:
                            logger.peringatan(f"Nav percobaan {url_attempt}/3: {str(nav_err)[:60]}")
                            if url_attempt < 3:
                                time.sleep(2)

                    if not url_loaded:
                        driver.quit()
                        drivers[idx] = None
                        chunk_results[idx] = self._make_error_result(
                            file_path, start_times[idx], "Timeout membuka Picsart"
                        )
                        continue

                except Exception as e:
                    logger.kesalahan("Gagal init browser", str(e))
                    if is_chrome_version_mismatch_exception(e):
                        chrome_ver = extract_chrome_version_from_error(e)
                        app_dir = os.path.dirname(os.path.abspath(__file__))
                        base_dir = os.path.dirname(app_dir)
                        attempt_chromedriver_fix(base_dir, chrome_ver)
                    chunk_results[idx] = self._make_error_result(file_path, start_times[idx], str(e))
                    drivers[idx] = None

            # --- Tunggu upload input tersedia ---
            page_ready_start = time.time()
            while not self.should_stop:
                if time.time() - page_ready_start > 60:
                    break
                all_ready = True
                for d in drivers:
                    if d is None:
                        continue
                    try:
                        ready = d.execute_script("return document.readyState")
                        found = any(
                            d.find_elements(By.CSS_SELECTOR, sel)
                            for sel in self.UPLOAD_SELECTORS
                        )
                        if not (ready == 'complete' and found):
                            all_ready = False
                            break
                    except Exception:
                        all_ready = False
                        break
                if all_ready:
                    break
                time.sleep(self.polling_interval)

            # --- Upload file ---
            for idx, d in enumerate(drivers):
                if d is None or self.should_stop:
                    continue

                file_path = chunk[idx]
                input_el = None
                for sel in self.UPLOAD_SELECTORS:
                    try:
                        els = d.find_elements(By.CSS_SELECTOR, sel)
                        if els:
                            input_el = els[0]
                            break
                    except Exception:
                        continue

                if not input_el:
                    try:
                        input_el = d.execute_script(
                            "return document.querySelector('input[type=\"file\"]');"
                        )
                    except Exception:
                        pass

                if not input_el:
                    chunk_results[idx] = self._make_error_result(
                        file_path, start_times[idx], "Elemen upload tidak ditemukan"
                    )
                    try:
                        d.quit()
                    except Exception:
                        pass
                    drivers[idx] = None
                    continue

                # Kompres jika > 10 MB
                upload_path = file_path
                try:
                    if os.path.getsize(file_path) > 10 * 1024 * 1024:
                        upload_path = compress_image_to_limit(file_path, max_size_mb=9.5)
                except Exception:
                    pass

                try:
                    input_el.send_keys(upload_path)
                    self.update_progress(
                        f"Mengupload gambar ke Picsart",
                        percentage=int((start + idx) / total_files * 100),
                        current=start + idx + 1, total=total_files
                    )
                    time.sleep(1)
                except Exception as e:
                    chunk_results[idx] = self._make_error_result(
                        file_path, start_times[idx], f"Gagal upload: {e}"
                    )
                    try:
                        d.quit()
                    except Exception:
                        pass
                    drivers[idx] = None

            # --- Tunggu & ambil hasil via JS async ---
            # FIX UTAMA: pakai JS polling yang menunggu tombol Download Picsart
            # muncul, lalu mengambil blob/canvas sebagai gambar hasil yang benar
            pending = sum(1 for d in drivers if d is not None)
            max_wait = 150
            if self.config_manager and hasattr(self.config_manager, 'get_max_wait_seconds'):
                try:
                    max_wait = int(self.config_manager.get_max_wait_seconds())
                except Exception:
                    pass

            # Jalankan JS async untuk setiap driver
            js_futures: dict = {}  # idx -> {'thread': Thread, 'result': None}

            def _run_js(driver_ref, idx_ref, result_store):
                """Jalankan _JS_WAIT_AND_GRAB di thread terpisah agar tidak block batch."""
                try:
                    res = driver_ref.execute_async_script(_JS_WAIT_AND_GRAB)
                    result_store[idx_ref] = res
                except Exception as ex:
                    result_store[idx_ref] = {'url': None, 'source': f'js_error: {ex}'}

            js_results = {}
            js_threads = {}
            for idx, d in enumerate(drivers):
                if d is None or chunk_results[idx] is not None:
                    continue
                t = threading.Thread(
                    target=_run_js, args=(d, idx, js_results), daemon=True
                )
                t.start()
                js_threads[idx] = t

            # Tunggu semua thread JS selesai (dengan batas waktu)
            deadline = time.time() + max_wait
            while js_threads and not self.should_stop:
                if time.time() > deadline:
                    logger.kesalahan("Timeout menunggu hasil JS dari Picsart")
                    break
                done = [idx for idx, t in js_threads.items() if not t.is_alive()]
                for idx in done:
                    js_threads.pop(idx)
                if js_threads:
                    time.sleep(1)
                    self.last_activity_time = time.time()

            # --- Proses hasil ---
            for idx, d in enumerate(drivers):
                if d is None or chunk_results[idx] is not None:
                    continue

                file_path = chunk[idx]
                js_res = js_results.get(idx, {})
                image_url = js_res.get('url') if js_res else None
                source = js_res.get('source', 'unknown') if js_res else 'no_result'

                if not image_url:
                    chunk_results[idx] = self._make_error_result(
                        file_path, start_times[idx],
                        f"Gambar hasil tidak ditemukan (source={source})"
                    )
                    try:
                        d.quit()
                    except Exception:
                        pass
                    drivers[idx] = None
                    pending -= 1
                    continue

                logger.info(f"Hasil ditemukan via {source}: {image_url[:80]}")

                try:
                    data_bytes = None

                    if image_url.startswith('data:'):
                        # Canvas toDataURL hasil
                        _, b64 = image_url.split(',', 1)
                        data_bytes = base64.b64decode(b64)

                    elif image_url.startswith('blob:'):
                        # Blob URL - fetch via JS
                        data_url = d.execute_async_script("""
                            const blobUrl = arguments[0];
                            const cb = arguments[1];
                            fetch(blobUrl)
                                .then(r => r.blob())
                                .then(b => {
                                    const fr = new FileReader();
                                    fr.onload = () => cb(fr.result);
                                    fr.onerror = () => cb(null);
                                    fr.readAsDataURL(b);
                                })
                                .catch(() => cb(null));
                        """, image_url)
                        if data_url and ',' in data_url:
                            _, b64 = data_url.split(',', 1)
                            data_bytes = base64.b64decode(b64)

                    elif image_url.startswith('http'):
                        # URL biasa - download langsung
                        cookies = {c['name']: c['value'] for c in d.get_cookies()}
                        resp = requests.get(
                            image_url, stream=True,
                            cookies=cookies,
                            headers={'Referer': self.TARGET_URL},
                            timeout=60
                        )
                        if resp.status_code == 200:
                            data_bytes = resp.content
                        else:
                            raise ValueError(f"HTTP {resp.status_code} saat download")

                    if not data_bytes or len(data_bytes) < 1000:
                        # Data terlalu kecil = bukan gambar yang valid
                        chunk_results[idx] = self._make_error_result(
                            file_path, start_times[idx],
                            "Data hasil terlalu kecil, mungkin bukan gambar yang benar"
                        )
                        try:
                            d.quit()
                        except Exception:
                            pass
                        drivers[idx] = None
                        pending -= 1
                        continue

                    # Simpan sebagai PNG
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    base_name = self._get_base_name(file_path)
                    output_folder = self._get_output_folder(file_path)
                    os.makedirs(output_folder, exist_ok=True)
                    output_path = os.path.join(
                        output_folder, f"{base_name}_nobg_{timestamp}.png"
                    )

                    # Konversi ke PNG via Pillow untuk memastikan format benar
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(data_bytes))
                        img.save(output_path, format='PNG')
                    except Exception:
                        # Fallback: simpan langsung
                        with open(output_path, 'wb') as f:
                            f.write(data_bytes)

                    current_num = start + idx + 1
                    chunk_results[idx] = {
                        "file_path": file_path,
                        "success": True,
                        "output_path": output_path,
                        "error": None,
                        "start_time": start_times[idx],
                        "end_time": datetime.now(),
                        "duration": (datetime.now() - start_times[idx]).total_seconds()
                    }
                    self.last_activity_time = time.time()
                    self.update_progress(
                        f"Background berhasil dihapus: {Path(output_path).name}",
                        percentage=int(current_num / total_files * 100),
                        current=current_num, total=total_files
                    )

                except Exception as e:
                    chunk_results[idx] = self._make_error_result(
                        file_path, start_times[idx], str(e)
                    )

                finally:
                    try:
                        if drivers[idx]:
                            drivers[idx].quit()
                    except Exception:
                        pass
                    drivers[idx] = None
                    pending -= 1

            # --- Kumpulkan hasil batch ---
            for idx, file_path in enumerate(chunk):
                res = chunk_results[idx] or self._make_error_result(
                    file_path, start_times[idx], "Unknown error"
                )
                self.results.append(res)
                if res.get("success"):
                    self.total_processed += 1
                else:
                    self.total_failed += 1

            for d in batch_driver_tracker:
                if d in self.global_driver_tracker:
                    self.global_driver_tracker.remove(d)
            batch_driver_tracker.clear()

            if sys.platform == 'win32':
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/IM', 'chromedriver.exe'],
                        capture_output=True, timeout=1,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                except Exception:
                    pass

            import gc
            gc.collect()

            if start + batch_size < total_files and not self.should_stop:
                time.sleep(0.5)

        # --- Selesai ---
        self.end_time = datetime.now()
        if self.file_update_signal:
            self.file_update_signal.file_update.emit("", True)
        self.update_progress(
            f"Selesai! Berhasil: {self.total_processed}, Gagal: {self.total_failed}",
            percentage=100
        )
        self._cleanup_converted_files()
        logger.sukses(
            f"Remove background selesai. Berhasil: {self.total_processed}, Gagal: {self.total_failed}"
        )

    def _make_error_result(self, file_path: str, start_time: datetime, error: str) -> Dict:
        return {
            "file_path": file_path,
            "success": False,
            "output_path": None,
            "error": error,
            "start_time": start_time,
            "end_time": datetime.now(),
            "duration": (datetime.now() - start_time).total_seconds()
        }

    def get_statistics(self) -> Dict:
        duration = (
            (self.end_time - self.start_time).total_seconds()
            if self.start_time and self.end_time else 0
        )
        return {
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "total_duration": duration,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "results": self.results,
        }
