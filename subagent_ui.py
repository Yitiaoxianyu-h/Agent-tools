# -*- coding: utf-8 -*-
"""
SubAgent 控制台（PySide6）
=========================

一个图形界面，用来：
1. **统一配置副 Agent 的 API Key**（以及 base_url / model / 工具开关 / 工作目录）——
   配置直接写进项目根目录的 `config.json`，和命令行 `python subagent.py` 完全共享。
2. 查看所有副 Agent 任务，并**随时新建 / 暂停 / 恢复 / 停止 / 追加指令 / 删除**。

界面本身不执行任务，只做「配置 + 调度」：新建任务时会用 `subagent.spawn()` 起一个
独立的副 Agent 进程，因此界面上看到的任务状态，和主 Agent 通过终端看到的完全一致。

启动：
    python subagent_ui.py
    python subagent.py ui          # 等价
"""
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import subagent

# 状态 -> (中文名, 颜色)
STATUS_STYLE = {
    subagent.STATUS_PENDING: ("排队中", "#8a8a8a"),
    subagent.STATUS_RUNNING: ("执行中", "#1a7f37"),
    subagent.STATUS_PAUSED: ("已暂停", "#b26a00"),
    subagent.STATUS_DONE: ("已完成", "#0b5cad"),
    subagent.STATUS_FAILED: ("失败", "#c0392b"),
    subagent.STATUS_STOPPED: ("已停止", "#6c6c6c"),
}


def _fmt_time(ts):
    if not ts:
        return "-"
    return subagent.fmt_ts(ts)


class NewTaskDialog(QDialog):
    """新建任务对话框：填标题 + 任务内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建副 Agent 任务")
        self.resize(560, 380)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("可留空，默认取任务首行")

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "描述要交给副 Agent 的子任务，例如：\n"
            "打开记事本，输入 hello world，然后截图保存到 workspace/notepad.png")

        form = QFormLayout()
        form.addRow("任务标题", self.title_edit)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("任务内容"))
        layout.addWidget(self.prompt_edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.title_edit.text().strip(), self.prompt_edit.toPlainText().strip()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SubAgent 控制台 — Agent Tools")
        self.resize(1180, 800)

        self.current_task = None
        self._last_log_text = ""

        self._build_ui()
        self._load_config_to_form()
        self.refresh_tasks()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1500)   # 每 1.5 秒刷新一次任务状态与日志

    # ------------------------------------------------------------ 界面搭建
    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_task_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 480])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("就绪")

    def _build_config_panel(self):
        box = QGroupBox("副 Agent 配置（保存后写入 config.json，与命令行共享）")
        form = QFormLayout(box)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("如 https://api.deepseek.com/v1（需带 /v1）")

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-...（所有副 Agent 任务共用这一个 Key）")
        show_key = QCheckBox("显示")
        show_key.toggled.connect(
            lambda on: self.api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(show_key)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("如 deepseek-chat / gpt-4o-mini")

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)

        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 500)

        self.shell_spin = QSpinBox()
        self.shell_spin.setRange(5, 3600)
        self.shell_spin.setSuffix(" 秒")

        self.workspace_edit = QLineEdit()
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._pick_workspace)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.workspace_edit, 1)
        ws_row.addWidget(browse)

        tools_row = QHBoxLayout()
        self.desktop_chk = QCheckBox("桌面操控")
        self.shell_chk = QCheckBox("Shell 命令")
        self.file_chk = QCheckBox("文件读写")
        for c in (self.desktop_chk, self.shell_chk, self.file_chk):
            tools_row.addWidget(c)
        tools_row.addStretch(1)

        form.addRow("Base URL", self.base_url_edit)
        form.addRow("API Key", key_row)
        form.addRow("模型", self.model_edit)
        form.addRow("温度", self.temp_spin)
        form.addRow("最大步数", self.steps_spin)
        form.addRow("Shell 超时", self.shell_spin)
        form.addRow("工作目录", ws_row)
        form.addRow("启用工具", tools_row)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self._save_config)
        reload_btn = QPushButton("重新载入")
        reload_btn.clicked.connect(self._load_config_to_form)
        btn_row.addStretch(1)
        btn_row.addWidget(reload_btn)
        btn_row.addWidget(save_btn)
        form.addRow("", btn_row)
        return box

    def _build_task_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        for text, slot in (
            ("新建任务", self._new_task),
            ("刷新", self.refresh_tasks),
            ("暂停", lambda: self._signal("pause")),
            ("恢复", lambda: self._signal("resume")),
            ("停止", lambda: self._signal("stop")),
            ("追加指令", self._append_instruction),
            ("打开任务目录", self._open_task_dir),
            ("删除", self._delete_task),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["任务ID", "标题", "状态", "步数", "更新时间", "错误"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table, 3)

        log_header = QHBoxLayout()
        self.log_label = QLabel("任务日志（未选中任务）")
        log_header.addWidget(self.log_label)
        log_header.addStretch(1)
        layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_view, 4)
        return panel

    # ------------------------------------------------------------ 配置读写
    def _load_config_to_form(self):
        cfg = subagent.load_config()
        self.base_url_edit.setText(cfg.get("base_url", ""))
        self.api_key_edit.setText(cfg.get("api_key", ""))
        self.model_edit.setText(cfg.get("model", ""))
        self.temp_spin.setValue(float(cfg.get("temperature", 0.3)))
        self.steps_spin.setValue(int(cfg.get("max_steps", 30)))
        self.shell_spin.setValue(int(cfg.get("shell_timeout", 60)))
        self.workspace_edit.setText(cfg.get("workspace", ""))
        tools = cfg.get("tools", {})
        self.desktop_chk.setChecked(bool(tools.get("desktop", True)))
        self.shell_chk.setChecked(bool(tools.get("shell", True)))
        self.file_chk.setChecked(bool(tools.get("file", True)))
        self.statusBar().showMessage("配置已载入")

    def _save_config(self):
        workspace = self.workspace_edit.text().strip() or subagent.DEFAULT_WORKSPACE
        cfg = {
            "base_url": self.base_url_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "temperature": round(self.temp_spin.value(), 2),
            "max_steps": self.steps_spin.value(),
            "shell_timeout": self.shell_spin.value(),
            "workspace": workspace,
            "tools": {
                "desktop": self.desktop_chk.isChecked(),
                "shell": self.shell_chk.isChecked(),
                "file": self.file_chk.isChecked(),
            },
        }
        try:
            os.makedirs(workspace, exist_ok=True)
            subagent.save_config(cfg)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.workspace_edit.setText(workspace)
        self.statusBar().showMessage(f"配置已保存到 {subagent.CONFIG_PATH}")
        QMessageBox.information(self, "已保存",
                                f"配置已写入：\n{subagent.CONFIG_PATH}")

    def _pick_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作目录",
                                                self.workspace_edit.text() or subagent.ROOT)
        if path:
            self.workspace_edit.setText(os.path.normpath(path))

    # ------------------------------------------------------------ 任务刷新
    def _tick(self):
        self.refresh_tasks(keep_selection=True)
        self._refresh_log()

    def refresh_tasks(self, keep_selection=False):
        selected = self.current_task if keep_selection else None
        tasks = subagent.list_tasks()

        self.table.blockSignals(True)
        self.table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            status = t.get("status", subagent.STATUS_PENDING)
            label, color = STATUS_STYLE.get(status, (status, "#333333"))
            cells = [
                t.get("id", ""),
                t.get("title", ""),
                label,
                str(t.get("steps", 0)),
                _fmt_time(t.get("updated_at")),
                t.get("error") or "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 2:
                    item.setForeground(QColor(color))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, t.get("id"))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

        # 恢复选中行
        target_row = 0 if tasks else -1
        if selected:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == selected:
                    target_row = row
                    break
        if target_row >= 0:
            self.table.selectRow(target_row)
        elif not tasks:
            self.current_task = None
            self.log_label.setText("任务日志（未选中任务）")
        self.statusBar().showMessage(f"共 {len(tasks)} 个任务")

    def _on_select(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        self.current_task = item.text() if item else None
        self._last_log_text = ""
        self._refresh_log()

    def _refresh_log(self):
        if not self.current_task:
            return
        events = subagent.read_log(self.current_task, tail=400)
        text = subagent.format_log(events) or "(暂无日志)"
        meta = subagent.load_meta(self.current_task) or {}
        result = meta.get("result")
        if result:
            text += "\n\n===== 最终结果 =====\n" + result
        if text != self._last_log_text:
            self._last_log_text = text
            scrollbar = self.log_view.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
            self.log_view.setPlainText(text)
            if at_bottom:
                scrollbar.setValue(scrollbar.maximum())
        self.log_label.setText(f"任务日志 — {self.current_task}")

    # ------------------------------------------------------------ 任务操作
    def _new_task(self):
        cfg = subagent.load_config()
        if not cfg.get("api_key"):
            if QMessageBox.question(
                    self, "尚未配置 API Key",
                    "还没有配置副 Agent 的 API Key，任务会因为无法调用模型而失败。\n"
                    "仍要创建吗？") != QMessageBox.StandardButton.Yes:
                return
        dlg = NewTaskDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title, prompt = dlg.values()
        if not prompt:
            QMessageBox.warning(self, "任务内容为空", "请填写任务内容。")
            return
        task_id = subagent.create_task(prompt, title or None)
        pid = subagent.spawn(task_id)
        self.current_task = task_id
        self.refresh_tasks(keep_selection=True)
        self._refresh_log()
        self.statusBar().showMessage(f"已派发任务 {task_id}（副 Agent pid={pid}）")

    def _signal(self, action):
        if not self.current_task:
            QMessageBox.information(self, "未选中任务", "请先在列表里选中一个任务。")
            return
        fn = {"pause": subagent.request_pause,
              "resume": subagent.request_resume,
              "stop": subagent.request_stop}[action]
        result = fn(self.current_task)
        if not result.get("ok"):
            QMessageBox.warning(self, "操作失败", result.get("error", "未知错误"))
        else:
            self.statusBar().showMessage(f"{action} 已发送 -> {self.current_task}")
        self.refresh_tasks(keep_selection=True)
        self._refresh_log()

    def _append_instruction(self):
        if not self.current_task:
            QMessageBox.information(self, "未选中任务", "请先在列表里选中一个任务。")
            return
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(self, "追加指令",
                                                 f"给任务 {self.current_task} 追加新指令：")
        if not ok or not text.strip():
            return
        result = subagent.request_instruction(self.current_task, text.strip())
        if not result.get("ok"):
            QMessageBox.warning(self, "操作失败", result.get("error", "未知错误"))
        else:
            self.statusBar().showMessage("指令已写入，副 Agent 将在下一步读取")
        self._refresh_log()

    def _open_task_dir(self):
        if not self.current_task:
            QMessageBox.information(self, "未选中任务", "请先在列表里选中一个任务。")
            return
        path = subagent._task_dir(self.current_task)
        if not os.path.isdir(path):
            QMessageBox.warning(self, "目录不存在", path)
            return
        os.startfile(path)   # noqa: S606  Windows 专用

    def _delete_task(self):
        if not self.current_task:
            QMessageBox.information(self, "未选中任务", "请先在列表里选中一个任务。")
            return
        if QMessageBox.question(self, "确认删除",
                                f"删除任务 {self.current_task} 的全部记录？") \
                != QMessageBox.StandardButton.Yes:
            return
        subagent.remove_task(self.current_task)
        self.current_task = None
        self._last_log_text = ""
        self.log_view.clear()
        self.refresh_tasks()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
