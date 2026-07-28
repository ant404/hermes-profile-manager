"""Hermes Profile Manager - Desktop App Entry Point

Launches the Flask backend in a background thread, then opens a pywebview
window pointing at it. When the window closes, the app exits.
"""
import os
import sys
import threading
import time
import webview
import subprocess

# Determine paths
if getattr(sys, "frozen", False):
    # PyInstaller bundle
    APP_DIR = os.path.dirname(sys.executable)
    # In onedir mode, app.py / index.html are in _internal or same dir
    BASE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = APP_DIR

PORT = 18520


def _find_port_pid(port):
    """返回占用指定端口的 PID 列表（仅 LISTENING 状态），失败返回空列表"""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"], text=True, stderr=subprocess.DEVNULL
        )
        pids = []
        for line in out.splitlines():
            # 形如  TCP  127.0.0.1:18520   0.0.0.0:0  LISTENING  12345
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) >= 5 and f":{port}" in parts[1]:
                try:
                    pids.append(int(parts[-1]))
                except ValueError:
                    pass
        return pids
    except Exception:
        return []


def _kill_pid(pid):
    """杀掉指定 PID（Windows 用 taskkill /T 杀整个进程树，避免子进程残留）"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        return False


def ensure_port_free(port):
    """确保端口可用：如果被占用，尝试杀掉占用进程（仅限 python.exe / HermesProfileManager.exe）。
    其他程序占用则提示用户手动处理。"""
    import socket
    # 先用 socket 试绑，能绑就说明端口空闲
    test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test.bind(("127.0.0.1", port))
        test.close()
        return True, None
    except OSError:
        test.close()

    # 端口被占用，查 PID
    pids = _find_port_pid(port)
    if not pids:
        return False, f"端口 {port} 被占用，但无法定位占用进程，请手动关闭后重试"

    # 判断每个 PID 的进程名，只杀自己的旧实例
    killed = []
    blocked_by = []
    for pid in pids:
        try:
            proc = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            # 形如 "HermesProfileManager.exe","12345","Console","1","12,345 K"
            name = proc.split('","')[0].strip('"').lower() if proc else ""
        except Exception:
            name = ""

        if name in ("python.exe", "pythonw.exe", "hermesprofilemanager.exe"):
            if _kill_pid(pid):
                killed.append(f"{name}({pid})")
            else:
                blocked_by.append(f"{name}({pid})")
        else:
            blocked_by.append(f"{name}({pid})")

    # 杀完后再次确认端口可用
    import time
    time.sleep(1)
    test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        test.bind(("127.0.0.1", port))
        test.close()
        return True, (f"已清理旧实例: {', '.join(killed)}" if killed else None)
    except OSError:
        test.close()
        msg = f"端口 {port} 仍被占用"
        if blocked_by:
            msg += f"（被 {', '.join(blocked_by)} 占用，请手动关闭）"
        return False, msg


def start_flask():
    """Start Flask server in background thread"""
    os.chdir(BASE_DIR)
    sys.path.insert(0, BASE_DIR)

    # 端口冲突检测：杀掉旧实例（dev 模式残留的 python.exe 或旧 exe 子进程）
    ok, msg = ensure_port_free(PORT)
    if not ok:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Hermes Profile Manager", 0x10)
        sys.exit(1)

    from app import app, init_watch, HERMES_HOME, HERMES_HOME_SOURCE

    init_watch()
    print(f"  HERMES_HOME = {HERMES_HOME}  (source: {HERMES_HOME_SOURCE})")

    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", PORT, app, threaded=True)
    server.serve_forever()


def wait_for_server(timeout=10):
    """Wait until the Flask server responds"""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/info", timeout=1)
            return True
        except:
            time.sleep(0.3)
    return False


def main():
    # Start Flask in background
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait for server to be ready
    if not wait_for_server():
        print("ERROR: Flask server failed to start")
        sys.exit(1)

    print(f"Server ready at http://127.0.0.1:{PORT}")

    # Open pywebview window
    # icon.ico 在 BASE_DIR 下（dev 模式为脚本目录，onefile 模式为 _MEIPASS 解压目录）
    icon_path = os.path.join(BASE_DIR, "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = None

    webview.create_window(
        title="Hermes Profile Manager",
        url=f"http://127.0.0.1:{PORT}",
        width=1200,
        height=800,
        min_size=(900, 600),
        text_select=True,
    )
    webview.start(debug=False, icon=icon_path)


if __name__ == "__main__":
    main()
