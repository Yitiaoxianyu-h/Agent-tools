# -*- coding: utf-8 -*-
"""
SubAgent — 可被主 Agent 通过终端调度的「副 Agent」
====================================================

设计目标
--------
1. 主 Agent（或人）用一条命令把子任务丢给副 Agent，**立即返回 task_id**，副 Agent 在后台跑。
2. 副 Agent 用统一的 API Key（在 `config.json` 里配置，也可由 Qt UI 配置）调用任意
   OpenAI 兼容大模型，自己规划、自己调工具，直到任务完成。
3. 主 Agent 随时可以用命令**查看 / 暂停 / 恢复 / 停止 / 追加指令**，靠的是 `tasks/<id>/control.json`
   这个文件信号——跨进程、跨终端都有效。

目录结构
--------
    config.json                 配置（api_key / base_url / model / 工具开关 …）
    tasks/<task_id>/meta.json   任务元信息（标题、原始任务、状态、步数）
    tasks/<task_id>/state.json  运行时状态（当前步骤、最新消息）
    tasks/<task_id>/control.json主 Agent 的控制信号（desired: run|pause|stop + 追加指令）
    tasks/<task_id>/log.jsonl   逐步事件日志（assistant / tool_call / tool_result / error）
    tasks/<task_id>/result.md   最终结果
    workspace/                  副 Agent 的默认工作目录（文件读写、shell 的 cwd）

命令行
------
    python subagent.py config --set api_key=sk-xxx --set model=deepseek-chat
    python subagent.py run --task "打开记事本输入 hello 并截图"      # 后台，返回 task_id
    python subagent.py run --task "..." --foreground               # 前台阻塞
    python subagent.py list
    python subagent.py status <task_id>
    python subagent.py logs <task_id> --tail 30
    python subagent.py pause <task_id>
    python subagent.py resume <task_id>
    python subagent.py stop <task_id>
    python subagent.py update <task_id> --instruction "改成存到 workspace/shot.png"
    python subagent.py ui                                          # 打开 Qt 界面

也可作为模块 import：
    import subagent
    tid = subagent.create_task("标题", "任务内容")
    subagent.spawn(tid)              # 后台起一个副 Agent 进程
    subagent.request_pause(tid)      # 请求暂停
"""
import argparse
import json
import os
import random
import string
import subprocess
import sys
import time
import traceback

# ---------------------------------------------------------------- 路径与常量

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
TASKS_DIR = os.path.join(ROOT, "tasks")
DEFAULT_WORKSPACE = os.path.join(ROOT, "workspace")

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.3,
    "max_steps": 30,
    "shell_timeout": 60,
    "workspace": DEFAULT_WORKSPACE,
    "tools": {"desktop": True, "shell": True, "file": True},
}

# 任务状态：pending(排队) running(执行中) paused(已暂停) done(完成) failed(失败) stopped(被停止)
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
FINAL_STATUSES = (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)


# ---------------------------------------------------------------- 通用小工具

def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 原子替换，避免主 Agent 读到半截文件


def now_ts():
    return time.time()


def fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return "-"


# ---------------------------------------------------------------- 配置

def load_config():
    """读取配置：默认值 <- config.json <- 环境变量（环境变量优先级最高）。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg["tools"] = dict(DEFAULT_CONFIG["tools"])
    user = read_json(CONFIG_PATH, {}) or {}
    for k, v in user.items():
        if k == "tools" and isinstance(v, dict):
            cfg["tools"].update(v)
        else:
            cfg[k] = v
    # 环境变量覆盖，方便主 Agent 临时指定
    api_key = os.environ.get("SUBAGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        cfg["api_key"] = api_key
    base_url = os.environ.get("SUBAGENT_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if base_url:
        cfg["base_url"] = base_url
    if os.environ.get("SUBAGENT_MODEL"):
        cfg["model"] = os.environ["SUBAGENT_MODEL"]
    return cfg


def save_config(updates):
    """把 updates 合并写回 config.json（只写用户显式给的字段）。"""
    cfg = read_json(CONFIG_PATH, {}) or {}
    for k, v in updates.items():
        if k == "tools" and isinstance(v, dict):
            cfg.setdefault("tools", {}).update(v)
        else:
            cfg[k] = v
    write_json(CONFIG_PATH, cfg)
    return cfg


def _coerce(value):
    """把命令行里的字符串值转成合适的类型。"""
    if not isinstance(value, str):
        return value
    low = value.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("none", "null", ""):
        return "" if low == "" else None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# ---------------------------------------------------------------- 任务持久化

def _task_dir(task_id):
    return os.path.join(TASKS_DIR, task_id)


def _meta_path(task_id):
    return os.path.join(_task_dir(task_id), "meta.json")


def _state_path(task_id):
    return os.path.join(_task_dir(task_id), "state.json")


def _control_path(task_id):
    return os.path.join(_task_dir(task_id), "control.json")


def _log_path(task_id):
    return os.path.join(_task_dir(task_id), "log.jsonl")


def _result_path(task_id):
    return os.path.join(_task_dir(task_id), "result.md")


def new_task_id():
    ts = time.strftime("%Y%m%d-%H%M%S")
    rnd = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"t{ts}-{rnd}"


def create_task(prompt, title=None):
    """新建一个任务（只落盘，不执行）。返回 task_id。"""
    _ensure_dir(TASKS_DIR)
    task_id = new_task_id()
    d = _ensure_dir(_task_dir(task_id))
    ts = now_ts()
    meta = {
        "id": task_id,
        "title": title or (prompt.strip().splitlines()[0][:40] if prompt.strip() else "未命名任务"),
        "prompt": prompt,
        "created_at": ts,
        "updated_at": ts,
        "status": STATUS_PENDING,
        "steps": 0,
        "error": None,
        "finished_at": None,
        "dir": d,
    }
    write_json(_meta_path(task_id), meta)
    write_json(_state_path(task_id), {"status": STATUS_PENDING, "step": 0,
                                      "message": "已创建，等待执行", "updated_at": ts})
    write_json(_control_path(task_id), {"desired": "run", "instructions": [],
                                        "consumed": 0, "updated_at": ts})
    return task_id


def load_meta(task_id):
    return read_json(_meta_path(task_id), None)


def update_meta(task_id, **fields):
    meta = load_meta(task_id) or {"id": task_id}
    meta.update(fields)
    meta["updated_at"] = now_ts()
    write_json(_meta_path(task_id), meta)
    return meta


def set_state(task_id, status=None, step=None, message=None):
    state = read_json(_state_path(task_id), {}) or {}
    if status is not None:
        state["status"] = status
    if step is not None:
        state["step"] = step
    if message is not None:
        state["message"] = message
    state["updated_at"] = now_ts()
    write_json(_state_path(task_id), state)
    return state


def append_log(task_id, event):
    event = dict(event)
    event.setdefault("ts", now_ts())
    with open(_log_path(task_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def read_log(task_id, tail=None):
    events = []
    try:
        with open(_log_path(task_id), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        return []
    return events[-tail:] if tail else events


def list_tasks():
    """列出全部任务，按创建时间倒序。"""
    if not os.path.isdir(TASKS_DIR):
        return []
    tasks = []
    for name in os.listdir(TASKS_DIR):
        meta = read_json(_meta_path(name), None)
        if not meta:
            continue
        state = read_json(_state_path(name), {}) or {}
        meta["state"] = state
        # meta 里的 status 优先，其次看 state
        meta["status"] = meta.get("status") or state.get("status") or STATUS_PENDING
        if meta["status"] in (STATUS_RUNNING, STATUS_PAUSED):
            meta["worker_alive"] = worker_alive(name)
        tasks.append(meta)
    tasks.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return tasks


def task_status(task_id):
    meta = load_meta(task_id)
    if not meta:
        return None
    state = read_json(_state_path(task_id), {}) or {}
    meta["state"] = state
    meta["status"] = meta.get("status") or state.get("status") or STATUS_PENDING
    if meta["status"] in (STATUS_RUNNING, STATUS_PAUSED):
        meta["worker_alive"] = worker_alive(task_id)
    meta.pop("dir", None)
    return meta


# ---------------------------------------------------------------- 控制信号

def read_control(task_id):
    ctl = read_json(_control_path(task_id), None)
    if ctl is None:
        ctl = {"desired": "run", "instructions": [], "consumed": 0, "updated_at": now_ts()}
    ctl.setdefault("instructions", [])
    ctl.setdefault("consumed", 0)
    return ctl


def write_control(task_id, ctl):
    ctl["updated_at"] = now_ts()
    write_json(_control_path(task_id), ctl)
    return ctl


def _set_desired(task_id, desired):
    meta = load_meta(task_id)
    if not meta:
        return {"ok": False, "error": f"任务不存在: {task_id}"}
    status = meta.get("status")
    if desired == "run" and status in FINAL_STATUSES:
        return {"ok": False, "error": f"任务已结束({status})，无法恢复", "task_id": task_id}
    ctl = read_control(task_id)
    ctl["desired"] = desired
    write_control(task_id, ctl)
    # 立刻反映到 meta，主 Agent 不用等副 Agent 醒来
    if desired == "pause":
        update_meta(task_id, status=STATUS_PAUSED)
        set_state(task_id, status=STATUS_PAUSED, message="已被主 Agent 暂停")
    elif desired == "stop":
        update_meta(task_id, status=STATUS_STOPPED, finished_at=now_ts())
        set_state(task_id, status=STATUS_STOPPED, message="已被主 Agent 停止")
    elif desired == "run" and status == STATUS_PAUSED:
        if worker_alive(task_id):
            update_meta(task_id, status=STATUS_RUNNING)
            set_state(task_id, status=STATUS_RUNNING, message="已被主 Agent 恢复")
        else:
            # 暂停期间进程没了（被杀 / 重启过电脑）：重新拉起一个副 Agent
            append_log(task_id, {"type": "system",
                                 "content": "原副 Agent 进程已不在，恢复时重新拉起"})
            spawn(task_id)
    return {"ok": True, "task_id": task_id, "desired": desired}


def request_pause(task_id):
    return _set_desired(task_id, "pause")


def request_resume(task_id):
    return _set_desired(task_id, "run")


def request_stop(task_id):
    return _set_desired(task_id, "stop")


def request_instruction(task_id, text):
    """给运行中的任务追加一条新指令，副 Agent 会在下一步读取并执行。"""
    meta = load_meta(task_id)
    if not meta:
        return {"ok": False, "error": f"任务不存在: {task_id}"}
    ctl = read_control(task_id)
    ctl["instructions"].append({"text": text, "ts": now_ts()})
    write_control(task_id, ctl)
    append_log(task_id, {"type": "instruction", "content": text, "from": "main-agent"})
    return {"ok": True, "task_id": task_id, "pending_instructions":
            len(ctl["instructions"]) - ctl.get("consumed", 0)}


def remove_task(task_id):
    import shutil
    d = _task_dir(task_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return {"ok": True, "task_id": task_id}
    return {"ok": False, "error": f"任务不存在: {task_id}"}


# ---------------------------------------------------------------- 后台进程

def spawn(task_id):
    """后台启动一个独立的副 Agent 进程来执行该任务，立即返回。"""
    flags = 0
    if os.name == "nt":
        # 完全分离：父进程退出不会连带杀掉副 Agent
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_GROUP | NO_WINDOW
    devnull = open(os.devnull, "wb")
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "_worker", "--task-id", task_id],
            cwd=ROOT, stdin=devnull, stdout=devnull, stderr=devnull,
            creationflags=flags, close_fds=True,
        )
    finally:
        devnull.close()
    # 立刻把状态置为 running，主 Agent 不用等子进程冷启动
    update_meta(task_id, status=STATUS_RUNNING, pid=proc.pid)
    set_state(task_id, status=STATUS_RUNNING, message=f"副 Agent 进程已启动 (pid={proc.pid})")
    append_log(task_id, {"type": "system", "content": f"副 Agent 进程已启动 pid={proc.pid}"})
    return proc.pid


def worker_alive(task_id):
    """判断任务的副 Agent 进程是否还活着（依据 meta 里记录的 pid）。"""
    meta = load_meta(task_id) or {}
    pid = meta.get("pid")
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return True   # 判断不了时保守起见当作活着，避免误判导致重复拉起


# ---------------------------------------------------------------- 工具集

DESKTOP_ACTIONS = [
    "shot_all", "shot_window", "list_windows", "find_window", "activate", "wait",
    "click", "click_window", "type", "key", "combo", "paste", "scroll",
    "mouse", "mouse_move", "pixel", "findcolor", "screen",
    "procs", "run", "kill", "close", "topmost", "move", "resize",
]


def build_tools(cfg):
    """按配置生成 function-calling 工具声明。"""
    enabled = cfg.get("tools", {})
    tools = []
    if enabled.get("desktop", True):
        tools.append({
            "type": "function",
            "function": {
                "name": "desktop",
                "description": (
                    "操控 Windows 桌面。action 取值：" + " / ".join(DESKTOP_ACTIONS) + "。"
                    "常用：shot_all(全屏截图)、shot_window(title=窗口标题) 截图存盘并返回路径；"
                    "find_window/activate 找窗口并激活；click_window(title, rx, ry) 按窗口内相对坐标(0~1)点击；"
                    "click(x, y) 按屏幕绝对坐标点击；type(text) 输入英文/数字；"
                    "paste(text) 走剪贴板输入中文；key(key=Enter) 或 combo(combo=ctrl+c) 按键；"
                    "findcolor(color='#ff0000', tolerance=20) 在屏幕上按颜色找点并返回坐标；"
                    "pixel(x, y) 取某点颜色；scroll(amount) 滚轮；screen 看分辨率；"
                    "procs/kill/run/close 管理进程与窗口。"
                    "注意：截图只返回图片文件路径，若模型不能看图，请改用 pixel/findcolor 做精确判断。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": DESKTOP_ACTIONS},
                        "title": {"type": "string", "description": "窗口标题（子串匹配）"},
                        "proc": {"type": "string", "description": "配合 title 的进程名片段"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "rx": {"type": "number", "description": "窗口内相对横坐标 0~1"},
                        "ry": {"type": "number", "description": "窗口内相对纵坐标 0~1"},
                        "text": {"type": "string"}, "key": {"type": "string"},
                        "combo": {"type": "string"}, "color": {"type": "string"},
                        "tolerance": {"type": "integer"}, "amount": {"type": "integer"},
                        "w": {"type": "integer"}, "h": {"type": "integer"},
                        "exe": {"type": "string"}, "args": {"type": "string"},
                        "cwd": {"type": "string"}, "timeout": {"type": "number"},
                    },
                    "required": ["action"],
                },
            },
        })
    if enabled.get("shell", True):
        tools.append({
            "type": "function",
            "function": {
                "name": "shell",
                "description": "在工作目录下执行一条 PowerShell 命令，返回 exit_code / stdout / stderr。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的命令"},
                        "cwd": {"type": "string", "description": "工作目录，默认 workspace"},
                        "timeout": {"type": "integer", "description": "超时秒数，默认取配置"},
                    },
                    "required": ["command"],
                },
            },
        })
    if enabled.get("file", True):
        tools.append({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文本文件内容（相对路径基于 workspace）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "description": "最多读多少字符，默认 20000"},
                    },
                    "required": ["path"],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写入文本文件（相对路径基于 workspace，自动建父目录）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean", "description": "true 则追加，默认覆盖"},
                    },
                    "required": ["path", "content"],
                },
            },
        })
    return tools


class ToolBox:
    """副 Agent 的工具执行器。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.workspace = cfg.get("workspace") or DEFAULT_WORKSPACE
        _ensure_dir(self.workspace)
        self._at = None

    @property
    def at(self):
        if self._at is None:
            from agent_tools import AgentTools
            self._at = AgentTools()
        return self._at

    # ---- 路径 ----
    def resolve(self, path):
        if not os.path.isabs(path):
            path = os.path.join(self.workspace, path)
        return os.path.abspath(path)

    # ---- 分派 ----
    def dispatch(self, name, args):
        args = args or {}
        if name == "desktop":
            return self.do_desktop(args)
        if name == "shell":
            return self.run_shell(args.get("command", ""), args.get("cwd"), args.get("timeout"))
        if name == "read_file":
            return self.read_file(args.get("path", ""), args.get("max_chars", 20000))
        if name == "write_file":
            return self.write_file(args.get("path", ""), args.get("content", ""), args.get("append", False))
        return {"error": f"未知工具: {name}"}

    # ---- 桌面 ----
    def do_desktop(self, a):
        at = self.at
        action = a.get("action")
        if action == "shot_all":
            return {"path": at.capture_all()}
        if action == "shot_window":
            win = at.find_window(a.get("title"), a.get("proc"))
            if win is None:
                return {"error": f"未找到窗口: {a.get('title')}"}
            path, ok = at.capture_window_bg(win)
            return {"path": path, "ok": ok}
        if action == "list_windows":
            return {"windows": [{"title": w.title, "bbox": list(w.bbox)} for w in at.list_windows()]}
        if action == "find_window":
            win = at.find_window(a.get("title"), a.get("proc"))
            return {"found": bool(win), "title": win.title if win else None,
                    "bbox": list(win.bbox) if win else None}
        if action == "activate":
            return {"ok": at.activate(a.get("title"))}
        if action == "wait":
            win = at.wait_for_window(a.get("title"), a.get("proc"), a.get("timeout", 15))
            return {"found": bool(win), "title": win.title if win else None}
        if action == "click":
            at.click(int(a.get("x", 0)), int(a.get("y", 0)))
            return {"ok": True}
        if action == "click_window":
            at.click_on_window(a.get("title"), float(a.get("rx", 0.5)), float(a.get("ry", 0.5)))
            return {"ok": True}
        if action == "type":
            at.type_text(a.get("text", ""))
            return {"ok": True}
        if action == "key":
            at.press(a.get("key", ""))
            return {"ok": True}
        if action == "combo":
            at.combo(a.get("combo", ""))
            return {"ok": True}
        if action == "paste":
            at.paste_text(a.get("text", ""))
            return {"ok": True}
        if action == "scroll":
            at.scroll(int(a.get("amount", 0)))
            return {"ok": True}
        if action == "mouse":
            return {"pos": list(at.mouse_pos())}
        if action == "mouse_move":
            at.move_mouse(int(a.get("x", 0)), int(a.get("y", 0)))
            return {"ok": True}
        if action == "pixel":
            return {"color": at.pixel_color(int(a.get("x", 0)), int(a.get("y", 0)))}
        if action == "findcolor":
            hits = at.find_color(a.get("color", "#000000"),
                                 tolerance=int(a.get("tolerance", 12)), step=2)
            return {"hits": [list(h) for h in (hits or [])][:50]}
        if action == "screen":
            return {"size": list(at.screen_size())}
        if action == "procs":
            return {"processes": at.list_processes()[:80]}
        if action == "run":
            pid = at.launch_detached(a.get("exe"), a.get("args", ""), a.get("cwd"))
            return {"pid": pid}
        if action == "kill":
            return {"killed": at.kill_by_name(a.get("name"))} if a.get("name") else {"error": "缺少 name"}
        if action == "close":
            return {"ok": at.close_window(a.get("title"), timeout=a.get("timeout", 3.0))}
        if action == "topmost":
            return {"ok": at.set_topmost(a.get("title"), True)}
        if action == "move":
            return {"ok": at.move(a.get("title"), int(a.get("x", 0)), int(a.get("y", 0)))}
        if action == "resize":
            return {"ok": at.resize(a.get("title"), int(a.get("w", 800)), int(a.get("h", 600)))}
        return {"error": f"未知 desktop action: {action}"}

    # ---- Shell ----
    def run_shell(self, command, cwd=None, timeout=None):
        if not command:
            return {"error": "command 为空"}
        timeout = int(timeout or self.cfg.get("shell_timeout", 60))
        cwd = self.resolve(cwd) if cwd else self.workspace
        _ensure_dir(cwd)
        ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command]
        try:
            p = subprocess.run(ps, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            return {"exit_code": p.returncode,
                    "stdout": (p.stdout or "")[-8000:],
                    "stderr": (p.stderr or "")[-4000:]}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"命令超时（>{timeout}s）"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    # ---- 文件 ----
    def read_file(self, path, max_chars=20000):
        if not path:
            return {"error": "path 为空"}
        p = self.resolve(path)
        if not os.path.isfile(p):
            return {"error": f"文件不存在: {p}"}
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                data = f.read(int(max_chars))
            return {"path": p, "content": data, "truncated": os.path.getsize(p) > int(max_chars)}
        except Exception as e:
            return {"error": str(e)}

    def write_file(self, path, content, append=False):
        if not path:
            return {"error": "path 为空"}
        p = self.resolve(path)
        _ensure_dir(os.path.dirname(p))
        try:
            mode = "a" if append else "w"
            with open(p, mode, encoding="utf-8") as f:
                f.write(content)
            return {"path": p, "bytes": len(content.encode("utf-8")), "append": bool(append)}
        except Exception as e:
            return {"error": str(e)}


# ---------------------------------------------------------------- Agent Loop

SYSTEM_PROMPT = """你是一个被「主 Agent」通过命令行调度的副 Agent（SubAgent）。
主 Agent 会把一个具体的子任务派给你，你负责把它做完，并给出简洁的最终结果。

可用工具（按需调用，也可以不调用直接回答）：
- desktop：操控 Windows 桌面（截图、找窗口、点击、输入、按键、剪贴板、进程等）
- shell：在工作目录下执行 PowerShell 命令
- read_file / write_file：读写文件（相对路径基于工作目录）

工作要求：
1. 先想清楚步骤，再一步步执行；每一步尽量只做一件事。
2. 完成任务后，**不要再调用工具**，直接用一段简洁的中文说明「做了什么 + 结果/产物路径」，
   这段话就是返回给主 Agent 的最终答案。
3. 遇到工具报错要换方法重试，不要重复同样的失败调用；确实做不了就说明原因。
4. 只能通过工具认识界面：截图工具只返回图片路径，你不能直接看图；
   要判断界面状态请用 pixel / findcolor，或先 list_windows / find_window 确认窗口。
5. 主 Agent 可能随时给你追加新指令（会以 [主Agent新指令] 开头的消息出现），收到后要优先执行。
6. 不要做与任务无关的危险操作（如格式化磁盘、删除系统目录）。"""


class TaskStopped(Exception):
    """主 Agent 要求停止任务时抛出。"""


class SubAgentRunner:
    def __init__(self, cfg, task_id):
        self.cfg = cfg
        self.task_id = task_id
        self.toolbox = ToolBox(cfg)

    # ---- 状态同步 ----
    def _state(self, status=None, step=None, message=None):
        set_state(self.task_id, status=status, step=step, message=message)
        fields = {}
        if status:
            fields["status"] = status
        if step is not None:
            fields["steps"] = step
        if fields:
            update_meta(self.task_id, **fields)

    def _check_control(self, messages):
        """检查主 Agent 的控制信号：暂停则原地等待，停止则抛异常，有新指令则注入对话。"""
        while True:
            ctl = read_control(self.task_id)
            desired = ctl.get("desired", "run")

            if desired == "stop":
                raise TaskStopped()

            if desired == "pause":
                self._state(status=STATUS_PAUSED, message="已暂停，等待主 Agent 恢复")
                time.sleep(1.0)
                continue

            # desired == run
            instructions = ctl.get("instructions", [])
            consumed = int(ctl.get("consumed", 0))
            if len(instructions) > consumed:
                for item in instructions[consumed:]:
                    text = item.get("text", "") if isinstance(item, dict) else str(item)
                    messages.append({"role": "user",
                                     "content": f"[主Agent新指令] {text}"})
                    append_log(self.task_id, {"type": "instruction_injected", "content": text})
                ctl["consumed"] = len(instructions)
                write_control(self.task_id, ctl)
            return

    # ---- 主循环 ----
    def run(self):
        if not self.cfg.get("api_key"):
            raise RuntimeError(
                "未配置 API Key。请先执行：\n"
                "  python subagent.py config --set api_key=<你的KEY>\n"
                "或打开 Qt 界面：python subagent.py ui")

        from openai import OpenAI   # 放到校验之后，避免没配 Key 时白白冷启动导入

        meta = load_meta(self.task_id)
        if not meta:
            raise RuntimeError(f"任务不存在: {self.task_id}")

        client = OpenAI(api_key=self.cfg["api_key"],
                        base_url=self.cfg.get("base_url") or None)
        tools = build_tools(self.cfg)
        max_steps = int(self.cfg.get("max_steps", 30))

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": meta["prompt"]},
        ]
        append_log(self.task_id, {"type": "system", "content": "副 Agent 开始执行任务",
                                  "model": self.cfg.get("model")})

        step = 0
        try:
            while step < max_steps:
                self._check_control(messages)
                step += 1
                self._state(status=STATUS_RUNNING, step=step, message=f"第 {step} 步：请求模型")

                resp = client.chat.completions.create(
                    model=self.cfg.get("model"),
                    messages=messages,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                    temperature=float(self.cfg.get("temperature", 0.3)),
                )
                msg = resp.choices[0].message
                assistant = {"role": "assistant", "content": msg.content or ""}
                if getattr(msg, "tool_calls", None):
                    assistant["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name,
                                      "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ]
                messages.append(assistant)
                append_log(self.task_id, {"type": "assistant", "step": step,
                                          "content": msg.content or "",
                                          "tool_calls": [t["function"]["name"]
                                                         for t in assistant.get("tool_calls", [])]})

                # 没有工具调用 = 模型认为任务完成
                if not getattr(msg, "tool_calls", None):
                    self._finish(STATUS_DONE, msg.content or "")
                    return

                for tc in msg.tool_calls:
                    self._check_control(messages)
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    self._state(status=STATUS_RUNNING, step=step, message=f"第 {step} 步：调用 {name}")
                    append_log(self.task_id, {"type": "tool_call", "step": step,
                                              "name": name, "args": args})
                    try:
                        result = self.toolbox.dispatch(name, args)
                    except Exception as e:
                        result = {"error": str(e)}
                        append_log(self.task_id, {"type": "error", "step": step,
                                                  "content": traceback.format_exc()})
                    append_log(self.task_id, {"type": "tool_result", "step": step,
                                              "name": name, "result": result})
                    content = json.dumps(result, ensure_ascii=False, default=str)
                    if len(content) > 12000:
                        content = content[:12000] + "...(已截断)"
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

            # 步数用尽
            self._finish(STATUS_FAILED, "", error=f"达到最大步数({max_steps})仍未完成，已中止")

        except TaskStopped:
            self._finish(STATUS_STOPPED, "", error="已被主 Agent 停止")
        except Exception as e:
            detail = traceback.format_exc()
            append_log(self.task_id, {"type": "error", "content": detail})
            self._finish(STATUS_FAILED, "", error=f"{type(e).__name__}: {e}")

    def _finish(self, status, result_text, error=None):
        if result_text:
            try:
                with open(_result_path(self.task_id), "w", encoding="utf-8") as f:
                    f.write(result_text)
            except Exception:
                pass
        append_log(self.task_id, {"type": "finish", "status": status,
                                  "result": result_text, "error": error})
        update_meta(self.task_id, status=status, result=result_text,
                    error=error, finished_at=now_ts())
        set_state(self.task_id, status=status, message=error or "任务结束")


# ---------------------------------------------------------------- 日志格式化

def format_log(events):
    lines = []
    for e in events:
        ts = fmt_ts(e.get("ts"))
        kind = e.get("type")
        if kind == "system":
            lines.append(f"[{ts}] · {e.get('content', '')}")
        elif kind == "assistant":
            content = (e.get("content") or "").strip()
            calls = e.get("tool_calls") or []
            if content:
                lines.append(f"[{ts}] 🧠 {content}")
            if calls:
                lines.append(f"[{ts}] 🧠 → 调用工具: {', '.join(calls)}")
        elif kind == "tool_call":
            lines.append(f"[{ts}] 🔧 {e.get('name')} {json.dumps(e.get('args', {}), ensure_ascii=False)}")
        elif kind == "tool_result":
            body = json.dumps(e.get("result", {}), ensure_ascii=False, default=str)
            if len(body) > 500:
                body = body[:500] + "..."
            lines.append(f"[{ts}] 📤 {e.get('name')} -> {body}")
        elif kind == "instruction":
            lines.append(f"[{ts}] 📥 主Agent指令: {e.get('content')}")
        elif kind == "instruction_injected":
            lines.append(f"[{ts}] ✅ 已注入主Agent指令: {e.get('content')}")
        elif kind == "error":
            lines.append(f"[{ts}] ❌ {e.get('content', '')}")
        elif kind == "finish":
            lines.append(f"[{ts}] 🏁 结束({e.get('status')}) {e.get('error') or ''} {e.get('result') or ''}")
        else:
            lines.append(f"[{ts}] {json.dumps(e, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _build_parser():
    p = argparse.ArgumentParser(prog="subagent", description="副 Agent — 任务派发与调度")
    sub = p.add_subparsers(dest="cmd")

    cfg = sub.add_parser("config", help="查看 / 修改配置（api_key、model、base_url 等）")
    cfg.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                     help="设置字段，可重复，如 --set api_key=sk-xxx --set tools.shell=false")
    cfg.add_argument("--show", action="store_true", help="打印当前配置（api_key 打码）")

    run = sub.add_parser("run", help="派发一个任务给副 Agent")
    run.add_argument("--task", required=True, help="任务内容")
    run.add_argument("--title", help="任务标题（默认取任务首行）")
    run.add_argument("--foreground", action="store_true", help="前台阻塞执行（默认后台）")

    sub.add_parser("list", help="列出全部任务")

    st = sub.add_parser("status", help="查看某个任务的状态")
    st.add_argument("task_id")

    lg = sub.add_parser("logs", help="查看某个任务的日志")
    lg.add_argument("task_id")
    lg.add_argument("--tail", type=int, default=50, help="只看最后 N 条")
    lg.add_argument("--raw", action="store_true", help="输出原始 jsonl")

    for name, helptext in (("pause", "暂停任务"), ("resume", "恢复任务"), ("stop", "停止任务")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("task_id")

    up = sub.add_parser("update", help="给运行中的任务追加指令")
    up.add_argument("task_id")
    up.add_argument("--instruction", required=True, help="新指令内容")

    rm = sub.add_parser("rm", help="删除任务记录")
    rm.add_argument("task_id")

    sub.add_parser("ui", help="打开 Qt 图形界面")

    # 内部命令：由 spawn() 调用，不要手动使用
    wk = sub.add_parser("_worker", help=argparse.SUPPRESS)
    wk.add_argument("--task-id", required=True)

    return p


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "config":
        if args.set:
            updates = {}
            for item in args.set:
                if "=" not in item:
                    print(f"忽略无效项（应为 key=value）: {item}", file=sys.stderr)
                    continue
                k, v = item.split("=", 1)
                k = k.strip()
                val = _coerce(v)
                if k.startswith("tools."):
                    updates.setdefault("tools", {})[k.split(".", 1)[1]] = val
                else:
                    updates[k] = val
            save_config(updates)
        cfg = load_config()
        if cfg.get("api_key"):
            key = cfg["api_key"]
            cfg["api_key"] = key[:6] + "..." + key[-4:] if len(key) > 12 else "***"
        _print(cfg)
        return

    if args.cmd == "run":
        tid = create_task(args.task, args.title)
        if args.foreground:
            print(f"[foreground] 任务 {tid} 开始执行，请稍候…", file=sys.stderr)
            SubAgentRunner(load_config(), tid).run()
            _print(task_status(tid))
        else:
            pid = spawn(tid)
            _print({"task_id": tid, "status": STATUS_RUNNING, "pid": pid,
                    "hint": f"用 `python subagent.py status {tid}` 查看进度"})
        return

    if args.cmd == "list":
        tasks = list_tasks()
        _print([{k: t.get(k) for k in
                 ("id", "title", "status", "steps", "created_at", "updated_at", "error")}
                for t in tasks])
        return

    if args.cmd == "status":
        st = task_status(args.task_id)
        if st is None:
            _print({"ok": False, "error": f"任务不存在: {args.task_id}"})
        else:
            st.pop("dir", None)
            _print(st)
        return

    if args.cmd == "logs":
        events = read_log(args.task_id, None if args.raw else args.tail)
        if args.raw:
            for e in events:
                print(json.dumps(e, ensure_ascii=False, default=str))
        else:
            print(format_log(events) or "(暂无日志)")
        return

    if args.cmd == "pause":
        _print(request_pause(args.task_id))
        return
    if args.cmd == "resume":
        _print(request_resume(args.task_id))
        return
    if args.cmd == "stop":
        _print(request_stop(args.task_id))
        return
    if args.cmd == "update":
        _print(request_instruction(args.task_id, args.instruction))
        return
    if args.cmd == "rm":
        _print(remove_task(args.task_id))
        return

    if args.cmd == "ui":
        from subagent_ui import main as ui_main
        ui_main()
        return

    if args.cmd == "_worker":
        try:
            SubAgentRunner(load_config(), args.task_id).run()
        except Exception as e:
            append_log(args.task_id, {"type": "error", "content": traceback.format_exc()})
            update_meta(args.task_id, status=STATUS_FAILED,
                        error=f"{type(e).__name__}: {e}", finished_at=now_ts())
            set_state(args.task_id, status=STATUS_FAILED, message=str(e))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
