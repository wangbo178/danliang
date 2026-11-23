"""桌面入口：常规模式在进程内托管服务，--dev 使用受控子进程热重载。"""
from __future__ import annotations

import html
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "dist"
LOG_PATH = ROOT / "output" / "desktop.log"
TITLE = "BTN物流单量预测系统"


def status_page(message: str, *, error: bool = False) -> str:
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <style>body{{font:16px 'Microsoft YaHei',sans-serif;background:#f4f6fa;
    color:#243047;display:grid;place-content:center;height:95vh;margin:0}}
    main{{max-width:700px;padding:40px}}h1{{font-size:26px}}
    p{{line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}}</style>
    <main><h1>{TITLE}</h1><p>{html.escape(message)}</p>
    {'<p>请关闭窗口，排除问题后重新启动。</p>' if error else ''}</main></html>"""


def mount_frontend(app, directory: Path = FRONTEND) -> None:
    """只在桌面入口挂载；保留 API 404，并支持 Vue history 刷新。"""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    directory = directory.resolve()
    if not (directory / "index.html").is_file():
        raise RuntimeError("前端尚未构建，请先运行「安装桌面程序.bat」。")
    app.mount("/assets", StaticFiles(directory=directory / "assets"), name="desktop-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = (directory / path).resolve()
        if not target.is_relative_to(directory):
            raise HTTPException(status_code=404)
        if target.is_file():
            return FileResponse(target)
        if path and "." in Path(path).name:
            raise HTTPException(status_code=404)
        return FileResponse(directory / "index.html")


class DesktopServer:
    def __init__(self, app):
        import uvicorn

        # 持有已绑定 socket，避免固定端口冲突和“选端口后被抢占”。
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(
            app, log_config=None, access_log=False, timeout_graceful_shutdown=5,
        ))
        self.error = None
        self.thread = threading.Thread(target=self._run, name="desktop-api", daemon=True)

    def _run(self):
        try:
            self.server.run(sockets=[self.socket])
        except BaseException as exc:
            self.error = exc
            logging.exception("后端服务退出")

    def start(self, cancelled: threading.Event, timeout: float = 120):
        self.thread.start()
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not cancelled.is_set():
            if not self.thread.is_alive():
                raise RuntimeError("后端启动失败，请检查数据库连接和 desktop.log。") from self.error
            if self.server.started:
                try:
                    with opener.open(self.url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            return True
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise TimeoutError("后端启动超时（120秒），请检查数据库是否运行。")
            cancelled.wait(0.1)
        return False

    def stop(self):
        self.server.should_exit = True
        if self.thread.ident is not None:
            self.thread.join(timeout=8)
        if self.thread.is_alive():
            self.server.force_exit = True
            self.thread.join(timeout=2)
        self.socket.close()


def configure_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        handlers=[RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=2, encoding="utf-8")],
        force=True,
    )
    # pythonw 没有控制台，第三方库仍可能 print。
    if sys.stdout is None:
        sys.stdout = open(LOG_PATH.parent / "desktop.stdout.log", "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout


def main(dev: bool | None = None):
    if dev is None:
        dev = "--dev" in sys.argv[1:]
    configure_logging()
    import webview

    # 保留原网页的 Excel 等导出功能，由 WebView2 显示保存对话框。
    webview.settings["ALLOW_DOWNLOADS"] = True
    os.chdir(ROOT / "ecom_forecast")
    sys.path.insert(0, str(ROOT / "ecom_forecast"))
    cancelled = threading.Event()
    window = webview.create_window(
        TITLE + ("（开发模式）" if dev else ""), html=status_page("正在启动后端服务，请稍候……"),
        width=1440, height=960, min_size=(1024, 700), background_color="#f4f6fa",
    )
    window.events.closed += cancelled.set

    def boot():
        runtime = None
        try:
            if cancelled.is_set():
                return
            if dev:
                from desktop_dev import DevelopmentServer
                runtime = DevelopmentServer()
            else:
                from api.main import app
                mount_frontend(app)
                runtime = DesktopServer(app)
            if runtime.start(cancelled) and not cancelled.is_set():
                logging.info("后端就绪，加载前端 %s，宿主 PID=%s", runtime.url, os.getpid())
                window.load_url(runtime.url)
                while not cancelled.wait(0.5):
                    alive = runtime.is_alive() if dev else runtime.thread.is_alive()
                    if not alive:
                        raise RuntimeError("服务意外停止，请查看日志后重新启动。")
        except Exception as exc:
            logging.exception("桌面程序启动或运行失败")
            if not cancelled.is_set():
                window.load_html(status_page(f"{exc}\n\n日志：{LOG_PATH}", error=True))
        finally:
            if runtime:
                runtime.stop()

    worker = threading.Thread(target=boot, name="desktop-startup", daemon=True)
    worker.start()
    try:
        # 强制使用现代 Chromium 内核，避免降级到不支持 Vue 的 IE。
        webview.start(gui="edgechromium", private_mode=True)
    finally:
        cancelled.set()
        worker.join(timeout=12)
        logging.info("窗口已关闭，桌面服务已停止")


if __name__ == "__main__":
    main()
