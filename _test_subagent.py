# -*- coding: utf-8 -*-
"""临时自测：验证 subagent 的任务创建与控制信号（不需要 API Key、不起真进程）。"""
import json
import subagent as sa

# 把 spawn 换成纯内存桩：只写一个假 pid，不真的启动进程，保证测试确定性
def fake_spawn(task_id):
    sa.update_meta(task_id, pid=424242)
    sa.set_state(task_id, status=sa.STATUS_RUNNING, message="stub spawn")
    return 424242

sa.spawn = fake_spawn

tid = sa.create_task("自测任务：验证控制信号", "自测")
print("task_id =", tid)

print("1) 初始状态:", sa.task_status(tid)["status"])

print("2) pause ->", sa.request_pause(tid)["ok"],
      "| 状态:", sa.task_status(tid)["status"],
      "| control:", json.load(open(sa._control_path(tid), encoding="utf-8"))["desired"])

print("3) update ->", sa.request_instruction(tid, "把结果写到 workspace/out.txt")["ok"])

st = sa.request_resume(tid)
print("4) resume（原进程不存在，应自动重拉）->", st["ok"],
      "| 状态:", sa.task_status(tid)["status"], "| pid:", sa.task_status(tid).get("pid"))

print("5) stop ->", sa.request_stop(tid)["ok"], "| 状态:", sa.task_status(tid)["status"])

print("6) 已结束任务再 resume:", sa.request_resume(tid).get("error"))
print("7) 已结束任务再 pause:", sa.request_pause(tid).get("error"))

print("8) list 数量:", len(sa.list_tasks()))
print("9) 日志:\n" + sa.format_log(sa.read_log(tid)))

print("10) 清理:", sa.remove_task(tid)["ok"], "| 剩余任务:", len(sa.list_tasks()))
