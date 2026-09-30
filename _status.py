# -*- coding: utf-8 -*-
"""只读盘点: 列出残留 python/main.py 进程与 xytools 窗口, 不做任何激活操作。"""
import ctypes
import ctypes.wintypes as wt
import subprocess

# 1) 进程命令行(经 WMI 只读查询)
print("=== python 相关进程 ===")
ps = r'''Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId,CommandLine | Format-List'''
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, timeout=30)
print(r.stdout.strip() or "(无 python.exe)")

# 2) 枚举可见顶层窗口里含 xytools 的
print("=== 含 xytools 的可见窗口 ===")
user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
GetWindowRect = user32.GetWindowRect
GetWindowThreadProcessId = user32.GetWindowThreadProcessId

found = []
def cb(hwnd, lparam):
    if IsWindowVisible(hwnd):
        n = GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            GetWindowTextW(hwnd, buf, n + 1)
            if "xytools" in buf.value.lower():
                rect = wt.RECT()
                GetWindowRect(hwnd, ctypes.byref(rect))
                pid = wt.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, pid.value, buf.value,
                              (rect.left, rect.top, rect.right, rect.bottom)))
    return True
EnumWindows(EnumWindowsProc(cb), 0)
for f in found:
    print(f)
print("(数量: %d)" % len(found))
