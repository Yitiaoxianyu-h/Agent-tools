# -*- coding: utf-8 -*-
"""临时自测：走 agent_tools.py 转发，验证主 Agent 终端调用链路（不需要 API Key）。"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def cli(*args):
    p = subprocess.run([PY, "agent_tools.py", "subagent", *args],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, f"命令失败: {args}\nstdout={p.stdout}\nstderr={p.stderr}"
    return json.loads(p.stdout)


# 1) 派发任务（后台起独立进程）
r = cli("run", "--task", "冒烟测试：验证主 Agent 的终端调用链路", "--title", "cli-smoke")
tid, pid = r["task_id"], r["pid"]
print("1) run ->", {"task_id": tid, "pid": pid, "status": r["status"]})

# 2) 查状态：应 running 且进程存活
s = cli("status", tid)
print("2) status ->", s["status"], "| pid:", s.get("pid"), "| worker_alive:", s.get("worker_alive"))
assert s["status"] == "running" and s.get("worker_alive") is True

# 3) 暂停
print("3) pause ->", cli("pause", tid)["ok"])

# 4) 强杀 worker 进程，模拟进程死亡（断电/被杀）
subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
time.sleep(0.5)
s = cli("status", tid)
print("4) 杀进程后 ->", s["status"], "| worker_alive:", s.get("worker_alive"))
assert s["status"] == "paused" and s.get("worker_alive") is False

# 5) resume：进程已死，应自动重新拉起（拿到新 pid）
cli("resume", tid)
s = cli("status", tid)
print("5) resume 后 ->", s["status"], "| 新 pid:", s.get("pid"), "| pid已变:", s.get("pid") != pid)
assert s["status"] == "running" and s.get("pid") and s["pid"] != pid

# 6) 停止并清理
print("6) stop ->", cli("stop", tid)["ok"])
print("7) rm ->", cli("rm", tid)["ok"])
print("CLI 冒烟通过 ✔")
