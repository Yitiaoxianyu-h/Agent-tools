# Agent Tools — 桌面操控与截图工具

给 AI 代理（或脚本）用来**操控 Windows 电脑**的一套工具：截图、找窗口、点鼠标、敲键盘、
管进程、读剪贴板、找色定位……全部集中在一个 `agent_tools.py` 里，既能命令行调用，
也能当模块 import。

在此基础上还提供了一个**副 Agent（SubAgent）**：给它配一个统一的 API Key，
主 Agent 就能用一条终端命令把子任务派出去，副 Agent 自己规划、自己调工具（桌面操控 /
Shell / 文件读写）直到做完，主 Agent 随时**查看 / 追加指令 / 暂停 / 恢复 / 停止**——
详见 [第 8 节](#8-副-agent-subagent)。

---

## 1. 环境与依赖

| 依赖 | 用途 |
| --- | --- |
| `Pillow` | 图像读写 |
| `pyautogui` | 鼠标 / 键盘 / 截屏 |
| `pygetwindow` | 窗口列表（可选） |
| `pywin32`（`win32gui` / `win32con` / `win32process` / `win32clipboard`） | 窗口句柄、置顶、剪贴板 |
| `psutil` | 进程管理 |
| `openai` | 副 Agent 的大模型调用（OpenAI 兼容端点） |
| `PySide6` | 副 Agent 控制台（Qt 图形界面） |
| `_vendor/windows_capture` | 后台抓帧（WGC / D3D11），窗口被遮挡甚至最小化也能截到真实像素 |
| `_vendor/numpy` | 找色（`findcolor`）用 |

`_vendor` 目录里的库是项目自带的，脚本启动时会自动把它加到 `sys.path` 末尾，
不会覆盖系统里已装的版本。

---

## 2. 快速开始

```bash
cd "D:\编程项目\xytools\Agent Tools"

# 看看有哪些命令
python agent_tools.py

# 截一张全屏
python agent_tools.py shot --all --out full.png

# 截某个窗口（子串匹配标题，会自动激活它）
python agent_tools.py shot --title "AI 小说生成器" --out app.png

# 后台截窗口：不激活、不抢焦点，被遮挡也能截
python agent_tools.py shot --title "AI 小说生成器" --bg --out app_bg.png
```

当模块用：

```python
from agent_tools import AgentTools

at = AgentTools()
win = at.find_window("AI 小说生成器")
at.capture_window(win, "app.png")
at.click_on_window(win, 0.5, 0.9)      # 点窗口水平居中、垂直 90% 处
```

---

## 3. 命令一览

### 3.1 截图

| 命令 | 作用 |
| --- | --- |
| `shot --all --out x.png` | 截整个屏幕 |
| `shot --title <标题> --out x.png` | 截指定窗口（会先激活，保证像素最新） |
| `shot --title <标题> --bg --out x.png` | **后台截图**：不激活、不抢焦点，被遮挡/最小化也能截到 |
| `shot --title <标题> --proc <进程名片段>` | 标题相同时用进程名区分（例如排除同名 IDE 窗口） |

> 截图默认存到 `shots/` 目录，文件名带时间戳。

### 3.2 窗口

| 命令 | 作用 |
| --- | --- |
| `list` | 列出所有可见顶层窗口（含句柄、标题、坐标） |
| `find --title <标题> [--proc <片段>]` | 查找窗口，返回句柄与位置 |
| `activate --title <标题>` | 激活（切到前台） |
| `move --title <标题> --x 100 --y 100` | 移动窗口 |
| `resize --title <标题> --w 1200 --h 800` | 调整窗口大小 |
| `wait --title <标题> [--timeout 15]` | **等待窗口出现**，超时返回 `TIMEOUT:` |
| `topmost --title <标题>` | 窗口置顶；加 `--off` 取消置顶 |
| `close --title <标题> [--force]` | 优雅关闭窗口（`WM_CLOSE`），超时可强杀进程树 |

### 3.3 鼠标 / 键盘

| 命令 | 作用 |
| --- | --- |
| `click --x 100 --y 200` | 在屏幕绝对坐标点击 |
| `click --title <标题> --x 0.5 --y 0.9` | 在**窗口内相对坐标**(0~1) 点击，不用自己换算 |
| `mouse` | 打印当前鼠标坐标 |
| `mouse --move --x 100 --y 200` | 移动鼠标 |
| `scroll --amount -3 [--x --y]` | **滚轮滚动**（正数向上、负数向下；给了 x/y 先移过去） |
| `type --text "hello"` | 逐字键入（英文/数字稳，中文建议用 `clip --paste`） |
| `key --key Enter` / `key --combo ctrl+c` | 单个按键 / 组合键 |
| `clip --paste "中文文本"` | **写剪贴板并 Ctrl+V**，中文和长文本首选 |

### 3.4 屏幕 / 像素（定位按钮用）

| 命令 | 作用 |
| --- | --- |
| `screen` | 打印主屏分辨率 |
| `pixel --x 100 --y 200` | 取屏幕上某点的颜色，返回 `#rrggbb` |
| `findcolor --color "#ff0000" [--tolerance 12] [--region l,t,w,h] [--step 8] [--limit 20]` | **在屏幕上找该颜色的所有位置**，输出坐标列表 |

`findcolor` 是"用颜色找控件"的利器：先 `pixel` 量出目标按钮的颜色，
再 `findcolor` 拿到它的坐标，最后 `click` 点过去。

```bash
# 例：找一个纯红按钮并点击（先粗略扫，再精确定位）
python agent_tools.py findcolor --color "#e81123" --tolerance 20 --step 4
python agent_tools.py click --x 952 --y 64
```

### 3.5 剪贴板

| 命令 | 作用 |
| --- | --- |
| `clip --get` | 读剪贴板文本 |
| `clip --set "文本"` | 写剪贴板 |
| `clip --paste "文本"` | 写剪贴板并立刻 Ctrl+V 粘贴到当前窗口 |

> **注意**：剪贴板同一时刻只能被一个进程占用。代码里已经做了重试，
> 并会在 win32 接口被拒时自动回退到 PowerShell 的 `Set-Clipboard`/`Get-Clipboard`。
> 如果两种方式都失败（报 `拒绝访问` / `did not succeed`），说明当前进程所在的会话
> 没有桌面剪贴板权限——常见于在服务/非交互式会话里运行。在正常的交互式终端里运行即可。

### 3.6 进程

| 命令 | 作用 |
| --- | --- |
| `procs [--name python]` | 列出进程（可按名字过滤） |
| `run --exe <路径> --args "..." --cwd <目录>` | 启动程序，返回 PID |
| `run --exe <路径> --detached` | **完全分离启动**（GUI 程序用，父进程退出不会连带杀掉它） |
| `kill --name python` | 按进程名结束 |
| `killpid --pids 1234 --pids 5678` | 按 PID 结束 |

---

## 4. Python 模块用法

```python
from agent_tools import AgentTools

at = AgentTools()

# --- 窗口 ---
win = at.find_window("记事本")              # 找不到返回 None
win = at.wait_for_window("记事本", timeout=10)   # 等它出现
at.activate(win)
at.resize(win, 900, 600)
at.set_topmost(win, True)                  # 置顶
print(at.is_topmost(win))

# --- 截图 ---
path = at.capture_all()                    # 全屏
path = at.capture_window(win)              # 前台截图（会激活窗口）
path, ok = at.capture_window_bg(win)       # 后台截图，不抢焦点

# --- 鼠标键盘 ---
at.click(100, 200)                         # 绝对坐标
at.click_on_window(win, 0.5, 0.9)          # 窗口内相对坐标
at.move_mouse(100, 200)
at.scroll(-3)
at.type_text("hello")
at.combo("ctrl+s")
at.paste_text("中文文本")                   # 走剪贴板，中文更可靠

# --- 像素 / 找色 ---
print(at.screen_size())                    # (1920, 1080)
print(at.pixel_color(100, 200))            # '#f0f4f9'
hits = at.find_color("#e81123", tolerance=20, step=4)
print(at.color_at_ratio(win, 0.5, 0.5))    # 取窗口内相对位置的颜色

# --- 剪贴板 ---
at.set_clipboard("内容")
print(at.get_clipboard())

# --- 进程 ---
pid = at.launch_detached(r"D:\python\pythonw.exe", "main.py", r"D:\work")
at.kill_by_name("python")
```

---

## 5. 典型套路

**套路 A：启动程序 → 等窗口 → 后台截图验证**

```python
at = AgentTools()
pid = at.launch_detached(r"D:\python\python.exe", "main.py", r"D:\myapp")
win = at.wait_for_window("我的应用", timeout=20)
if win is None:
    raise SystemExit("窗口没起来")
path, ok = at.capture_window_bg(win, "check.png")
print(path, ok)
```

**套路 B：用颜色找按钮再点它**

```python
hits = at.find_color("#4a6cf7", tolerance=15, step=3)   # 主题色按钮
if hits:
    x, y = hits[0]
    at.click(x, y)
```

**套路 C：窗口内相对坐标操作（分辨率无关）**

```python
win = at.find_window("AI 小说生成器")
at.click_on_window(win, 0.10, 0.10)   # 左上角
at.click_on_window(win, 0.50, 0.95)   # 底部中间
```

---

## 6. 注意事项

1. **`pyautogui` 安全机制已关闭**（`FAILSAFE = False`），鼠标可以自由移动，
   但也就没有"甩到屏幕角落急停"的保护，脚本写错坐标时可能点到别处，注意先 `find_window` 确认目标。
2. **前台截图会抢焦点**：`capture_window` 会激活窗口。只想偷偷看一眼就用 `--bg`。
3. **`--title` 是子串匹配**，容易误匹配同名窗口；配合 `--proc` 用进程名过滤更稳。
4. **中文输入别用 `type`**：`pyautogui.typewrite` 对中文不可靠，用 `clip --paste`。
5. **剪贴板需要桌面会话权限**，服务/非交互式会话下会失败（见 3.5 节）。
6. 本目录下的 `_shot_*.py`、`_probe_*.py`、`_status.py` 等是临时排查脚本，
   不是工具库的一部分，可以按需删除。

---

## 7. 本次新增的功能

在原有能力之上补充了下面这些（同时提供了命令行与 Python 方法）：

| 新增 | 命令行 | Python | 作用 |
| --- | --- | --- | --- |
| 滚轮滚动 | `scroll --amount -3` | `at.scroll(-3)` | 翻页 / 滚动长列表，之前只能靠按键 |
| 鼠标位置 | `mouse` | `at.mouse_pos()` | 拿到当前坐标，便于定位 |
| 移动鼠标 | `mouse --move --x --y` | `at.move_mouse(x, y)` | 悬停 / 拖拽前定位 |
| 屏幕分辨率 | `screen` | `at.screen_size()` | 计算坐标、判断多屏 |
| 屏幕取色 | `pixel --x --y` | `at.pixel_color(x, y)` | 量控件颜色，为找色做准备 |
| 屏幕找色 | `findcolor --color ...` | `at.find_color(...)` | 按颜色定位按钮/标记，再点击 |
| 窗口内取色 | — | `at.color_at_ratio(win, rx, ry)` | 相对坐标取色，分辨率无关地校验界面状态 |
| 剪贴板读写 | `clip --get` / `--set` | `at.get_clipboard()` / `at.set_clipboard()` | 复制粘贴、跨程序传数据 |
| 剪贴板粘贴 | `clip --paste "中文"` | `at.paste_text("中文")` | 中文/长文本输入（`typewrite` 做不到） |
| 等待窗口 | `wait --title x` | `at.wait_for_window(...)` | 启动程序后等它就绪，替代 `sleep` |
| 窗口置顶 | `topmost --title x [--off]` | `at.set_topmost(win, True)` | 操作/截图时防止被别的窗口遮挡 |
| 是否置顶 | — | `at.is_topmost(win)` | 查询置顶状态 |

---

## 8. 副 Agent（SubAgent）

参考多 Agent 协作类开源项目的思路，把「派活」和「干活」拆开：

- **主 Agent**（比如你自己、或一个终端里的 AI 代理）只负责拆解与调度，用一条命令把子任务派出去；
- **副 Agent** 是一个独立后台进程（`subagent.py`），用统一配置的 API Key 调用任意
  OpenAI 兼容大模型，自己规划步骤、自己调工具（**桌面操控 / PowerShell / 文件读写**），
  直到把任务做完并写出最终结果；
- 两者通过 `tasks/<task_id>/` 下的**文件信号**通信，所以主 Agent 从任何终端、任何时刻
  都能查看进度、追加指令、暂停、恢复、停止——Qt 界面里看到的和终端里看到的完全一致。

涉及文件：

| 文件 | 作用 |
| --- | --- |
| `subagent.py` | 副 Agent 引擎 + 全部命令行（配置、任务持久化、控制信号、Agent Loop） |
| `subagent_ui.py` | PySide6 图形界面（配置 API Key、管理任务） |
| `config.json` | 统一配置（API Key 等），UI 与命令行共享 |
| `tasks/<task_id>/` | 每个任务一个目录（元信息 / 状态 / 控制信号 / 日志 / 结果） |
| `workspace/` | 副 Agent 的默认工作目录 |

### 8.1 Qt 界面：配置统一的副 Agent API Key

```bash
python agent_tools.py subagent ui    # 或 python subagent_ui.py
```

界面上半部分配置（保存后写入项目根目录 `config.json`，命令行与所有任务共用）：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| Base URL | `https://api.deepseek.com/v1` | OpenAI 兼容端点（注意带 `/v1`） |
| API Key | 空 | **所有副 Agent 任务共用这一个 Key** |
| 模型 | `deepseek-chat` | 任意 OpenAI 兼容模型名 |
| 温度 / 最大步数 / Shell 超时 | 0.3 / 30 / 60 秒 | 最大步数是兜底，防止任务停不下来 |
| 工作目录 | `./workspace` | 副 Agent 读写文件、执行 Shell 的默认目录 |
| 启用工具 | 桌面 / Shell / 文件全开 | 不想让它碰桌面或执行命令就取消勾选 |

下半部分是任务列表与实时日志：**新建 / 暂停 / 恢复 / 停止 / 追加指令 / 打开任务目录 / 删除**，
运行中的任务还会显示进程 pid 与存活标记（绿 ● 存活，红 ✘ 已退出）。

> 环境变量 `SUBAGENT_API_KEY`（或 `OPENAI_API_KEY`）、`SUBAGENT_BASE_URL`、
> `SUBAGENT_MODEL` 优先级高于 `config.json`，可用来临时覆盖。

### 8.2 主 Agent 怎么调用（终端方式）

主 Agent 不需要任何额外集成，**直接执行命令行**即可；所有命令都输出 JSON（`list` /
`status` / 控制类命令）或人读日志（`logs`），方便程序解析。

```bash
# ① 派发任务：后台起独立进程，立即返回 task_id，不阻塞
python agent_tools.py subagent run --task "统计 workspace 目录下所有 txt 的总行数，把结果写到 workspace/汇总.md"
# → {"task_id": "t20260930-225628-qkmj", "status": "running", "pid": 30588, ...}

# ② 查看状态：status 取值 pending / running / paused / done / failed / stopped
#    done 时返回里带 result 字段 = 副 Agent 的最终回答；failed 时带 error
python agent_tools.py subagent status t20260930-225628-qkmj

# ③ 查看执行过程（每一步调了什么工具、结果是什么）
python agent_tools.py subagent logs t20260930-225628-qkmj --tail 30

# ④ 随时干预
python agent_tools.py subagent pause  t20260930-225628-qkmj            # 暂停
python agent_tools.py subagent resume t20260930-225628-qkmj            # 恢复
python agent_tools.py subagent stop   t20260930-225628-qkmj            # 停止
python agent_tools.py subagent update t20260930-225628-qkmj --instruction "输出文件名改成 workspace/汇总_v2.md"   # 追加/修改指令

# ⑤ 其他
python agent_tools.py subagent list            # 列出全部任务（含 pid / worker_alive）
python agent_tools.py subagent rm t20260930-225628-qkmj               # 删除任务记录
python agent_tools.py subagent config --show                           # 查看配置（Key 打码）
python agent_tools.py subagent run --task "..." --foreground           # 前台阻塞执行
```

> `python subagent.py <命令>` 与 `python agent_tools.py subagent <命令>` 完全等价，
> 后者是为了让主 Agent 只需记住一个入口。

**推荐的主 Agent 调度循环**（伪代码）：

```
r    = run --task "<子任务描述>"            # 拿到 r.task_id
loop:
    st = status <r.task_id>
    if st.status == "done":    采纳 st.result，结束
    if st.status == "failed":  读 st.error / logs <id>，决定重试（重新 run）或放弃
    if st.status == "paused":  resume <id>，或先 update --instruction 再 resume
    if 计划有变:               update <id> --instruction "..."   # 下一步立即生效
    if 不再需要:               stop <id>
    等待 5~10 秒再轮询
```

查看类字段的两个补充：`status`/`list` 里 `pid` 是副 Agent 进程号，`worker_alive` 表示
进程是否存活（仅运行中/暂停的任务有）——如果 `status` 一直是 `running` 但
`worker_alive` 为 `false`，直接 `resume` 会自动重新拉起一个副 Agent 继续干。

### 8.3 编程方式调用（Python）

```python
import subagent

tid = subagent.create_task("整理截图", "把 workspace 里的截图按日期分目录")  # 只落盘不执行
subagent.spawn(tid)                       # 后台起一个副 Agent 进程，立即返回 pid

subagent.task_status(tid)                 # dict：status / steps / result / error / worker_alive
subagent.read_log(tid, tail=30)           # 逐步事件
subagent.request_pause(tid)               # 暂停 / 恢复 / 停止
subagent.request_resume(tid)
subagent.request_stop(tid)
subagent.request_instruction(tid, "文件名加上日期前缀")   # 追加指令
```

### 8.4 控制原理（主 Agent 为什么能"随时干预"）

主 Agent 与副 Agent 是两个独立进程，靠 `tasks/<task_id>/` 下的文件协同：

```
tasks/<task_id>/
  meta.json     任务元信息：标题 / status / steps / pid / result / error
  state.json    运行时状态：当前第几步、最新在做什么
  control.json  控制信号：desired = run|pause|stop + 待注入的 instructions
  log.jsonl     逐步事件日志（assistant / tool_call / tool_result / error / finish）
  result.md     副 Agent 的最终回答
```

- 主 Agent 的 `pause` / `resume` / `stop` / `update` 就是**写 `control.json`** 并同步改
  `meta.json`，所以命令立即返回、零延迟可见；
- 副 Agent 在**每个步骤前后**检查 `control.json`：`pause` 原地等待，`stop` 结束任务，
  新指令以 `[主Agent新指令]` 消息注入对话并优先执行；
- 所有状态文件都用「临时文件 + 原子替换」写盘，主 Agent 不会读到半截数据。

### 8.5 注意事项

1. **副 Agent 拥有真实操作能力**（能动鼠标、能执行 PowerShell），派任务前想清楚边界；
   不需要的能力在 UI 里关掉对应工具开关。
2. 后台副 Agent 是分离进程，主 Agent（或终端）退出**不会**连带杀掉它；要终止用 `stop`。
3. 前几次调用模型接口后任务才会真正动起来；进程冷启动（首次 `import openai`）可能稍慢。
4. 副 Agent 也没法"看"截图内容——它靠 `pixel` / `findcolor` / 窗口列表判断界面状态。
