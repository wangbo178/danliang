"""无控制台窗口入口。"""
import ctypes
import traceback

if __name__ == "__main__":
    try:
        from desktop import main
        main()
    except Exception:
        ctypes.windll.user32.MessageBoxW(
            None, traceback.format_exc(), "桌面程序启动失败", 0x10,
        )
