# 项目进度记录（给下一个 AI 看）

> 规则：每完成一项就**立刻**在这里记一条，避免后续 AI 重复劳动。
> 最新进度放最上面。

## 进行中

- [ ] 新增「副 Agent（SubAgent）+ Qt UI」功能（本次需求）

## 本次需求拆解

需求原文：基于开源项目的思路，给本项目写一个 Qt UI 界面；在 UI 里配置**统一的副 Agent API Key**；
让**主 Agent 通过终端等方式调用副 Agent 执行部分任务**；主 Agent 可**随时查看 / 修改 / 暂停**副 Agent 的任务；
并在 README 中补充实现方式（主 Agent 怎么调用）。

已与用户确认的技术选型：
- 副 Agent 引擎：**自研 OpenAI 兼容 Agent Loop**（用已安装的 `openai` SDK，支持任意 OpenAI 兼容端点）
- 副 Agent 可用能力：**桌面操控（复用 agent_tools）+ Shell 命令 + 文件读写**
- UI 框架：**PySide6**（环境已装 6.11）

## 已完成

（暂无）

## 关键约定（后续 AI 请遵守）

- 所有文件、依赖、产物**一律放在项目目录内**，禁止写入 C 盘。
- 新增文件规划：
  - `subagent.py` —— 副 Agent 引擎 + CLI（配置、任务持久化、控制信号、Agent Loop、工具集）
  - `subagent_ui.py` —— PySide6 图形界面（配置 API Key、任务管理）
  - `config.json` —— 运行时生成的配置（API Key 等），与 UI/CLI 共享
  - `tasks/` —— 任务持久化目录（每个任务一个子目录）
  - `workspace/` —— 副 Agent 的默认工作目录
- `agent_tools.py` 只做**最小改动**：新增 `subagent` 子命令转发，不改动原有任何命令行为。
- 主 Agent 与副 Agent 之间通过**文件信号**通信（`control.json` 表达"暂停/恢复/停止/追加指令"），
  保证跨进程、跨终端都能随时干预。
