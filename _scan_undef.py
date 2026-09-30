# -*- coding: utf-8 -*-
"""静态枚举 index.html 内联脚本中“被引用但未声明”的自定义函数。"""
import re

PATH = r"D:\编程项目\xytools\Windows\EYL\index.html"
html = open(PATH, encoding="utf-8").read()
m = re.search(r"<script>(.*)</script>", html, re.DOTALL)
js = m.group(1)

# 1) 已声明：function 名 / const|let|var 名 / function 表达式赋值
declared = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", js))
declared |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", js))
declared |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", js))
# 对象方法名不算，暂忽略

# 2) addEventListener(..., bareName) 的裸函数引用（顶层绑定会立即求值）
listener_refs = re.findall(r"addEventListener\(\s*['\"][^'\"]+['\"]\s*,\s*([A-Za-z_$][\w$]*)\s*\)", js)

# 3) 全脚本里的函数调用名
called = set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", js))

# 4) HTML(script 外) on* 属性内联调用
outside = html[:m.start()] + html[m.end():]
inline_called = set(re.findall(r"on[a-z]+\s*=\s*['\"][^'\"]*?\b([A-Za-z_$][\w$]*)\s*\(", outside))

# 浏览器/DOM 内置与常见全局，排除白名单
builtin = set("""if for while switch catch function return typeof new delete in of do else
document window console localStorage sessionStorage alert confirm prompt setTimeout clearTimeout
setInterval clearInterval fetch Promise JSON Date Math Object Array String Number Boolean
RegExp Error parseInt parseFloat isNaN encodeURIComponent decodeURIComponent encodeURI decodeURI
btoa atob getComputedStyle requestAnimationFrame cancelAnimationFrame URL URLSearchParams FormData
Blob FileReader Image Audio XMLHttpRequest WebSocket Map Set WeakMap Symbol parseInt parseFloat
ArrayBuffer Uint8Array atob navigator location history screen performance notification Notification
stopPropagation preventDefault appendChild removeChild querySelector querySelectorAll
getElementById getElementsByClassName getElementsByTagName createElement addEventListener
removeEventListener setAttribute getAttribute hasAttribute removeAttribute classList
stopImmediatePropagation getContext drawImage fillRect toDataURL open close send onload
onerror onabort then catch finally JSON stringify parse keys values entries from assign
freeze trim split join slice splice push pop shift unshift map filter forEach reduce find
some every includes indexOf startsWith endsWith replace match test toLocaleDateString toLocaleString
getTime now getFullYear getMonth getDate getHours getMinutes getSeconds add toggle contains
removeItem getItem setItem postMessage getBoundingClientRect scrollIntoView play pause reset
submit focus blur click select closest matches cloneNode insertBefore normalize reverse sort
concat flat flatMap findIndex fill copyWithin fromCharCode charCodeAt charAt toLowerCase
toUpperCase padStart padEnd repeat normalize""".split())

undef_listener = sorted(set(n for n in listener_refs if n not in declared and n not in builtin))
undef_called = sorted(set(n for n in called if n not in declared and n not in builtin and
                          n[:1].islower() and not n[0].isdigit()))
undef_inline = sorted(set(n for n in inline_called if n not in declared and n not in builtin))

print("=== addEventListener 裸引用但未声明(顶层会立即抛错) ===")
for n in undef_listener:
    print("  ", n)
print("\n=== HTML on* 内联属性调用但未声明(点击时抛错) ===")
for n in undef_inline:
    print("  ", n)
print("\n=== 脚本内调用但未声明(候选，含可能的方法名误报，需人工核对) ===")
for n in undef_called:
    print("  ", n)
