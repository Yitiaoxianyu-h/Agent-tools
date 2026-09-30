# -*- coding: utf-8 -*-
"""
Agent Tools — 桌面操作与截图工具
==================================
供 AI 代理（如 Copilot）用于操控 Windows 电脑：
  - 捕获指定应用/窗口的截图
  - 查找、激活、移动、缩放窗口
  - 鼠标点击 / 拖拽 / 按键输入
  - 进程管理（启动 / 结束）

依赖（均已安装）：Pillow、pyautogui、pygetwindow、pywin32(win32gui/win32con)、psutil

用法（命令行）：
    python agent_tools.py shot --title xytools --out shot.png
    python agent_tools.py shot --all --out full.png
    python agent_tools.py list
    python agent_tools.py find --title xytools
    python agent_tools.py activate --title xytools
    python agent_tools.py click --title xytools --x 100 --y 100
    python agent_tools.py type --text "hello"
    python agent_tools.py key --combo ctrl+c
    python agent_tools.py run --exe "D:\\python\\python.exe" --args main.py --cwd D:\\work
    python agent_tools.py kill --name python
    python agent_tools.py close --title xytools --force
    python agent_tools.py resize --title xytools --w 500 --h 900

    # 以下为新增命令
    python agent_tools.py screen                       # 打印主屏分辨率
    python agent_tools.py scroll --amount -3           # 向下滚 3 格
    python agent_tools.py mouse                        # 打印鼠标位置
    python agent_tools.py mouse --move --x 100 --y 200 # 移动鼠标
    python agent_tools.py pixel --x 100 --y 200        # 取该点颜色
    python agent_tools.py findcolor --color "#ff0000"  # 在屏幕上找红色像素
    python agent_tools.py clip --get                   # 读剪贴板
    python agent_tools.py clip --set "文本"             # 写剪贴板
    python agent_tools.py clip --paste "中文文本"       # 写剪贴板并 Ctrl+V
    python agent_tools.py wait --title xytools         # 等窗口出现
    python agent_tools.py topmost --title xytools      # 窗口置顶（--off 取消）

也可作为模块 import：
    from agent_tools import AgentTools
    at = AgentTools()
    at.find_window("xytools").activate()
    at.capture_window(at.find_window("xytools"), "shot.png")
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

import psutil
from PIL import Image
import pyautogui
import win32gui
import win32con
import win32process

try:
    import pygetwindow as gw
    _HAS_PYGETWINDOW = True
except Exception:
    _HAS_PYGETWINDOW = False

# 项目内依赖（不用 C 盘）：Windows.Graphics.Capture 抓帧库
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.append(_VENDOR)   # 放末尾，避免覆盖全局已装的 numpy
try:
    from windows_capture import WindowsCapture
except Exception:
    WindowsCapture = None


class AgentTools:
    """桌面操控工具集。"""

    def __init__(self, screenshot_dir: str = None):
        self.screenshot_dir = screenshot_dir or os.path.join(os.path.dirname(__file__), "shots")
        if not os.path.isdir(self.screenshot_dir):
            os.makedirs(self.screenshot_dir, exist_ok=True)
        # 防止 pyautogui 安全退出误触发（例如鼠标快速甩到屏幕角）
        pyautogui.FAILSAFE = False

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def capture_all(self, out: str = None) -> str:
        """截取整个屏幕，返回图片路径。"""
        out = out or self._default_out("full")
        img = pyautogui.screenshot()
        img.save(out)
        return out

    def capture_window(self, window, out: str = None) -> str:
        """截取某个窗口。window 可以是：
        - AgentTools.WindowInfo
        - pygetwindow.Window
        - 窗口标题（str）
        返回图片路径。窗口需在前景才保证截到最新像素，会先激活它。
        """
        out = out or self._default_out("win")
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            raise RuntimeError("未找到窗口，无法截图")
        window.activate()
        # 给 UI 一点重绘时间
        time.sleep(0.2)
        try:
            hwnd = window.hwnd
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.1)
        except Exception:
            pass

        left, top, right, bottom = window.bbox
        shot = pyautogui.screenshot()
        if left < 0 or top < 0:
            # 窗口部分在屏幕外时，用整屏裁剪
            crop = (max(0, left), max(0, top),
                    min(shot.width, right), min(shot.height, bottom))
            crop = shot.crop(crop)
        else:
            crop = shot.crop((left, top, right, bottom))
        crop.save(out)
        return out

    def capture_window_bg(self, window, out: str = None, flag: int = 2):
        """后台截图：不激活、不置顶、不抢前台焦点、不全屏抓屏。

        优先用 Windows.Graphics.Capture(WGC/D3D11) 按窗口句柄抓帧：
        窗口被其它窗口遮挡、甚至最小化，也能拿到真实像素。
        最小化窗口会先用 SW_SHOWNOACTIVATE 无激活还原（不夺焦点、不改 z 序）。
        WGC 不可用时回退 PrintWindow。
        返回 (图片路径, 是否成功)。
        """
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            raise RuntimeError("未找到窗口，无法后台截图")
        hwnd = window.hwnd
        out = out or self._default_out("winbg")

        # 最小化窗口抓不到帧：无激活还原（不抢焦点、不改变 z 序）
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            time.sleep(0.5)

        try:
            if self._capture_wgc(hwnd, out):
                return out, True
        except Exception as e:
            print(f"WGC 后台截图失败，回退 PrintWindow: {e}", file=sys.stderr)

        return self._capture_printwindow(hwnd, out, flag)

    def _capture_wgc(self, hwnd: int, out: str, timeout: float = 6.0) -> bool:
        """用 Windows.Graphics.Capture 抓取指定窗口的一帧，成功返回 True。

        用 start_free_threaded 在库自建的 MTA 线程上抓帧：调用方若已被初始化成
        STA（如 MiniBlink 宿主）会导致 WGC 初始化失败，且这样也不阻塞主线程。
        """
        if WindowsCapture is None:
            return False
        import threading
        done = threading.Event()
        state = {"ok": False}
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

        @cap.event
        def on_frame_arrived(frame, control):
            if not done.is_set():
                try:
                    # 不用 frame.save_as_image（走 OpenCV imwrite，不支持中文路径）
                    Image.fromarray(frame.frame_buffer, "RGBA").convert("RGB").save(out)
                    state["ok"] = True
                except Exception as e:
                    print(f"WGC 保存帧失败: {e}", file=sys.stderr)
                finally:
                    done.set()
            control.stop()

        @cap.event
        def on_closed():
            done.set()

        control = cap.start_free_threaded()
        try:
            done.wait(timeout)
        finally:
            try:
                control.stop()
            except Exception:
                pass
        return state["ok"]

    def _capture_printwindow(self, hwnd: int, out: str, flag: int = 2):
        """PrintWindow(PW_RENDERFULLCONTENT) 回退方案。返回 (路径, 是否成功)。"""
        import win32ui
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            raise RuntimeError(f"窗口尺寸异常: {w}x{h}（可能已最小化）")

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        try:
            ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flag)
            info = bmp.GetInfo()
            bits = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bits, "raw", "BGRX", 0, 1,
            )
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

        img.save(out)
        return out, bool(ok)

    def _default_out(self, prefix: str) -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.screenshot_dir, f"{prefix}_{ts}.png")

    # ------------------------------------------------------------------
    # 窗口查找
    # ------------------------------------------------------------------
    def list_windows(self) -> list:
        """列出所有可见顶层窗口。返回 [WindowInfo]。"""
        wins = []

        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                info = self._hwnd_to_info(hwnd)
                if info:
                    wins.append(info)
            return True

        win32gui.EnumWindows(_enum, None)
        return wins

    def find_window(self, title: str, proc: str = None) -> "WindowInfo":
        """按标题查找窗口（子串匹配，不区分大小写）。找到返回 WindowInfo，否则 None。

        proc 非空时只匹配「进程名含该子串」的窗口，用于排除 IDE/编辑器的同名窗口
        （例如标题里也带 xytools 的 Trae/VSCode）。
        """
        key = title.lower()
        pkey = proc.lower() if proc else None
        for w in self.list_windows():
            if key not in w.title.lower():
                continue
            if pkey:
                pid = self.window_pid(w)
                if not pid:
                    continue
                try:
                    name = psutil.Process(pid).name().lower()
                except psutil.Error:
                    continue
                if pkey not in name:
                    continue
            return w
        return None

    def _hwnd_to_info(self, hwnd: int) -> "WindowInfo":
        try:
            title = win32gui.GetWindowText(hwnd)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return None
        return WindowInfo(hwnd=hwnd, title=title, bbox=(left, top, right, bottom))

    def activate(self, window) -> bool:
        """激活/置顶某窗口。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return False
        return window.activate()

    def move(self, window, x: int, y: int) -> bool:
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return False
        return window.move(x, y)

    def resize(self, window, w: int, h: int) -> bool:
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return False
        return window.resize(w, h)

    # ------------------------------------------------------------------
    # 鼠标 / 键盘
    # ------------------------------------------------------------------
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1):
        pyautogui.click(x, y, button=button, clicks=clicks)

    def click_on_window(self, window, rel_x: float, rel_y: float,
                        button: str = "left", clicks: int = 1):
        """在窗口内相对坐标(0~1)处点击。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            raise RuntimeError("窗口不存在")
        left, top, right, bottom = window.bbox
        x = int(left + rel_x * (right - left))
        y = int(top + rel_y * (bottom - top))
        self.activate(window)
        time.sleep(0.15)
        self.click(x, y, button=button, clicks=clicks)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5):
        pyautogui.drag(x1, y1, x2, y2, duration=duration)

    def press(self, key: str):
        pyautogui.press(key)

    def combo(self, keys: str):
        """按下组合键，如 'ctrl+c'。"""
        pyautogui.hotkey(*keys.lower().split("+"))

    def type_text(self, text: str, interval: float = 0.02):
        pyautogui.typewrite(text, interval=interval)

    def scroll(self, amount: int, x: int = None, y: int = None):
        """滚动鼠标滚轮。amount 正数向上、负数向下；给了 x/y 先移过去再滚。"""
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y))
        pyautogui.scroll(int(amount))

    def mouse_pos(self) -> tuple:
        """当前鼠标位置，返回 (x, y)。"""
        p = pyautogui.position()
        return (int(p.x), int(p.y))

    def move_mouse(self, x: int, y: int, duration: float = 0.2):
        """把鼠标移到绝对坐标。"""
        pyautogui.moveTo(int(x), int(y), duration=duration)

    # ------------------------------------------------------------------
    # 屏幕 / 像素
    # ------------------------------------------------------------------
    def screen_size(self) -> tuple:
        """主屏分辨率，返回 (宽, 高)。"""
        w, h = pyautogui.size()
        return (int(w), int(h))

    def pixel_color(self, x: int, y: int) -> str:
        """取屏幕上某点的颜色，返回 '#rrggbb'。"""
        r, g, b = pyautogui.pixel(int(x), int(y))
        return f"#{r:02x}{g:02x}{b:02x}"

    def find_color(self, color: str, tolerance: int = 12, region: tuple = None,
                   step: int = 1, limit: int = 20) -> list:
        """在屏幕上找指定颜色的像素，返回坐标列表 [(x, y), ...]。

        color      : '#rrggbb' 或 'rrggbb'
        tolerance  : 每个通道允许的误差（0~255），越大越宽松
        region     : (left, top, width, height)，默认整个屏幕
        step       : 采样步长，>1 更快但可能漏掉小目标
        limit      : 最多返回多少个命中点

        典型用途：找到某个纯色按钮/标记的位置，再配合 click 点它。
        """
        import numpy as np

        target = color.lstrip("#")
        if len(target) != 6:
            raise ValueError("颜色格式应为 '#rrggbb'")
        tr = int(target[0:2], 16)
        tg = int(target[2:4], 16)
        tb = int(target[4:6], 16)

        shot = pyautogui.screenshot(region=region)
        arr = np.asarray(shot.convert("RGB"), dtype=np.int16)
        if step > 1:
            arr = arr[::step, ::step]
        mask = (
            (np.abs(arr[:, :, 0] - tr) <= tolerance)
            & (np.abs(arr[:, :, 1] - tg) <= tolerance)
            & (np.abs(arr[:, :, 2] - tb) <= tolerance)
        )
        ys, xs = np.nonzero(mask)
        offset_x = region[0] if region else 0
        offset_y = region[1] if region else 0
        hits = [
            (int(x * step + offset_x), int(y * step + offset_y))
            for y, x in zip(ys, xs)
        ]
        return hits[:limit]

    def color_at_ratio(self, window, rel_x: float, rel_y: float) -> str:
        """取窗口内相对坐标(0~1)处的颜色，配合 find_color 可用于校验界面状态。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            raise RuntimeError("窗口不存在")
        left, top, right, bottom = window.bbox
        x = int(left + rel_x * (right - left))
        y = int(top + rel_y * (bottom - top))
        return self.pixel_color(x, y)

    # ------------------------------------------------------------------
    # 剪贴板
    # ------------------------------------------------------------------
    def _clipboard_call(self, func, retries: int = 6, delay: float = 0.08):
        """打开剪贴板并执行 func(win32clipboard)。

        剪贴板同一时刻只能被一个进程占用，被别的程序占着时 OpenClipboard 会
        抛「拒绝访问」，这里退避重试几次。全部失败返回 None。
        """
        try:
            import win32clipboard
        except Exception:
            return None
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
            except Exception:
                time.sleep(delay)
                continue
            try:
                return func(win32clipboard)
            except Exception:
                return None
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        return None

    def _clipboard_via_powershell(self, text: str = None):
        """回退方案：借 PowerShell 的 Get-Clipboard / Set-Clipboard。

        某些会话下 win32 的 OpenClipboard 会被拒绝访问，PowerShell 走的是
        另一条路径，通常还能用。
        """
        import subprocess

        try:
            if text is None:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                    capture_output=True, text=True, timeout=15,
                )
                return r.stdout.rstrip("\r\n") if r.returncode == 0 else None
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                input=text, capture_output=True, text=True, timeout=15,
            )
            return True if r.returncode == 0 else None
        except Exception:
            return None

    def get_clipboard(self) -> str:
        """读取剪贴板里的文本，没有或读取失败则返回空串。"""

        def read(cb):
            if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT):
                return cb.GetClipboardData(cb.CF_UNICODETEXT) or ""
            return ""

        result = self._clipboard_call(read)
        if isinstance(result, str):
            return result
        fallback = self._clipboard_via_powershell()
        return fallback if isinstance(fallback, str) else ""

    def set_clipboard(self, text: str) -> bool:
        """把文本写入剪贴板，返回是否成功。"""

        def write(cb):
            cb.EmptyClipboard()
            cb.SetClipboardData(cb.CF_UNICODETEXT, text)
            return True

        if self._clipboard_call(write) is True:
            return True
        return self._clipboard_via_powershell(text) is True

    def paste_text(self, text: str, interval: float = 0.15) -> bool:
        """把文本放进剪贴板再 Ctrl+V 粘贴（比 typewrite 更适合中文/长文本）。"""
        if not self.set_clipboard(text):
            return False
        time.sleep(interval)
        self.combo("ctrl+v")
        return True

    # ------------------------------------------------------------------
    # 窗口：等待 / 置顶
    # ------------------------------------------------------------------
    def wait_for_window(self, title: str, proc: str = None,
                        timeout: float = 15.0, interval: float = 0.4):
        """等待窗口出现，返回 WindowInfo；超时返回 None。

        典型用法：launch 启动程序后，用它在窗口就绪时再截图/点击。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.find_window(title, proc)
            if win is not None:
                return win
            time.sleep(interval)
        return None

    def set_topmost(self, window, enabled: bool = True) -> bool:
        """设置窗口置顶（True）或取消置顶（False）。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return False
        flag = win32con.HWND_TOPMOST if enabled else win32con.HWND_NOTOPMOST
        try:
            win32gui.SetWindowPos(
                window.hwnd, flag, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
            )
            return True
        except Exception:
            return False

    def is_topmost(self, window) -> bool:
        """判断窗口是否置顶。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return False
        ex_style = win32gui.GetWindowLong(window.hwnd, win32con.GWL_EXSTYLE)
        return bool(ex_style & win32con.WS_EX_TOPMOST)

    # ------------------------------------------------------------------
    # 进程
    # ------------------------------------------------------------------
    def list_processes(self) -> list:
        out = []
        for p in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                out.append({"pid": p.info['pid'], "name": p.info['name'], "exe": p.info.get('exe')})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def launch(self, exe: str, args: str = "", cwd: str = None, wait_ms: int = 1500) -> int:
        """启动外部程序，返回 pid。"""
        import subprocess
        cmd = exe + ((" " + args) if args else "")
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if wait_ms:
            time.sleep(wait_ms / 1000.0)
        return proc.pid

    def launch_detached(self, exe: str, args: str = "", cwd: str = None) -> int:
        """经 Windows Shell 完全分离地启动 GUI 程序，返回 pid。

        与 launch 的区别：
        - 走 ShellExecuteEx，由系统 shell 托养，进程不在调用方的进程组/作业(Job)内，
          调用脚本退出或命令结束时不会连带把目标程序杀掉（避免 GUI 闪退）；
        - 不继承调用方的管道/文件句柄；
        - 适合启动 pythonw.exe 这类无控制台的 GUI 应用。
        """
        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_SHOWNORMAL = 1
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        info.fMask = SEE_MASK_NOCLOSEPROCESS
        info.hwnd = None
        info.lpVerb = "open"
        info.lpFile = exe
        info.lpParameters = args or None
        info.lpDirectory = cwd or None
        info.nShow = SW_SHOWNORMAL
        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
        if not ok:
            err = ctypes.windll.kernel32.GetLastError()
            raise OSError(f"ShellExecuteExW 失败, GetLastError={err}")
        pid = 0
        if info.hProcess:
            pid = ctypes.windll.kernel32.GetProcessId(info.hProcess)
            ctypes.windll.kernel32.CloseHandle(info.hProcess)
        return pid

    def kill_by_name(self, name: str) -> int:
        """按进程名（如 'python.exe'）结束所有匹配进程，返回结束数量。
        自动排除当前解释器进程，避免自杀。
        """
        killed = 0
        self_pid = os.getpid()
        self_exe = os.path.basename(sys.executable).lower()
        for p in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if p.info['pid'] == self_pid:
                    continue
                if p.info['name'] and name.lower() in p.info['name'].lower():
                    p.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return killed

    def kill_pids(self, pids) -> int:
        """结束指定 pid 列表的进程，返回结束数量。自动排除自身。"""
        killed = 0
        self_pid = os.getpid()
        for pid in pids:
            if pid == self_pid:
                continue
            try:
                psutil.Process(pid).kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return killed

    def kill_process_tree(self, pid: int) -> bool:
        """结束某个进程及其全部子进程（进程树）。返回是否成功。自动排除自身。"""
        if not pid or pid == os.getpid():
            return False
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def window_pid(self, window) -> int:
        """返回目标窗口所属进程的 pid；找不到窗口返回 None。"""
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return None
        try:
            _, pid = win32process.GetWindowThreadProcessId(window.hwnd)
            return pid
        except Exception:
            return None

    def close_window(self, window, timeout: float = 3.0, force: bool = False) -> bool:
        """优雅关闭窗口（发送 WM_CLOSE）；超时仍未退出且 force=True 时，
        结束其整个进程树。返回窗口最终是否已关闭。
        """
        if isinstance(window, str):
            window = self.find_window(window)
        if window is None:
            return True
        hwnd = window.hwnd
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not win32gui.IsWindow(hwnd):
                return True
            time.sleep(0.1)
        if force:
            self.kill_process_tree(self.window_pid(window))
            time.sleep(0.3)
        return not win32gui.IsWindow(hwnd)

    # 兼容别名
    def screenshot(self, out=None):
        return self.capture_all(out)


class WindowInfo:
    """一个窗口的轻量描述。"""

    def __init__(self, hwnd: int, title: str, bbox):
        self.hwnd = hwnd
        self.title = title
        self.bbox = tuple(bbox)  # left, top, right, bottom

    @property
    def rect(self):
        l, t, r, b = self.bbox
        return {"left": l, "top": t, "width": r - l, "height": b - t}

    def activate(self) -> bool:
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            # 单靠 SetForegroundWindow 常被系统前台锁定策略拒绝（报
            # "SetForegroundWindow: 系统找不到指定的文件"/无错误信息），
            # 这里用 AttachThreadInput 把自己的输入队列挂到当前前台线程，
            # 再置顶即可稳定抢到前台（否则后续 pyautogui 点击会落到遮挡窗口上）。
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            fg_hwnd = win32gui.GetForegroundWindow()
            fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
            tgt_tid = win32process.GetWindowThreadProcessId(self.hwnd)[0]
            attached = []
            for tid in (fg_tid, tgt_tid):
                if tid and tid != cur_tid:
                    ctypes.windll.user32.AttachThreadInput(cur_tid, tid, True)
                    attached.append(tid)
            try:
                win32gui.BringWindowToTop(self.hwnd)
                win32gui.SetForegroundWindow(self.hwnd)
            finally:
                for tid in attached:
                    ctypes.windll.user32.AttachThreadInput(cur_tid, tid, False)
            time.sleep(0.15)
            return True
        except Exception as e:
            print(f"activate failed: {e}", file=sys.stderr)
            return False

    def move(self, x: int, y: int) -> bool:
        try:
            win32gui.MoveWindow(self.hwnd, x, y, 0, 0, False)
            return True
        except Exception:
            return False

    def resize(self, w: int, h: int) -> bool:
        try:
            l, t, r, b = self.bbox
            win32gui.MoveWindow(self.hwnd, l, t, w, h, True)
            self.bbox = (l, t, l + w, t + h)
            return True
        except Exception:
            return False

    def __repr__(self):
        return f"WindowInfo(title={self.title!r}, bbox={self.bbox})"


def _build_parser():
    p = argparse.ArgumentParser(description="Agent Tools - 桌面操控/截图工具")
    sub = p.add_subparsers(dest="cmd")

    shot = sub.add_parser("shot", help="截图")
    shot.add_argument("--all", action="store_true", help="截取整个屏幕")
    shot.add_argument("--title", help="按窗口标题截取（子串匹配）")
    shot.add_argument("--proc", help="配合 --title：只匹配进程名含该子串的窗口（排除同名IDE窗口）")
    shot.add_argument("--out", help="输出图片路径")
    shot.add_argument("--bg", action="store_true",
                      help="后台截图(PrintWindow)，不激活/不抢焦点")

    sub.add_parser("list", help="列出所有可见窗口")

    find = sub.add_parser("find", help="查找窗口")
    find.add_argument("--title", required=True)
    find.add_argument("--proc", help="只匹配进程名含该子串的窗口（排除同名IDE窗口）")

    act = sub.add_parser("activate", help="激活窗口")
    act.add_argument("--title", required=True)

    mv = sub.add_parser("move", help="移动窗口")
    mv.add_argument("--title", required=True)
    mv.add_argument("--x", type=int, required=True)
    mv.add_argument("--y", type=int, required=True)

    rs = sub.add_parser("resize", help="缩放窗口")
    rs.add_argument("--title", required=True)
    rs.add_argument("--w", type=int, required=True)
    rs.add_argument("--h", type=int, required=True)

    cl = sub.add_parser("click", help="点击窗口内相对坐标(0~1)或绝对坐标")
    cl.add_argument("--title", help="窗口标题；提供则按相对坐标点击")
    cl.add_argument("--x", type=float, required=True)
    cl.add_argument("--y", type=float, required=True)
    cl.add_argument("--button", default="left")
    cl.add_argument("--clicks", type=int, default=1)

    tp = sub.add_parser("type", help="键入文本")
    tp.add_argument("--text", required=True)

    key = sub.add_parser("key", help="按键 / 组合键")
    key.add_argument("--key", help="单个按键，如 Enter")
    key.add_argument("--combo", help="组合键，如 ctrl+c")

    run = sub.add_parser("run", help="启动外部程序")
    run.add_argument("--exe", required=True)
    run.add_argument("--args", default="")
    run.add_argument("--cwd", default=None)
    run.add_argument("--wait-ms", type=int, default=1500)
    run.add_argument("--detached", action="store_true",
                     help="经Shell完全分离启动(GUI程序用,不随命令结束被杀)")

    kill = sub.add_parser("kill", help="按进程名结束进程")
    kill.add_argument("--name", required=True)

    killpid = sub.add_parser("killpid", help="按 PID 结束进程")
    killpid.add_argument("--pids", type=int, action="append", required=True)

    close = sub.add_parser("close", help="按窗口标题优雅关闭窗口(WM_CLOSE)")
    close.add_argument("--title", required=True)
    close.add_argument("--force", action="store_true", help="超时未退则强杀进程树")
    close.add_argument("--timeout", type=float, default=3.0)

    procs = sub.add_parser("procs", help="列出进程")
    procs.add_argument("--name", help="过滤进程名")

    scr = sub.add_parser("scroll", help="滚动鼠标滚轮（正数向上，负数向下）")
    scr.add_argument("--amount", type=int, required=True)
    scr.add_argument("--x", type=int, help="先移到该 x 再滚")
    scr.add_argument("--y", type=int, help="先移到该 y 再滚")

    mpos = sub.add_parser("mouse", help="打印当前鼠标位置")
    mpos.add_argument("--move", action="store_true", help="改为移动鼠标（需 --x/--y）")
    mpos.add_argument("--x", type=int)
    mpos.add_argument("--y", type=int)

    px = sub.add_parser("pixel", help="取屏幕上某点的颜色")
    px.add_argument("--x", type=int, required=True)
    px.add_argument("--y", type=int, required=True)

    fc = sub.add_parser("findcolor", help="在屏幕上找指定颜色，输出命中的坐标")
    fc.add_argument("--color", required=True, help="如 #ff0000")
    fc.add_argument("--tolerance", type=int, default=12)
    fc.add_argument("--region", help="限定范围：left,top,width,height")
    fc.add_argument("--step", type=int, default=1, help="采样步长，>1 更快")
    fc.add_argument("--limit", type=int, default=20)

    clip = sub.add_parser("clip", help="读写剪贴板")
    clip.add_argument("--get", action="store_true", help="读取剪贴板文本")
    clip.add_argument("--set", dest="set_text", help="写入剪贴板")
    clip.add_argument("--paste", help="写入剪贴板并 Ctrl+V 粘贴到当前窗口")

    wait = sub.add_parser("wait", help="等待窗口出现（配合 run 使用）")
    wait.add_argument("--title", required=True)
    wait.add_argument("--proc", help="只匹配进程名含该子串的窗口")
    wait.add_argument("--timeout", type=float, default=15.0)

    top = sub.add_parser("topmost", help="窗口置顶 / 取消置顶")
    top.add_argument("--title", required=True)
    top.add_argument("--off", action="store_true", help="取消置顶")

    sub.add_parser("screen", help="打印主屏分辨率")

    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    at = AgentTools()

    if args.cmd == "shot":
        if args.title:
            win = at.find_window(args.title, args.proc)
            if args.bg:
                path, ok = at.capture_window_bg(win, args.out)
                print(path)
                print(f"后台截图(WGC优先) ok={ok}")
            else:
                path = at.capture_window(win, args.out)
                print(path)
        else:
            path = at.capture_all(args.out)
            print(path)
    elif args.cmd == "list":
        for w in at.list_windows():
            print(w)
    elif args.cmd == "find":
        w = at.find_window(args.title, args.proc)
        print(w if w else f"NOT FOUND: {args.title}")
    elif args.cmd == "activate":
        print(at.activate(args.title))
    elif args.cmd == "move":
        print(at.move(args.title, args.x, args.y))
    elif args.cmd == "resize":
        print(at.resize(args.title, args.w, args.h))
    elif args.cmd == "click":
        if args.title:
            at.click_on_window(args.title, args.x, args.y, args.button, args.clicks)
            print(f"clicked {args.title} @({args.x},{args.y})")
        else:
            at.click(int(args.x), int(args.y), args.button, args.clicks)
            print(f"clicked ({args.x},{args.y})")
    elif args.cmd == "type":
        at.type_text(args.text)
        print("typed")
    elif args.cmd == "key":
        if args.combo:
            at.combo(args.combo)
        elif args.key:
            at.press(args.key)
        print("key done")
    elif args.cmd == "run":
        if args.detached:
            pid = at.launch_detached(args.exe, args.args, args.cwd)
        else:
            pid = at.launch(args.exe, args.args, args.cwd, args.wait_ms)
        print(pid)
    elif args.cmd == "kill":
        print(at.kill_by_name(args.name))
    elif args.cmd == "killpid":
        print(at.kill_pids(args.pids))
    elif args.cmd == "close":
        print(at.close_window(args.title, timeout=args.timeout, force=args.force))
    elif args.cmd == "procs":
        for p in at.list_processes():
            if not args.name or args.name.lower() in p["name"].lower():
                print(p)
    elif args.cmd == "scroll":
        at.scroll(args.amount, args.x, args.y)
        print(f"scrolled {args.amount}")
    elif args.cmd == "mouse":
        if args.move:
            if args.x is None or args.y is None:
                print("需要 --x 和 --y")
            else:
                at.move_mouse(args.x, args.y)
                print(f"moved to ({args.x},{args.y})")
        else:
            print(at.mouse_pos())
    elif args.cmd == "pixel":
        print(at.pixel_color(args.x, args.y))
    elif args.cmd == "findcolor":
        region = None
        if args.region:
            parts = [int(v) for v in args.region.split(",")]
            if len(parts) != 4:
                print("--region 需要 4 个数字：left,top,width,height")
                return
            region = tuple(parts)
        hits = at.find_color(args.color, args.tolerance, region,
                             args.step, args.limit)
        print(f"命中 {len(hits)} 个")
        for h in hits:
            print(h)
    elif args.cmd == "clip":
        if args.get:
            print(at.get_clipboard())
        elif args.set_text is not None:
            print("ok" if at.set_clipboard(args.set_text) else "failed")
        elif args.paste is not None:
            at.paste_text(args.paste)
            print("pasted")
        else:
            print(at.get_clipboard())
    elif args.cmd == "wait":
        win = at.wait_for_window(args.title, args.proc, args.timeout)
        print(win if win else f"TIMEOUT: {args.title}")
    elif args.cmd == "topmost":
        print(at.set_topmost(args.title, not args.off))
    elif args.cmd == "screen":
        print(at.screen_size())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
