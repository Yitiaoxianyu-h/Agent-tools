# -*- coding: utf-8 -*-
"""
渲染层诊断（真实引擎、真实文件）：
- loadFile 前挂 onConsole，收集所有 console 输出 / 未捕获 JS 错误
- 等 4.5s（启动动画约3.3s后应切到用户协议弹窗）
- 顶层注入 JS，采集关键元素计算样式/盒模型、中心点命中元素、CSS 变量与规则数
- 结果经 document.title 回传，console 消息由 Python 直接打印
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
    api, WKE_WINDOW_TYPE_POPUP, WKE_DOCUMENT_READY_CALLBACK, WKE_CONSOLE_CALLBACK,
)

HTML = os.path.join(WIN_DIR, "EYL", "index.html")
state = {"ready": False}
console_msgs = []


@WKE_DOCUMENT_READY_CALLBACK
def on_ready(webview, param, frame_id):
    state["ready"] = True


@WKE_CONSOLE_CALLBACK
def on_console(webview, param, level, message, source, line, stack):
    def dec(x):
        return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
    msg = dec(message)
    src = dec(source)
    stk = dec(stack)
    console_msgs.append({"level": level, "msg": msg, "src": src, "line": int(line or 0)})
    print(f"[console][L{level}] {msg}  @{src}:{line}")
    if stk.strip():
        print("           stack:", stk[:500])


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        win32gui.PumpWaitingMessages()
        time.sleep(0.02)


def finish(code):
    try:
        sys.stdout.flush()
        if state.get("wv"):
            api.wkeDestroyWebWindow(state["wv"])
        api.wkeShutdown()
    except Exception:
        pass
    os._exit(code)


DIAG_JS = r"""
(function(){
 function cs(sel){
   var e=document.querySelector(sel);
   if(!e) return null;
   var s=getComputedStyle(e), r=e.getBoundingClientRect();
   return {display:s.display, visibility:s.visibility, opacity:s.opacity,
     color:s.color, bg:s.backgroundColor, z:s.zIndex, anim:s.animationName,
     w:Math.round(r.width), h:Math.round(r.height),
     inlineDisplay:e.style.display, inlineOpacity:e.style.opacity};
 }
 var root=getComputedStyle(document.documentElement);
 var el=document.elementFromPoint(innerWidth/2, innerHeight/2);
 var top=el?{tag:el.tagName,id:el.id,cls:String(el.className).slice(0,40),
   text:(el.textContent||'').replace(/\s+/g,' ').slice(0,30)}:null;
 var rules=0, sheetInfo=[];
 for(var i=0;i<document.styleSheets.length;i++){
   var ss=document.styleSheets[i]; var n=-1;
   try{ n=(ss.cssRules||[]).length; rules+=n; }catch(e){ n='blocked'; }
   sheetInfo.push({href:ss.href?ss.href.split('/').pop():'inline', rules:n});
 }
 var data={
  inner:[innerWidth,innerHeight],
  bgVar:root.getPropertyValue('--bg-color').trim(),
  textVar:root.getPropertyValue('--text-color').trim(),
  bodyBg:getComputedStyle(document.body).backgroundColor,
  splash:cs('#splash-screen'),
  welcome:cs('#welcome-text'),
  agreement:cs('#agreement-modal'),
  container:cs('.container'),
  centerTop:top,
  sheets:sheetInfo,
  totalRules:rules
 };
 document.title='DIAG '+encodeURIComponent(JSON.stringify(data));
})();
"""

api.wkeInit()
wv = api.wkeCreateWebWindow(WKE_WINDOW_TYPE_POPUP, None, 100, 100, 1024, 768)
state["wv"] = wv
api.wkeOnDocumentReady(wv, on_ready, None)
api.wkeOnConsole(wv, on_console, None)   # 保留 on_console 引用防 GC
api._cb_keep = on_console
api.wkeLoadFile(wv, HTML.encode("utf-8"))
api.wkeShowWindow(wv, True)

deadline = time.time() + 12
while time.time() < deadline and not state["ready"]:
    win32gui.PumpWaitingMessages()
    time.sleep(0.03)
print("[diag] documentReady =", state["ready"])

# 等启动动画走完(约3.3s切到协议弹窗)
pump(4.5)

api.wkeRunJS(wv, DIAG_JS)
raw = None
end = time.time() + 3
while time.time() < end:
    win32gui.PumpWaitingMessages()
    t = api.wkeGetTitle(wv)
    if isinstance(t, bytes):
        t = t.decode("utf-8", "replace")
    if t and t.startswith("DIAG "):
        raw = t
        break
    time.sleep(0.05)

print(f"[diag] 捕获 console 消息 {len(console_msgs)} 条")
if not raw:
    print("[verdict] FAIL: 未收到 DIAG 回传")
    finish(1)

data = json.loads(unquote(raw[len("DIAG "):]))
print("[diag] 渲染快照:")
print(json.dumps(data, ensure_ascii=False, indent=2))

# 启动完成后(4.5s)，正常应 splash=none、agreement=flex
sp = data.get("splash") or {}
ag = data.get("agreement") or {}
print("[check] splash.display =", sp.get("display"),
      "| agreement.display =", ag.get("display"))
if sp.get("display") != "none":
    print("[hint] splash 未被隐藏 => 启动动画的 DOMContentLoaded 回调可能未执行(脚本顶层先期抛错)")
if ag.get("display") != "flex":
    print("[hint] 用户协议弹窗未显示")
finish(0)
