# -*- coding: utf-8 -*-
"""临时自测：验证 subagent 的任务创建与控制信号（不需要 API Key）。"""
import json
import subagent as sa

tid = sa.create_task("自测任务：验证控制信号", "自测")
print("task_id =", tid)

print("1) 初始状态:", sa.task_status(tid)["status"])

print("2) pause ->", sa.request_pause(tid))
print("   状态:", sa.task_status(tid)["status"], "| control:",
      json.load(open(sa._control_path(tid), encoding="utf-8"))["desired"])

print("3) update ->", sa.request_instruction(tid, "把结果写到 workspace/out.txt"))

print("4) resume ->", sa.request_resume(tid))
print("   状态:", sa.task_status(tid)["status"])

print("5) stop ->", sa.request_stop(tid))
print("   状态:", sa.task_status(tid)["status"])

print("6) 已结束任务再 resume:", sa.request_resume(tid))

print("7) list 数量:", len(sa.list_tasks()))
print("8) 日志:\n" + sa.format_log(sa.read_log(tid)))

print("9) 清理:", sa.remove_task(tid))
print("   剩余任务:", len(sa.list_tasks()))
