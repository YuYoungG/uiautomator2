# Standard library imports
import os
import time
import re
import platform
import zipfile
import io
import stat
import socket
import subprocess
from pathlib import Path

# Third-party imports (Hard dependencies)
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By


class WebViewDetector:
    """负责检测手机上的 WebView 版本"""
    
    @staticmethod
    def get_free_port():
        """获取一个空闲端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', 0))
            return s.getsockname()[1]

    @staticmethod
    def get_version(d):
        """
        直接连接正在运行的 WebView，询问其版本
        Args:
            d: uiautomator2 Device 对象
        """
        try:
            # 1. 查找 Socket
            # grep -a 防止二进制干扰
            output = d.shell("cat /proc/net/unix | grep -a webview_devtools_remote").output.strip()
            socket_name = None
            if output:
                # 提取最后一个 socket (通常是当前前台应用)
                lines = output.splitlines()
                for line in reversed(lines):
                    match = re.search(r'webview_devtools_remote_\d+', line) or re.search(r'chrome_devtools_remote_\d+', line)
                    if match:
                        socket_name = match.group(0)
                        break
            
            if socket_name:
                # 2. 端口转发
                local_port = WebViewDetector.get_free_port()
                subprocess.check_call(
                    ["adb", "-s", d.serial, "forward", f"tcp:{local_port}", f"localabstract:{socket_name}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                try:
                    # 3. 调用 CDP Version 接口
                    url = f"http://127.0.0.1:{local_port}/json/version"
                    # 直接使用全局 requests
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200:
                        data = resp.json()
                        browser_str = data.get("Browser", "")
                        # 提取 Chrome/x.x.x.x 中的版本号
                        ver_match = re.search(r'Chrome/([\d\.]+)', browser_str)
                        if ver_match:
                            version = ver_match.group(1)
                            return version
                finally:
                    # 清理端口
                    subprocess.call(
                        ["adb", "-s", d.serial, "forward", "--remove", f"tcp:{local_port}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
        except Exception as e:
            print(f"⚠️ [WebViewDetector] 动态检测失败: {e}")

        raise RuntimeError("无法检测到任何 WebView 版本，请确认手机环境")


class ChromeDriverDownloader:
    """负责下载 ChromeDriver (自动适配 Legacy 和 CfT)"""
    
    CFT_RELEASE_URL = "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_"
    CFT_DOWNLOAD_BASE = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
    LEGACY_RELEASE_URL = "https://registry.npmmirror.com/-/binary/chromedriver/LATEST_RELEASE_"
    LEGACY_DOWNLOAD_BASE = "https://registry.npmmirror.com/-/binary/chromedriver"

    def __init__(self):
        # 将保存目录设为当前工作目录(用户脚本同级)下的 drivers 文件夹
        self.save_dir = Path.cwd() / "drivers"
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def _get_platform_key(self, is_cft=False):
        system = platform.system().lower()
        machine = platform.machine().lower()
        if system == "windows":
            return "win64" if is_cft else "win32"
        elif system == "linux":
            return "linux64"
        elif system == "darwin":
            if is_cft:
                return "mac-arm64" if "arm" in machine or "aarch64" in machine else "mac-x64"
            else:
                return "mac64_m1" if "arm" in machine else "mac64"
        raise RuntimeError(f"不支持的操作系统: {system}")

    def download(self, version_full):
        major_ver = int(version_full.split('.')[0])
        version_base = ".".join(version_full.split('.')[:3])
        
        if major_ver >= 115:
            return self._process_cft(version_base)
        else:
            return self._process_legacy(version_base)

    def _process_cft(self, version_base):
        lookup_url = f"{self.CFT_RELEASE_URL}{version_base}"
        exact_version = self._fetch_version_string(lookup_url)
        if not exact_version: return None

        platform_key = self._get_platform_key(is_cft=True)
        download_url = f"{self.CFT_DOWNLOAD_BASE}/{exact_version}/{platform_key}/chromedriver-{platform_key}.zip"
        return self._do_download(download_url, exact_version, is_cft=True)

    def _process_legacy(self, version_base):
        lookup_url = f"{self.LEGACY_RELEASE_URL}{version_base}"
        exact_version = self._fetch_version_string(lookup_url)
        if not exact_version: return None

        platform_key = self._get_platform_key(is_cft=False)
        download_url = f"{self.LEGACY_DOWNLOAD_BASE}/{exact_version}/chromedriver_{platform_key}.zip"
        return self._do_download(download_url, exact_version, is_cft=False)

    def _fetch_version_string(self, url):
        try:
            # 直接使用全局 requests
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception as e:
            print(f"网络请求异常: {e}")
        return None

    def _do_download(self, url, version, is_cft):
        bin_name = "chromedriver.exe" if os.name == 'nt' else "chromedriver"
        target_dir = self.save_dir / version
        target_file = target_dir / bin_name

        if target_file.exists():
            return str(target_file.absolute())

        try:
            # 直接使用全局 requests
            resp = requests.get(url, stream=True, timeout=30)
            if resp.status_code != 200:
                return None
            
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                for filename in z.namelist():
                    if filename.endswith(bin_name):
                        target_dir.mkdir(parents=True, exist_ok=True)
                        with open(target_file, "wb") as f:
                            f.write(z.read(filename))
                        break
            
            if os.name != 'nt' and target_file.exists():
                st = os.stat(target_file)
                os.chmod(target_file, st.st_mode | stat.S_IEXEC)

            return str(target_file.absolute())
        except Exception as e:
            print(f"驱动下载失败: {e}")
            return None


class WebViewExtension:
    def __init__(self, d):
        self.d = d
        self.driver = None

    def attach(self, package_name: str, chromedriver_path: str = None, activity: str = None, process_name: str = None, extra_args: list = None):
        """
        切换到 WebView 模式
        
        Args:
            package_name: App 包名
            chromedriver_path: (可选) 本地驱动路径。留空则自动检测并下载！
            activity: (可选) App Activity
            process_name: (可选) 进程名
            extra_args: (可选) 额外的 Chrome 启动参数 list
        """
        # 自动下载逻辑
        if not chromedriver_path:
            try:
                # 1. 检测版本 (不需要传 requests 了)
                version = WebViewDetector.get_version(self.d)
                # 2. 下载驱动 (不需要传 requests 了)
                downloader = ChromeDriverDownloader()
                downloaded_path = downloader.download(version)
                if downloaded_path:
                    chromedriver_path = downloaded_path
                else:
                    raise RuntimeError("chrome driver download failed")
            except Exception as e:
                raise RuntimeError(f"❌ 自动获取 ChromeDriver 失败: {e}\n请尝试手动指定 chromedriver_path 参数")

        if not os.path.exists(chromedriver_path):
            raise FileNotFoundError(f"can not find ChromeDriver: {chromedriver_path}")

        # 配置 Options (直接使用导入的类)
        options = Options()
        options.add_experimental_option('androidPackage', package_name)
        options.add_experimental_option('androidUseRunningApp', True)
        options.add_experimental_option('androidDeviceSerial', self.d.serial)
        options.add_experimental_option('androidKeepAppDataDir', True)

        if activity:
            options.add_experimental_option('androidActivity', activity)
        if process_name:
            options.add_experimental_option('androidProcess', process_name)
        
        if extra_args:
            for arg in extra_args:
                options.add_argument(arg)

        try:
            # 启动 Service
            service = Service(executable_path=chromedriver_path)
            # 启动 WebDriver
            self.driver = webdriver.Chrome(service=service, options=options)
            time.sleep(1.0)
            return self.driver
        except Exception as e:
            if "version" in str(e).lower():
                print("💡 提示: 驱动版本可能不兼容，请检查手机 WebView 版本与驱动是否匹配。")
            raise e

    def detach(self):
        """退出 WebView 模式"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

    def wait_find(self, by: str, value: str, timeout: int = 10):
        """显式等待查找"""
        if not self.driver:
            raise RuntimeError("请先调用 d.webview.attach() 启动 WebView 模式")
        
        # 直接使用 WebDriverWait 和 EC
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )