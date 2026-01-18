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
    """负责检测手机上的 WebView 版本 (仅动态检测)"""
    
    @staticmethod
    def get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', 0))
            return s.getsockname()[1]

    @staticmethod
    def get_version(d):
        try:
            # 1. 查找 Socket
            output = d.shell("cat /proc/net/unix | grep -a webview_devtools_remote").output.strip()
            socket_name = None
            if output:
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
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200:
                        data = resp.json()
                        browser_str = data.get("Browser", "")
                        ver_match = re.search(r'Chrome/([\d\.]+)', browser_str)
                        if ver_match:
                            version = ver_match.group(1)
                            return version
                finally:
                    subprocess.call(
                        ["adb", "-s", d.serial, "forward", "--remove", f"tcp:{local_port}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
        except Exception as e:
            print(f"⚠️ [WebViewDetector] 动态检测失败: {e}")

        raise RuntimeError("❌ 无法检测到任何 WebView 版本，请确认 App 已打开且开启了 setWebContentsDebuggingEnabled(true)")


class ChromeDriverDownloader:
    """负责下载 ChromeDriver (使用国内镜像源)"""
    
    # 国内镜像源
    CFT_RELEASE_URL = "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_"
    CFT_DOWNLOAD_BASE = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
    LEGACY_RELEASE_URL = "https://registry.npmmirror.com/-/binary/chromedriver/LATEST_RELEASE_"
    LEGACY_DOWNLOAD_BASE = "https://registry.npmmirror.com/-/binary/chromedriver"

    def __init__(self):
        # 将保存目录设为当前工作目录(用户脚本同级)下的 drivers 文件夹
        self.save_dir = Path.cwd() / "drivers"
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def _get_platform_key(self, is_cft=False):
        """精准获取平台 key"""
        system = platform.system().lower()
        machine = platform.machine().lower()

        if system == "windows":
            return "win64" if is_cft else "win32"
        elif system == "linux":
            return "linux64"
        elif system == "darwin":
            if "arm" in machine or "aarch64" in machine:
                return "mac-arm64" if is_cft else "mac64_m1"
            else:
                return "mac-x64" if is_cft else "mac64"
        raise RuntimeError(f"不支持的操作系统: {system}")

    def download(self, version_full):
        major_ver = int(version_full.split('.')[0])
        version_base = ".".join(version_full.split('.')[:3])
        
        
        if major_ver >= 115:
            return self._process_cft(version_base)
        else:
            return self._process_legacy(version_base)

    def _process_cft(self, version_base):
        # CfT 逻辑
        lookup_url = f"{self.CFT_RELEASE_URL}{version_base}"
        exact_version = self._fetch_version_string(lookup_url)
        if not exact_version: 
            exact_version = version_base

        platform_key = self._get_platform_key(is_cft=True)
        
        # 直接使用镜像地址
        download_url = f"{self.CFT_DOWNLOAD_BASE}/{exact_version}/{platform_key}/chromedriver-{platform_key}.zip"
            
        return self._do_download(download_url, exact_version, is_cft=True)

    def _process_legacy(self, version_base):
        # Legacy 逻辑
        # Legacy 镜像有自己的 LATEST_RELEASE 接口
        lookup_url = f"{self.LEGACY_RELEASE_URL}{version_base}"
        exact_version = self._fetch_version_string(lookup_url)
        if not exact_version: return None

        platform_key = self._get_platform_key(is_cft=False)
        
        # 直接使用镜像地址
        download_url = f"{self.LEGACY_DOWNLOAD_BASE}/{exact_version}/chromedriver_{platform_key}.zip"
        
        return self._do_download(download_url, exact_version, is_cft=False)

    def _fetch_version_string(self, url):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.text.strip()
        except:
            pass
        return None

    def _do_download(self, url, version, is_cft):
        """核心下载与解压逻辑 (含 Mac 修复)"""
        bin_name = "chromedriver.exe" if os.name == 'nt' else "chromedriver"
        target_dir = self.save_dir / version
        target_file = target_dir / bin_name

        # 定义一个内部函数处理权限和隔离属性
        def _set_executable(path):
            if os.name != 'nt':
                try:
                    # 1. 赋予执行权限
                    st = os.stat(path)
                    os.chmod(path, st.st_mode | stat.S_IEXEC)
                    # 2. macOS 特有：移除 com.apple.quarantine 属性，防止Gatekeeper拦截
                    if platform.system() == 'Darwin':
                        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(path)], 
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

        if target_file.exists():
            _set_executable(target_file)
            return str(target_file.absolute())

        try:
            resp = requests.get(url, stream=True, timeout=60)
            if resp.status_code != 200:
                print(f"❌ 下载失败 HTTP {resp.status_code}")
                return None
            
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                found = False
                for filename in z.namelist():
                    # 过滤逻辑
                    if filename.endswith(bin_name) and "LICENSE" not in filename and not filename.endswith("/"):
                        target_dir.mkdir(parents=True, exist_ok=True)
                        with open(target_file, "wb") as f:
                            f.write(z.read(filename))
                        found = True
                        break
                
                if not found:
                    print(f"❌ 压缩包中未找到 {bin_name}")
                    return None
                    
            # 设置权限并移除隔离属性
            _set_executable(target_file)

            return str(target_file.absolute())

        except Exception as e:
            print(f"❌ 下载过程出错: {e}")
            return None


class WebViewExtension:
    def __init__(self, d):
        self.d = d
        self.driver = None

    def attach(self, package_name: str, chromedriver_path: str = None, 
               activity: str = None, process_name: str = None, extra_args: list = None,
               fallback: bool = True): 
        """
        切换到 WebView 模式
        """
        
        # 🟢 自动下载逻辑
        if not chromedriver_path:
            try:
                # 1. 检测版本
                version = WebViewDetector.get_version(self.d)
                # 2. 下载驱动 (默认使用镜像)
                downloader = ChromeDriverDownloader()
                downloaded_path = downloader.download(version)
                if downloaded_path:
                    chromedriver_path = downloaded_path
                else:
                    raise RuntimeError("Download failed")
            except Exception as e:
                msg = f"❌ 自动获取驱动失败: {e}"
                print(msg)
                if fallback: 
                    print("⚠️ 已触发容错机制，跳过 WebView 挂载")
                    return None
                else: 
                    raise RuntimeError(msg)

        if not os.path.exists(chromedriver_path):
            if fallback: return None
            raise FileNotFoundError(f"Driver not found: {chromedriver_path}")

        # 配置 Options
        options = Options()
        options.add_experimental_option('androidPackage', package_name)
        options.add_experimental_option('androidUseRunningApp', True)
        options.add_experimental_option('androidDeviceSerial', self.d.serial)
        options.add_experimental_option('androidKeepAppDataDir', True)

        if activity: options.add_experimental_option('androidActivity', activity)
        if process_name: options.add_experimental_option('androidProcess', process_name)
        if extra_args:
            for arg in extra_args: options.add_argument(arg)

        try:
            # 🟢 确保路径为绝对路径
            service = Service(executable_path=os.path.abspath(chromedriver_path))
            self.driver = webdriver.Chrome(service=service, options=options)
            time.sleep(1.0)
            return self.driver
        except Exception as e:
            if fallback:
                print(f"⚠️ WebDriver 启动失败: {e}")
                return None
            raise e

    def detach(self):
        if self.driver:
            try:
                self.driver.quit()
            except: pass
            finally: self.driver = None

    def wait_find(self, by: str, value: str, timeout: int = 10):
        if not self.driver: raise RuntimeError("Call attach() first")
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )