# Standard library imports
import os
import time

class WebViewExtension:
    def __init__(self, d):
        """
        初始化 WebView 扩展
        :param d: uiautomator2 Device 对象
        """
        self.d = d
        self.driver = None
        # 用于标记依赖是否已加载
        self._deps_loaded = False

    def _check_dependencies(self):
        """检查并延迟导入 Selenium 依赖，保存为实例属性以避免 IDE 警告"""
        if self._deps_loaded:
            return

        try:
            # 局部导入
            import selenium.webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By

            # 将模块/类保存为实例属性
            self.webdriver = selenium.webdriver
            self.Service = Service
            self.Options = Options
            self.WebDriverWait = WebDriverWait
            self.EC = EC
            self.By = By
            
            self._deps_loaded = True
        except ImportError:
            raise ImportError(
                "❌ 检测到未安装 Selenium。\n"
                "uiautomator2.ext.webview 需要安装 selenium 库支持。\n"
                "请运行: pip install selenium"
            )

    def attach(self, package_name: str, chromedriver_path: str, activity: str = None, process_name: str = None):
        """
        切换到 WebView 模式 (启动 Selenium 并接管当前 App)
        
        Args:
            package_name: App 包名 (如 "com.example.app")
            chromedriver_path: 本地 chromedriver.exe 的绝对路径
            activity: (可选) App Activity
            process_name: (可选) 进程名 (用于多进程 WebView)
            
        Returns:
            Selenium WebDriver 对象
        """
        # 1. 加载依赖
        self._check_dependencies()

        if not os.path.exists(chromedriver_path):
            raise FileNotFoundError(f"❌ 找不到 ChromeDriver，请检查路径: {chromedriver_path}")

        # 2. 配置 Options (使用 self.Options)
        options = self.Options()
        options.add_experimental_option('androidPackage', package_name)
        options.add_experimental_option('androidUseRunningApp', True)
        options.add_experimental_option('androidDeviceSerial', self.d.serial)
        options.add_experimental_option('androidKeepAppDataDir', True)

        if activity:
            options.add_experimental_option('androidActivity', activity)
        if process_name:
            options.add_experimental_option('androidProcess', process_name)

        # 3. 启动 Driver
        try:
            service = self.Service(executable_path=chromedriver_path)
            self.driver = self.webdriver.Chrome(service=service, options=options)
            
            # 简单缓冲，等待 Session 建立
            time.sleep(1.0)
            return self.driver
        except Exception as e:
            if "version" in str(e).lower():
                print("💡 提示: 请检查 ChromeDriver 版本是否与手机 Android System WebView 版本一致。")
            raise e

    def detach(self):
        """退出 WebView 模式 (关闭 Selenium 连接，但保持 App 运行)"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

    def wait_find(self, by: str, value: str, timeout: int = 10):
        """
        [封装] 显式等待并查找元素
        
        Args:
            by: 定位方式 (如 "xpath", "id", "css selector")
            value: 定位表达式
            timeout: 超时时间(秒)
            
        Returns:
            WebElement
        """
        if not self.driver:
            raise RuntimeError("请先调用 d.webview.attach() 启动 WebView 模式")
        
        # 确保依赖已加载
        self._check_dependencies()

        # 使用 self.WebDriverWait 和 self.EC
        return self.WebDriverWait(self.driver, timeout).until(
            self.EC.presence_of_element_located((by, value))
        )