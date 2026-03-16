from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException
import tempfile
import re
import time
import requests
import os
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


def initialize_chrome_driver_with_timeout(chromedriver_path: str, chrome_options, caps: dict = None, timeout: int = 30, max_retries: int = 3) -> webdriver.Chrome:
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
        except:
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
        output_path = os.path.join(os.path.dirname(image_path), Path(image_path).stem + "_compressed.jpg")

    min_q, max_q, best_q = 10, 95, 95
    while min_q <= max_q:
        mid_q = (min_q + max_q) // 2
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


class ImageProcessor:
    """
    Background Remover menggunakan Picsart Background Remover.
    URL target: https://picsart.com/background-remover/
    Output selalu PNG (transparan).
    """

    # Picsart Background Remover URL
    TARGET_URL = "https://picsart.com/background-remover/"
    # Output folder name
    OUTPUT_FOLDER = "BG_REMOVED"

    # CSS selectors for upload input on Picsart BG Remover page
    UPLOAD_SELECTORS = [
        "div[id='uploadArea'] input[type='file']",
        "div[id='uploadArea'] input",
        "div[class*='upload-area-root'] input[type='file']",
        "div[class*='upload-area'] input[type='file']",
        "div[class*='upload-area'] input",
        "input[data-testid='input']",
        "input[accept*='image/jpeg']",
        "input[accept*='image/']",
        "input[type='file']",
    ]

    # CSS selectors for result image on Picsart BG Remover page
    RESULT_SELECTORS = [
        'div[data-testid="ResultImage"] img',
        'div[data-testid="result"] img',
        'div[class*="result"] img[src]',
        'img[alt*="result"]',
        'img[alt*="removed"]',
        'img[alt*="background"]',
        'div[data-testid="ResultImage"] picture img',
        'div[class*="ResultImage"] img',
        'div[class*="removedBg"] img',
        # Generic fallback: any img loaded after upload
        'img[src^="https://cdn"]',
        'img[src*="picsart"]',
    ]

    def __init__(self, chromedriver_path: str = None, progress_callback: Callable = None,
                 progress_signal: ProgressSignal = None, file_update_signal: FileUpdateSignal = None,
                 config_manager=None, headless: bool | None = None, incognito: bool | None = None):
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
                os.chmod(self.chromedriver_path, current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

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
        self.batch_size = 1
        self.actual_file_count = 0
        self.converted_files_to_cleanup = []
        self.chrome_init_timeout = 30
        self.chrome_init_retries = 3
        self.page_load_timeout = 30
        self.global_driver_tracker = []
        self.chrome_pids = []
        import time as _time
        self.last_activity_time = _time.time()

    def update_progress(self, message: str, percentage: int = None, current: int = None, total: int = None):
        if current is not None and total is not None:
            message = f"{message} [{current}/{total}]"
        if self.progress_signal:
            self.progress_signal.progress.emit(message, percentage if percentage is not None else 0)
        elif self.progress_callback:
            self.progress_callback(message, percentage)
        try:
            import time as _time
            self.last_activity_time = _time.time()
        except Exception:
            pass
        if percentage in (0, 100) or (percentage is not None and percentage % 25 == 0):
            logger.info(message, f"{percentage}%" if percentage is not None else None)

    def get_files_to_process(self, paths: List[str]) -> List[str]:
        all_files = []
        for path in paths:
            path_obj = Path(path)
            if path_obj.is_file() and self._is_image_file(path):
                all_files.append(str(path_obj))
            elif path_obj.is_dir():
                for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.gif', '*.tif', '*.tiff',
                            '*.webp', '*.avif', '*.ico', '*.pcx', '*.ppm', '*.sgi', '*.tga']:
                    all_files.extend(glob.glob(os.path.join(path, '**', ext), recursive=True))
        return all_files

    def _is_image_file(self, file_path: str) -> bool:
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                _ = img.format
                return True
        except Exception:
            valid_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff',
                                '.webp', '.avif', '.ico', '.pcx', '.ppm', '.sgi', '.tga']
            return Path(file_path).suffix.lower() in valid_extensions

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
            output_dir = os.path.dirname(file_path)
            base_name = Path(file_path).stem
            converted_path = os.path.join(output_dir, f"{base_name}_converted.png")
            if img.mode in ('RGBA', 'LA'):
                img.save(converted_path, format='PNG', optimize=True)
            elif img.mode == 'P':
                img = img.convert('RGBA')
                img.save(converted_path, format='PNG', optimize=True)
            elif img.mode in ('L', 'RGB'):
                img.save(converted_path, format='PNG', optimize=True)
            else:
                img = img.convert('RGB')
                img.save(converted_path, format='PNG', optimize=True)
            img.close()
            return converted_path, True
        except Exception as e:
            logger.kesalahan(f"Gagal konversi {os.path.basename(file_path)}", str(e))
            return file_path, False

    def _cleanup_converted_files(self):
        for file_path in self.converted_files_to_cleanup:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        self.converted_files_to_cleanup = []

    def _get_output_folder(self, file_path: str) -> str:
        file_dir = os.path.dirname(os.path.normpath(file_path))
        return os.path.normpath(os.path.join(file_dir, self.OUTPUT_FOLDER))

    def _get_base_name(self, file_path: str) -> str:
        return Path(file_path).stem

    def _build_chrome_options(self) -> Options:
        chrome_options = Options()
        if self.headless is True:
            try:
                chrome_options.add_argument("--headless=new")
            except Exception:
                chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1366,768")
        chrome_options.add_argument("--log-level=3")
        if self.incognito:
            chrome_options.add_argument("--incognito")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--metrics-recording-only")
        chrome_options.add_argument("--mute-audio")
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        return chrome_options

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

        converted_files = []
        for file_path in files_to_process:
            converted_path, was_converted = self._convert_to_standard_format(file_path)
            converted_files.append(converted_path)
            if was_converted:
                self.converted_files_to_cleanup.append(converted_path)
        files_to_process = converted_files
        self.actual_file_count = len(files_to_process)

        logger.info(f"Mulai proses remove background untuk {len(files_to_process)} file")
        self.processing_thread = threading.Thread(
            target=self._process_files,
            args=(files_to_process,)
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

    def _process_files(self, files: List[str]):
        total_files = len(files)
        batch_size = max(1, min(int(getattr(self, 'batch_size', 1) or 1), 20))
        logger.info(f"Memproses {total_files} file (batch_size={batch_size})")

        for start in range(0, total_files, batch_size):
            if self.should_stop:
                break

            chunk = files[start:start + batch_size]
            drivers = [None] * len(chunk)
            chunk_results = [None] * len(chunk)
            batch_driver_tracker = []
            start_times = [datetime.now() for _ in chunk]

            for idx, file_path in enumerate(chunk):
                if self.should_stop:
                    break

                current_num = start + idx + 1
                if self.file_update_signal:
                    self.file_update_signal.file_update.emit(file_path, False)

                self.update_progress(
                    f"Membuka browser untuk file",
                    percentage=int((start + idx) / total_files * 100),
                    current=current_num, total=total_files
                )

                try:
                    chrome_options = self._build_chrome_options()
                    try:
                        caps = chrome_options.to_capabilities() or {}
                    except Exception:
                        caps = {}

                    driver = initialize_chrome_driver_with_timeout(
                        chromedriver_path=self.chromedriver_path,
                        chrome_options=chrome_options,
                        caps=caps,
                        timeout=self.chrome_init_timeout,
                        max_retries=self.chrome_init_retries
                    )
                    drivers[idx] = driver
                    batch_driver_tracker.append(driver)
                    self.global_driver_tracker.append(driver)

                    # Navigate to Picsart Background Remover
                    url_loaded = False
                    for url_attempt in range(1, 4):
                        try:
                            driver.set_page_load_timeout(self.page_load_timeout)
                            driver.get(self.TARGET_URL)
                            url_loaded = True
                            break
                        except Exception as nav_error:
                            logger.peringatan(f"Percobaan {url_attempt}/3 gagal: {str(nav_error)[:80]}")
                            if url_attempt < 3:
                                time.sleep(2)

                    if not url_loaded:
                        driver.quit()
                        drivers[idx] = None
                        chunk_results[idx] = self._make_error_result(file_path, start_times[idx], "Timeout membuka halaman Picsart BG Remover")
                        continue

                except Exception as e:
                    logger.kesalahan(f"Gagal init browser", str(e))
                    if is_chrome_version_mismatch_exception(e):
                        chrome_ver = extract_chrome_version_from_error(e)
                        app_dir = os.path.dirname(os.path.abspath(__file__))
                        base_dir = os.path.dirname(app_dir)
                        attempt_chromedriver_fix(base_dir, chrome_ver)
                    chunk_results[idx] = self._make_error_result(file_path, start_times[idx], str(e))
                    drivers[idx] = None

            # Wait for pages to be ready (upload area visible)
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
                        found = False
                        for sel in self.UPLOAD_SELECTORS:
                            elems = d.find_elements(By.CSS_SELECTOR, sel)
                            if elems:
                                found = True
                                break
                        if not (ready == 'complete' and found):
                            all_ready = False
                            break
                    except Exception:
                        all_ready = False
                        break
                if all_ready:
                    break
                time.sleep(self.polling_interval)

            # Upload files
            for idx, d in enumerate(drivers):
                if d is None or self.should_stop:
                    continue

                file_path = chunk[idx]
                input_file = None
                for selector in self.UPLOAD_SELECTORS:
                    try:
                        elems = d.find_elements(By.CSS_SELECTOR, selector)
                        if elems:
                            input_file = elems[0]
                            break
                    except Exception:
                        continue

                if not input_file:
                    try:
                        input_file = d.execute_script(
                            "return document.querySelector('input[type=\'file\']') || "
                            "document.querySelector('input[data-testid=\'input\']');"
                        )
                    except Exception:
                        pass

                if not input_file:
                    chunk_results[idx] = self._make_error_result(file_path, start_times[idx], "Elemen upload tidak ditemukan")
                    try:
                        d.quit()
                    except Exception:
                        pass
                    drivers[idx] = None
                    continue

                # Compress if needed
                upload_path = file_path
                if os.path.getsize(file_path) / (1024 * 1024) > 10.0:
                    try:
                        upload_path = compress_image_to_limit(file_path, max_size_mb=9.5)
                    except Exception:
                        pass

                try:
                    input_file.send_keys(upload_path)
                    time.sleep(self.polling_interval)
                except Exception as e:
                    chunk_results[idx] = self._make_error_result(file_path, start_times[idx], f"Gagal upload: {e}")
                    try:
                        d.quit()
                    except Exception:
                        pass
                    drivers[idx] = None

            # Wait for result images
            pending = sum(1 for d in drivers if d is not None)
            max_wait = 120
            if self.config_manager and hasattr(self.config_manager, 'get_max_wait_seconds'):
                try:
                    max_wait = int(self.config_manager.get_max_wait_seconds())
                except Exception:
                    pass

            wait_start = time.time()
            while pending > 0 and not self.should_stop:
                if time.time() - wait_start > max_wait:
                    logger.kesalahan("Timeout menunggu hasil remove background")
                    for j, d in enumerate(drivers):
                        if d is not None:
                            chunk_results[j] = self._make_error_result(chunk[j], start_times[j], "Timeout menunggu hasil")
                            try:
                                d.quit()
                            except Exception:
                                pass
                            drivers[j] = None
                            pending -= 1
                    break

                # Detect hang
                hang_timeout = 300
                if self.config_manager and hasattr(self.config_manager, 'get_processing_hang_timeout'):
                    try:
                        hang_timeout = int(self.config_manager.get_processing_hang_timeout())
                    except Exception:
                        pass
                if time.time() - self.last_activity_time > hang_timeout:
                    logger.kesalahan("Hang terdeteksi, menghentikan batch")
                    for j, d in enumerate(drivers):
                        if d is not None:
                            chunk_results[j] = self._make_error_result(chunk[j], start_times[j], "Timeout: tidak ada aktivitas")
                            try:
                                d.quit()
                            except Exception:
                                pass
                            drivers[j] = None
                            pending -= 1
                    break

                for idx, d in enumerate(drivers):
                    if d is None or chunk_results[idx] is not None:
                        continue

                    file_path = chunk[idx]

                    # Check for result image
                    image_url = None
                    for selector in self.RESULT_SELECTORS:
                        try:
                            imgs = d.execute_script(f"return document.querySelectorAll('{selector}');")
                            if imgs:
                                for img in imgs:
                                    try:
                                        src = img.get_attribute('src')
                                    except Exception:
                                        try:
                                            src = d.execute_script('return arguments[0].getAttribute("src");', img)
                                        except Exception:
                                            src = None
                                    if src and ('http' in src or src.startswith('blob:') or src.startswith('data:')):
                                        image_url = src
                                        break
                            if image_url:
                                break
                        except Exception:
                            continue

                    if not image_url:
                        continue

                    # Download result
                    try:
                        data_bytes = None
                        if image_url.startswith('http'):
                            resp = requests.get(image_url, stream=True)
                            if resp.status_code != 200:
                                chunk_results[idx] = self._make_error_result(file_path, start_times[idx], f"Download gagal: HTTP {resp.status_code}")
                                try:
                                    d.quit()
                                except Exception:
                                    pass
                                drivers[idx] = None
                                pending -= 1
                                continue
                            data_bytes = resp.content
                        elif image_url.startswith('blob:'):
                            data_url = d.execute_async_script("""
                                const blobUrl = arguments[0];
                                const cb = arguments[1];
                                fetch(blobUrl).then(r=>r.blob()).then(b=>{
                                    const fr = new FileReader();
                                    fr.onload=()=>cb(fr.result);
                                    fr.onerror=()=>cb(null);
                                    fr.readAsDataURL(b);
                                }).catch(()=>cb(null));
                            """, image_url)
                            if data_url:
                                import base64
                                _, b64 = data_url.split(',', 1)
                                data_bytes = base64.b64decode(b64)
                        elif image_url.startswith('data:'):
                            import base64
                            _, b64 = image_url.split(',', 1)
                            data_bytes = base64.b64decode(b64)

                        if not data_bytes:
                            continue

                        # Save as PNG (always - to preserve transparency)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        base_name = self._get_base_name(file_path)
                        output_folder = self._get_output_folder(file_path)
                        os.makedirs(output_folder, exist_ok=True)
                        output_path = os.path.join(output_folder, f"{base_name}_nobg_{timestamp}.png")

                        with open(output_path, 'wb') as f:
                            f.write(data_bytes)

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
                        current_num = start + idx + 1
                        self.update_progress(
                            f"Background berhasil dihapus: {Path(output_path).name}",
                            percentage=int(current_num / total_files * 100),
                            current=current_num, total=total_files
                        )
                        try:
                            d.quit()
                        except Exception:
                            pass
                        drivers[idx] = None
                        pending -= 1

                    except Exception as e:
                        chunk_results[idx] = self._make_error_result(file_path, start_times[idx], str(e))
                        try:
                            d.quit()
                        except Exception:
                            pass
                        drivers[idx] = None
                        pending -= 1

                if pending > 0:
                    time.sleep(self.polling_interval)

            # Collect results
            for idx, file_path in enumerate(chunk):
                res = chunk_results[idx]
                if res is None:
                    res = self._make_error_result(file_path, start_times[idx], "Unknown error")
                self.results.append(res)
                if res.get("success"):
                    self.total_processed += 1
                else:
                    self.total_failed += 1

            # Cleanup batch drivers
            for d in batch_driver_tracker:
                if d in self.global_driver_tracker:
                    self.global_driver_tracker.remove(d)
            batch_driver_tracker.clear()

            # Kill chromedriver processes
            if sys.platform == 'win32':
                try:
                    subprocess.run(['taskkill', '/F', '/IM', 'chromedriver.exe'],
                                   capture_output=True, timeout=1,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception:
                    pass

            import gc
            gc.collect()

            if start + batch_size < total_files:
                time.sleep(0.5)

        # Done
        self.end_time = datetime.now()
        if self.file_update_signal:
            self.file_update_signal.file_update.emit("", True)
        self.update_progress(
            f"Selesai! Berhasil: {self.total_processed}, Gagal: {self.total_failed}",
            percentage=100
        )
        self._cleanup_converted_files()
        logger.sukses(f"Remove background selesai. Berhasil: {self.total_processed}, Gagal: {self.total_failed}")

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
        duration = (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else 0
        return {
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "total_duration": duration,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "results": self.results,
        }
