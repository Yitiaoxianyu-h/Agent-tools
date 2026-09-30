# -*- coding: utf-8 -*-
"""临时自测：走 agent_tools.py 转发，验证主 Agent 终端调用链路（不需要 API Key）。

注意：没配 Key 时副 Agent 进程会在启动后立刻失败落盘，所以这里只断言
不依赖时序的字段（pid 记录、命令返回 ok/明确的错误说明），不断言进程存活。
"""
import json
import os
import subprocess
import sys

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

# 2) 查状态：pid 应已记录（worker 可能因没配 Key 已快速失败，不断言存活）
s = cli("status", tid)
print("2) status ->", {"status": s["status"], "pid": s.get("pid"),
                       "worker_alive": s.get("worker_alive")})
assert s.get("pid") == pid, "meta 里记录的 pid 与 run 返回的不一致"

# 3) 暂停（若 worker 已失败会得到明确的拒绝说明，也算通过）
p = cli("pause", tid)
print("3) pause ->", p)
assert p.get("ok") in (True, None) or "已结束" in p.get("error", "")

# 4) 恢复（仅当上一步暂停成功；未配 Key 时 worker 大概率已自己失败）
if p.get("ok"):
    cli("resume", tid)
    s = cli("status", tid)
    print("4) resume 后 ->", {"status": s["status"], "pid": s.get("pid")})

# 5) 停止 + 删除清理
print("5) stop ->", cli("stop", tid))
print("6) rm ->", cli("rm", tid)["ok"])
print("CLI 冒烟通过 ✔")
