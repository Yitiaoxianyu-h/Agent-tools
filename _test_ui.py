# -*- coding: utf-8 -*-
"""临时自测：离屏模式（offscreen）验证 Qt 界面能否正常构建，不需要真实显示器。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import subagent_ui  # noqa: E402

app = QApplication([])
win = subagent_ui.MainWindow()
win.show()

print("窗口标题:", win.windowTitle())
print("任务表格行数:", win.table.rowCount())
print("当前配置 -> model:", win.model_edit.text(), "| base_url:", win.base_url_edit.text())
print("工具开关 -> 桌面:", win.desktop_chk.isChecked(),
      "shell:", win.shell_chk.isChecked(), "文件:", win.file_chk.isChecked())

# 模拟一次刷新 tick（会读 tasks/ 目录）
win._tick()
print("tick 后行数:", win.table.rowCount())

print("离屏构建成功 ✔")
app.quit()
