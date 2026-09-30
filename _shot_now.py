# -*- coding: utf-8 -*-
"""对当前正在运行的应用实例做后台截图(不重启/不激活)。用法: python _shot_now.py before"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent_tools import AgentTools  # noqa: E402
from _verify import find_app_window, analyze  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "shot"
at = AgentTools()
win, pid, name = find_app_window(at)
if not win:
    print("[error] 没有找到 python 承载的 xytools 应用窗口")
    sys.exit(2)
print(f"[info] 目标 hwnd={win.hwnd} pid={pid} proc={name} title={win.title!r} bbox={win.bbox}")
out = os.path.join(at.screenshot_dir, f"{tag}.png")
path, ok = at.capture_window_bg(win, out, flag=2)
print(f"[shot] {path} PrintWindow flag2 ok={ok}")
if not ok:
    path, ok = at.capture_window_bg(win, out, flag=0)
    print(f"[shot] 回退flag0 ok={ok}")
r = analyze(path)
print(f"[result] 尺寸={r['size']} 平均灰度={r['avg']} 标准差={r['std']} "
      f"纯白={r['white']} 纯黑={r['black']}")
if r["black"] > 0.98:
    print("[verdict] 全黑(后台截图未捕获)")
elif r["avg"] > 225 and r["std"] < 12:
    print("[verdict] 白屏(高亮纯色,未渲染)")
else:
    print("[verdict] 正常渲染")
