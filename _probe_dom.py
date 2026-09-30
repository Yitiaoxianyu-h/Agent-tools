# -*- coding: utf-8 -*-
"""
DOM 探针 v3（真实 webwindow + 主线程顶层注入，避免在回调内重入 mbRunJs）：
- onDocumentReady 回调里只置标志，不调用任何 wke API（main.py 已注明回调内调 API 会线程状态错误）
- 回到主线程顶层、非重入状态后，再 mbRunJs 注入诊断
- 结果写 document.title，Python 轮询读取；不 SetForeground、不全屏抓屏，跑完自动关闭
"""
import json
import os
import sys
import time
from urllib.parse import unquote

WIN_DIR = r"D:\编程项目\xytools\Windows"
sys.path.insert(0, WIN_DIR)
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
import win32gui  # noqa: E402
from wke_api import (  # noqa: E402
    api, WKE_WINDOW_TYPE_POPUP, WKE_DOCUMENT_READY_CALLBACK,
)

HTML = os.path.join(WIN_DIR, "EYL", "index.html")
state = {"ready": False, "injected": False, "wv": None}

PROBE_JS = r"""
(function(){
 function g(i){var e=document.getElementById(i);return e?e.textContent:null;}
 var html=document.documentElement.innerHTML;
 var r={
  ready:document.readyState,
  title:document.title,
  bodyChildren:(document.body&&document.body.children.length)||0,
  hasContainer:document.querySelectorAll('.container').length,
  sidebar:document.querySelectorAll('.sidebar').length,
  navItems:document.querySelectorAll('.nav-item').length,
  buttons:document.querySelectorAll('button').length,
  welcome:g('welcome-text'),
  agree:g('agree-btn'),
  h1:(document.querySelector('h1')||{}).textContent||null,
  scripts:document.scripts.length,
  brokenToken:html.indexOf('?/button>')!==-1,
  mojibake:html.indexOf('鍚姩')!==-1
 };
 document.title='PROBE '+encodeURIComponent(JSON.stringify(r));
})();
"""


@WKE_DOCUMENT_READY_CALLBACK
def on_ready(webview, param, frame_id):
    state["ready"] = True   # 只置标志，绝不在回调里调 wke API


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        win32gui.PumpWaitingMessages()
        time.sleep(0.03)


def finish(code):
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        if state["wv"]:
            api.wkeDestroyWebWindow(state["wv"])
        api.wkeShutdown()
    except Exception:
        pass
    os._exit(code)


api.wkeInit()
wv = api.wkeCreateWebWindow(WKE_WINDOW_TYPE_POPUP, None, 100, 100, 1024, 768)
state["wv"] = wv
if not wv:
    print("[FATAL] 无法创建 webwindow")
    sys.exit(2)
api.wkeOnDocumentReady(wv, on_ready, None)
api.wkeLoadFile(wv, HTML.encode("utf-8"))
api.wkeShowWindow(wv, True)

# 1) 主线程顶层轮询等 ready（非重入）
deadline = time.time() + 12
while time.time() < deadline and not state["ready"]:
    win32gui.PumpWaitingMessages()
    time.sleep(0.03)
print("[probe] documentReady =", state["ready"])

# 2) 顶层再泵 2.5s 等外部 main.js 完成初始化
pump(2.5)

# 3) 关键：在主线程顶层、不在任何回调栈内执行 JS
api.wkeRunJS(wv, PROBE_JS)
state["injected"] = True
print("[probe] 已在主线程顶层注入诊断 JS")

# 4) 轮询读取 title 回传
raw = None
end = time.time() + 5
while time.time() < end:
    win32gui.PumpWaitingMessages()
    t = api.wkeGetTitle(wv)
    if isinstance(t, bytes):
        t = t.decode("utf-8", "replace")
    if t and t.startswith("PROBE "):
        raw = t
        break
    time.sleep(0.05)

print("[probe] title =", raw)
if not raw:
    print("[verdict] FAIL: 未收到诊断回传")
    finish(1)

data = json.loads(unquote(raw[len("PROBE "):]))
print("[probe] DOM 诊断:")
for k, v in data.items():
    print(f"    {k} = {v!r}")

ok = (
    data.get("ready") == "complete"
    and (data.get("hasContainer") or 0) >= 1
    and (data.get("navItems") or 0) >= 4
    and (data.get("sidebar") or 0) >= 1
    and data.get("welcome") == "欢迎使用xytools"
    and data.get("agree") == "同意并继续"
    and data.get("brokenToken") is False
    and data.get("mojibake") is False
)
print("[verdict]", "PASS: 页面DOM/中文/脚本均正常" if ok else "FAIL: 仍存在异常")
finish(0 if ok else 1)
