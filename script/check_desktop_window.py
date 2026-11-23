"""可选的真实 WebView2 冒烟检查；运行后自动关闭测试窗口。"""
import json
import re
from pathlib import Path
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop
import webview

original_start = webview.start
result = {}
dev = "--dev" in sys.argv
probe_id = uuid.uuid4().hex
python_probe = desktop.ROOT / "ecom_forecast" / "api" / f"desktop_probe_{probe_id}.py"
app_vue = desktop.ROOT / "frontend" / "src" / "App.vue"
app_vue_original = None
process_handles = []


def verify_hot_reload(window, origin):
    global app_vue_original
    from test_desktop_dev import open_process
    app_vue_original = app_vue.read_bytes()
    window.evaluate_js(f"window.desktopProbe = '{probe_id}'; window.desktopErrors=[]; window.addEventListener('error',e=>window.desktopErrors.push(e.message)); window.addEventListener('unhandledrejection',e=>window.desktopErrors.push(String(e.reason))); true")
    app_vue.write_bytes(app_vue_original + b"\n<style>body { --desktop-hmr-probe: probe-one; }</style>\n")

    def wait_css(expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = window.evaluate_js("({value:getComputedStyle(document.body).getPropertyValue('--desktop-hmr-probe').trim(),marker:window.desktopProbe,error:window.desktopProbeError})")
            if state and state.get("error"):
                raise RuntimeError(state["error"])
            if state and state["value"] == expected:
                assert state["marker"] == probe_id, "页面被整体刷新，HMR 状态未保留"
                return
            time.sleep(0.1)
        debug = window.evaluate_js("({errors:window.desktopErrors,styles:Array.from(document.querySelectorAll('style')).map(s=>({id:s.getAttribute('data-vite-dev-id'),text:s.textContent.slice(-150)})).filter(s=>s.id?.includes('App.vue')||s.text.includes('desktop-hmr')), scripts:Array.from(document.scripts).map(s=>s.src)})")
        raise TimeoutError(f"CSS HMR 未生效: {expected}, state={state}, debug={debug}")

    wait_css("probe-one")
    app_vue.write_bytes(app_vue_original + b"\n<style>body { --desktop-hmr-probe: probe-two; }</style>\n")
    wait_css("probe-two")
    result["frontend_hmr"] = "CSS 已更新，页面 JS 状态保留"

    log = desktop.ROOT / "output" / "desktop-dev-backend.log"
    initial_log = log.read_text(encoding="utf-8", errors="replace")
    old_pids = re.findall(r"Started server process \[(\d+)\]", initial_log)
    assert old_pids, "未找到实际后端 worker PID"
    process_handles.append(open_process(int(old_pids[-1])))
    python_probe.write_text("# desktop reload verification\n", encoding="utf-8")
    deadline = time.monotonic() + 60
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        current = log.read_text(encoding="utf-8", errors="replace")[len(initial_log):]
        new_pids = re.findall(r"Started server process \[(\d+)\]", current)
        if new_pids and "Application startup complete" in current:
            with opener.open(origin + "/api/health", timeout=5) as response:
                assert json.load(response)["status"] == "ok"
            process_handles.append(open_process(int(new_pids[-1])))
            result["backend_reload"] = {"before": int(old_pids[-1]), "after": int(new_pids[-1])}
            return
        time.sleep(0.2)
    raise TimeoutError("修改 Python 文件后，后端未重载成功")


def inspect_window():
    window = webview.windows[0]
    deadline = time.monotonic() + 150
    try:
        while time.monotonic() < deadline:
            state = window.evaluate_js("({url:location.href,title:document.title,text:document.body.innerText})")
            if state and state["url"].startswith("http://127.0.0.1:") and "/dashboard" in state["url"] and len(state["text"]) > 50:
                result.update(state)
                origin = state["url"].split("/dashboard")[0]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(origin + "/api/health", timeout=5) as response:
                    result["health"] = json.load(response)
                if dev:
                    verify_hot_reload(window, origin)
                return
            if state and "请关闭窗口" in state["text"]:
                raise RuntimeError(state["text"])
            time.sleep(0.3)
        raise TimeoutError("WebView2 页面未在规定时间内加载")
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        window.destroy()


def start_checked(**kwargs):
    return original_start(inspect_window, **kwargs)


if __name__ == "__main__":
    webview.start = start_checked
    if dev:
        from desktop_dev import DevelopmentServer
        from test_desktop_dev import open_process
        original_dev_start = DevelopmentServer.start

        def capture_start(self, *args, **kwargs):
            ready = original_dev_start(self, *args, **kwargs)
            if ready:
                for process in self.job.processes:
                    process_handles.append(open_process(process.pid))
            return ready

        DevelopmentServer.start = capture_start
    try:
        desktop.main(dev=dev)
    finally:
        if app_vue_original is not None:
            # 只移除本次测试的追加样式，保留测试期间可能发生的其他源码编辑。
            current = app_vue.read_bytes()
            for value in (b"probe-one", b"probe-two"):
                current = current.replace(b"\n<style>body { --desktop-hmr-probe: " + value + b"; }</style>\n", b"")
            app_vue.write_bytes(current)
        python_probe.unlink(missing_ok=True)
    for kernel, handle in process_handles:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0, "窗口关闭后仍有开发服务存活"
        finally:
            kernel.CloseHandle(handle)
    if dev and result.get("url"):
        origin = result["url"].split("/dashboard")[0]
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(origin, timeout=1)
        except urllib.error.URLError:
            result["closed_port"] = True
        else:
            raise AssertionError("窗口关闭后前端端口仍可访问")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert "error" not in result, result
    assert result.get("health", {}).get("status") == "ok", result
    assert not any(t.name == "desktop-api" for t in threading.enumerate())
    print("WebView2 + real backend + Vue dashboard + shutdown: OK")
