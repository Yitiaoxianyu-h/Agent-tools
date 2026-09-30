# -*- coding: utf-8 -*-
"""一次性读屏取证：不激活任何窗口，仅 pyautogui.screenshot 读一帧并按应用窗口bbox裁剪。"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pyautogui  # noqa: E402
from agent_tools import AgentTools  # noqa: E402
from _verify import find_app_window, analyze  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "front"
at = AgentTools()
win, pid, name = find_app_window(at)
if not win:
    print("[error] 未找到运行中的应用窗口")
    sys.exit(2)
# 重新取实时 bbox
fresh = at.find_window(win.title) or win
# find_window 可能命中 IDE，故用进程定位到的 hwnd 直接取 rect
import win32gui
l, t, r, b = win32gui.GetWindowRect(win.hwnd)
print(f"[info] hwnd={win.hwnd} pid={pid} title={win.title!r} rect=({l},{t},{r},{b})")
shot = pyautogui.screenshot()  # 只读一帧，不激活、不抢焦
crop = shot.crop((max(0, l), max(0, t), min(shot.width, r), min(shot.height, b)))
out = os.path.join(at.screenshot_dir, f"{tag}.png")
crop.save(out)
res = analyze(out)
print(f"[shot] {out}")
print(f"[result] 平均灰度={res['avg']} 标准差={res['std']} 纯白={res['white']} 纯黑={res['black']}")
if res["avg"] > 225 and res["std"] < 12:
    print("[verdict] 白屏")
else:
    print("[verdict] 有内容渲染")
