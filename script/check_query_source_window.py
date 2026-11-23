"""通过内置 WebView2 检查源库页面；仅读源库，不启动模型训练。"""
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop
import webview

result = {}
original_start = webview.start


def wait_for(window, expression, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(expression)
        if value:
            return value
        error = window.evaluate_js("document.body.innerText.includes('请关闭窗口') ? document.body.innerText : ''")
        if error:
            raise RuntimeError(error)
        time.sleep(0.2)
    raise TimeoutError(expression)


def inspect():
    window = webview.windows[0]
    try:
        wait_for(window, "location.pathname === '/dashboard' && document.body.innerText.includes('数据管理')")
        window.evaluate_js("document.querySelector('.sidebar-nav a[href=\"/data\"]').click();true")
        wait_for(window, "location.pathname === '/data' && document.querySelectorAll('.source-card').length === 3 && document.querySelectorAll('.source-data .el-table__body tr').length > 0")
        result["channel_page"] = window.evaluate_js("({url:location.href,title:document.title,cards:Array.from(document.querySelectorAll('.source-card')).map(e=>e.innerText),rows:document.querySelectorAll('.source-data .el-table__body tr').length})")
        assert not window.evaluate_js("document.body.innerText.includes('渠道订单 *') || document.body.innerText.includes('下载模板') || document.body.innerText.includes('拖拽文件')")
        window.evaluate_js("document.querySelectorAll('.source-card')[1].click();true")
        wait_for(window, "document.querySelector('.source-caption')?.innerText.includes('仓库订单') && !document.querySelector('.source-data .el-table .el-loading-mask') && document.querySelector('.source-data').innerText.includes('上海仓') && document.querySelector('.source-data').innerText.includes('武汉仓')")
        result["warehouse_mapping"] = True
        window.evaluate_js("document.querySelectorAll('.source-card')[2].click();true")
        wait_for(window, "document.querySelector('.source-caption')?.innerText.includes('抖音主播') && document.querySelector('.source-data').innerText.includes('来源店铺名称（未关联）') && document.querySelectorAll('.source-data .el-table__body tr').length > 0")
        result["anchor_only"] = True
        window.evaluate_js("Array.from(document.querySelectorAll('.source-pagination button')).find(e=>e.innerText.includes('下一页')).click();true")
        wait_for(window, "document.querySelector('.source-pagination')?.innerText.includes('第 2 页') && !document.querySelector('.source-data .el-table .el-loading-mask')")
        result["pagination"] = True
        window.evaluate_js("document.querySelector('.sidebar-nav a[href=\"/training\"]').click();true")
        wait_for(window, "location.pathname === '/training' && document.body.innerText.includes('切换数据源后请先完整训练')")
        assert not window.evaluate_js("document.body.innerText.includes('渠道批次') || document.body.innerText.includes('仓库批次')")
        result["training_no_batches"] = True
        window.evaluate_js("document.querySelector('.sidebar-nav a[href=\"/config\"]').click();true")
        wait_for(window, "location.pathname === '/config' && document.querySelector('#tab-warehouses')")
        window.evaluate_js("document.querySelector('#tab-warehouses').click();true")
        wait_for(window, "document.body.innerText.includes('上海仓') && document.body.innerText.includes('武汉仓')", timeout=60)
        result["configuration"] = True
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        window.destroy()


def start_checked(**kwargs):
    return original_start(inspect, **kwargs)


if __name__ == "__main__":
    webview.start = start_checked
    desktop.main(dev="--dev" in sys.argv)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    assert "error" not in result, result
    assert not any(t.name == "desktop-api" for t in threading.enumerate())
    print("Direct database UI + pagination + training/configuration pages + shutdown: OK")
