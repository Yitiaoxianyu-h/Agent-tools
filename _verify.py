# -*- coding: utf-8 -*-
"""
安全验证器（全程不激活窗口、不抢前台焦点、不全屏抓屏）：
  1) 经 Shell 完全分离启动 pythonw.exe main.py（脱离调用方作业，命令结束不被杀）
  2) 仅用只读枚举找到真正的应用窗口（标题含 xytools 且进程为 python/pythonw，
     借此排除 Trae / VSCode 等同名窗口）
  3) PrintWindow 后台截图 + 白/黑屏量化

阶段：
  python _verify.py --phase launch     # 只启动并报告存活，验证不闪退
  python _verify.py --phase shot --tag before [--keep]
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent_tools import AgentTools  # noqa: E402
import psutil  # noqa: E402
from PIL import Image  # noqa: E402

PYTHONW = r"D:\python\pythonw.exe"
WIN_DIR = r"D:\编程项目\xytools\Windows"
APP_TITLE = "xytools"  # 应用窗口标题 "xytools 工具箱"


def find_app_window(at):
    """返回真正属于 python/pythonw 的 xytools 应用窗口，排除 IDE 同名窗口。"""
    for w in at.list_windows():
        if APP_TITLE not in w.title.lower():
            continue
        pid = at.window_pid(w)
        if not pid:
            continue
        try:
            name = psutil.Process(pid).name().lower()
        except psutil.Error:
            continue
        # 应用由 pythonw/python 承载；排除 TraeCode、Code.exe 等
        if name.startswith("python"):
            return w, pid, name
    return None, None, None


def alive(pid):
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def analyze(img_path):
    im = Image.open(img_path).convert("L")
    w, h = im.size
    box = (int(w * 0.10), int(h * 0.10), int(w * 0.92), int(h * 0.95))
    crop = im.crop(box)
    px = list(crop.getdata())
    total = len(px)
    avg = sum(px) / total
    var = sum((p - avg) ** 2 for p in px) / total
    std = var ** 0.5
    white = sum(1 for p in px if p >= 248) / total
    black = sum(1 for p in px if p <= 6) / total
    return {"size": (w, h), "avg": round(avg, 2), "std": round(std, 2),
            "white": round(white, 4), "black": round(black, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["launch", "shot"], default="shot")
    ap.add_argument("--tag", default="shot")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--wait", type=float, default=3.0, help="出现窗口后渲染等待秒数")
    args = ap.parse_args()

    at = AgentTools()

    # 0) 关闭旧的应用实例（仅 python 承载的，绝不碰 IDE）
    old, opid, _ = find_app_window(at)
    if old:
        print(f"[info] 关闭旧实例 pid={opid}: {old.title}")
        at.close_window(old, timeout=3, force=True)
        time.sleep(0.5)

    # 1) 完全分离启动
    pid = at.launch_detached(PYTHONW, '"main.py"', WIN_DIR)
    print(f"[info] 已分离启动 pid={pid}")

    # 2) 轮询窗口 + 存活检测（最多 20s）
    win = None
    for _ in range(200):
        win, wpid, pname = find_app_window(at)
        if win:
            pid = wpid or pid
            break
        if pid and not alive(pid):
            print(f"[error] 进程 {pid} 已退出（闪退）。请查看 Windows\\data\\xytools.log")
            sys.exit(2)
        time.sleep(0.1)
    if not win:
        print("[error] 20s 内未发现应用窗口，但进程仍在；可能窗口标题不符")
        sys.exit(2)
    print(f"[ok] 窗口出现且进程存活: pid={pid} proc={pname} title={win.title!r} bbox={win.bbox}")

    if args.phase == "launch":
        print("[ok] launch 阶段通过：未闪退。保留进程，可人工观察。")
        return

    # 3) 等待渲染后后台截图（不激活）
    time.sleep(args.wait)
    # 重新取一次最新位置
    win, _, _ = find_app_window(at)
    out = os.path.join(at.screenshot_dir, f"{args.tag}.png")
    path, ok = at.capture_window_bg(win, out, flag=2)
    print(f"[shot] {path}  PrintWindow(flag2) ok={ok}")
    if not ok:
        path, ok = at.capture_window_bg(win, out, flag=0)
        print(f"[shot] 回退 flag0 -> {path} ok={ok}")

    r = analyze(path)
    if r["black"] > 0.98:
        verdict = "截图全黑(PrintWindow未捕获到内容)"
        code = 3
    elif r["avg"] > 225 and r["std"] < 12:
        verdict = "白屏(高亮纯色,页面未渲染)"
        code = 1
    else:
        verdict = "正常渲染"
        code = 0
    print(f"[result] 尺寸={r['size']} 平均灰度={r['avg']} 标准差={r['std']} "
          f"纯白={r['white']} 纯黑={r['black']} => {verdict}")

    if args.keep:
        print("[info] 保留窗口运行")
    else:
        at.close_window(win, timeout=3, force=True)
        print("[info] 已关闭验证实例")
    sys.exit(code)


if __name__ == "__main__":
    main()
