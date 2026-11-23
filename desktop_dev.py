"""Vite HMR + Uvicorn reload，由桌面窗口持有 Windows Job。"""
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
import urllib.request

from desktop_processes import ProcessJob

ROOT = Path(__file__).resolve().parent


class DevelopmentServer:
    def __init__(self):
        self.job = ProcessJob()
        self.url = None
        self.backend = None
        self.frontend = None

    @staticmethod
    def _reserve_port():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        return sock

    def _wait_ready(self, url, process, cancelled, label, timeout):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while not cancelled.is_set():
            if process.poll() is not None:
                raise RuntimeError(f"{label}已退出，请查看 output/desktop-dev-*.log。")
            try:
                with opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        return True
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{label}启动超时，请检查开发日志和数据库。")
            cancelled.wait(0.1)
        return False

    def start(self, cancelled: threading.Event, timeout=120):
        node = shutil.which("node.exe")
        vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
        if not node or not vite.is_file():
            raise RuntimeError("开发模式需要 Node.js 和前端依赖，请先运行「安装桌面程序.bat」。")
        python = Path(sys.executable).with_name("python.exe")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        with self._reserve_port() as backend_socket, self._reserve_port() as frontend_socket:
            backend_url = f"http://127.0.0.1:{backend_socket.getsockname()[1]}"
            frontend_port = frontend_socket.getsockname()[1]
            backend_port = backend_socket.getsockname()[1]
            self.url = f"http://127.0.0.1:{frontend_port}"
            if cancelled.is_set():
                return False
            backend_socket.close()
            self.backend = self.job.spawn([
                python, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                "--port", str(backend_port), "--reload", "--reload-dir", str(ROOT / "ecom_forecast"),
                "--timeout-graceful-shutdown", "3",
            ], cwd=ROOT / "ecom_forecast", log=ROOT / "output" / "desktop-dev-backend.log", env=env)
            if not self._wait_ready(backend_url + "/api/health", self.backend, cancelled, "开发后端", timeout):
                return False
            if cancelled.is_set():
                return False
            frontend_socket.close()
            self.frontend = self.job.spawn([
                node, vite, "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort",
            ], cwd=ROOT / "frontend", log=ROOT / "output" / "desktop-dev-frontend.log",
                env={**env, "DESKTOP_API_TARGET": backend_url})
        if not self._wait_ready(self.url + "/@vite/client", self.frontend, cancelled, "开发前端", timeout):
            return False
        if not self._wait_ready(self.url + "/api/health", self.frontend, cancelled, "前端 API 代理", timeout):
            return False
        logging.info("开发模式就绪，backend=%s frontend=%s，PID=%s/%s",
                     backend_url, self.url, self.backend.pid, self.frontend.pid)
        return True

    def is_alive(self):
        return all(process is not None and process.poll() is None for process in (self.backend, self.frontend))

    def stop(self):
        self.job.close()
