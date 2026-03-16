import os
import sys
import platform
import requests
import zipfile
import shutil
import re
import subprocess
from pathlib import Path

from .logger import logger


def get_chrome_version_windows() -> int:
    try:
        result = subprocess.run(
            ['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            match = re.search(r'(\d+)\.\d+\.\d+\.\d+', result.stdout)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    try:
        result = subprocess.run(
            ['reg', 'query', r'HKEY_LOCAL_MACHINE\SOFTWARE\Google\Chrome\BLBeacon', '/v', 'version'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            match = re.search(r'(\d+)\.\d+\.\d+\.\d+', result.stdout)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return None


def get_platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'windows':
        return 'win64' if machine in ('amd64', 'x86_64', 'x64') else 'win32'
    elif system == 'darwin':
        return 'mac-arm64' if machine == 'arm64' else 'mac-x64'
    elif system == 'linux':
        return 'linux64'
    return 'win64'


def get_chromedriver_download_url(platform_key: str, chrome_major: int = None) -> str:
    if chrome_major:
        try:
            url = f"https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            versions = data.get('versions', [])
            matched = [v for v in versions if v.get('version', '').startswith(str(chrome_major) + '.')]
            if matched:
                latest = matched[-1]
                downloads = latest.get('downloads', {}).get('chromedriver', [])
                for d in downloads:
                    if d.get('platform') == platform_key:
                        return d.get('url')
        except Exception as e:
            logger.peringatan(f"Gagal mendapatkan URL via version lookup: {e}")

    # Fallback: stable channel
    try:
        PAGE_URL = 'https://googlechromelabs.github.io/chrome-for-testing/'
        resp = requests.get(PAGE_URL, timeout=10)
        resp.raise_for_status()
        html = resp.text
        stable_start = html.find('id="stable"')
        if stable_start == -1:
            stable_start = html.find("id=stable")
        sec_open = html.rfind('<section', 0, stable_start)
        sec_close = html.find('</section>', stable_start)
        stable_html = html[sec_open:sec_close]
        pattern = re.compile(
            rf'https://storage\.googleapis\.com/[A-Za-z0-9_\-./]*/[0-9\.]+/{re.escape(platform_key)}/chromedriver-{re.escape(platform_key)}\.zip'
        )
        m = pattern.search(stable_html)
        if m:
            return m.group(0)
    except Exception as e:
        logger.peringatan(f"Gagal mendapatkan URL stable: {e}")

    return None


def download_chromedriver_for_chrome_version(base_dir: str, chrome_major: int = None) -> bool:
    platform_key = get_platform_key()
    url = get_chromedriver_download_url(platform_key, chrome_major)
    if not url:
        logger.kesalahan("Tidak dapat menemukan URL download ChromeDriver")
        return False
    return _download_and_install_chromedriver(base_dir, url, platform_key)


def _download_and_install_chromedriver(base_dir: str, url: str, platform_key: str) -> bool:
    try:
        logger.info(f"Mengunduh ChromeDriver dari: {url}")
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()

        temp_zip = os.path.join(base_dir, "chromedriver_temp.zip")
        with open(temp_zip, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

        temp_extract = os.path.join(base_dir, "chromedriver_temp_extract")
        if os.path.exists(temp_extract):
            shutil.rmtree(temp_extract)
        os.makedirs(temp_extract)

        with zipfile.ZipFile(temp_zip, 'r') as zf:
            zf.extractall(temp_extract)

        driver_filename = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
        driver_dir = os.path.join(base_dir, 'driver')
        os.makedirs(driver_dir, exist_ok=True)
        dest = os.path.join(driver_dir, driver_filename)

        found = None
        folder_name = f"chromedriver-{platform_key}"
        candidate = os.path.join(temp_extract, folder_name, driver_filename)
        if os.path.exists(candidate):
            found = candidate
        else:
            for root, dirs, files in os.walk(temp_extract):
                if driver_filename in files:
                    found = os.path.join(root, driver_filename)
                    break

        if not found:
            logger.kesalahan("ChromeDriver executable tidak ditemukan dalam ZIP")
            return False

        shutil.copy2(found, dest)
        if sys.platform != 'win32':
            import stat
            os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        shutil.rmtree(temp_extract, ignore_errors=True)
        os.remove(temp_zip)

        logger.sukses(f"ChromeDriver berhasil diinstall ke: {dest}")
        return True
    except Exception as e:
        logger.kesalahan(f"Gagal download/install ChromeDriver", str(e))
        return False


def check_tools(base_dir: str) -> bool:
    driver_filename = 'chromedriver.exe' if sys.platform == 'win32' else 'chromedriver'
    driver_path = os.path.join(base_dir, 'driver', driver_filename)

    if os.path.exists(driver_path):
        logger.info(f"ChromeDriver ditemukan: {driver_path}")
        return True

    logger.info("ChromeDriver tidak ditemukan, mencoba download otomatis...")
    platform_key = get_platform_key()

    chrome_major = None
    if sys.platform == 'win32':
        chrome_major = get_chrome_version_windows()
        if chrome_major:
            logger.info(f"Chrome versi {chrome_major} terdeteksi")

    success = download_chromedriver_for_chrome_version(base_dir, chrome_major)
    if success:
        logger.sukses("ChromeDriver berhasil disiapkan")
        return True
    else:
        logger.kesalahan("Gagal menyiapkan ChromeDriver. Pastikan koneksi internet tersedia.")
        return False
