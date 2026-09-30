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

### 1) `subagent.py` —— 副 Agent 引擎 + CLI（已完成，已自测）

- 配置管理：`config.json`（默认值 ← 文件 ← 环境变量），支持 `SUBAGENT_API_KEY` /
  `OPENAI_API_KEY` / `SUBAGENT_BASE_URL` / `OPENAI_BASE_URL` / `SUBAGENT_MODEL` 覆盖。
- 任务持久化：`tasks/<task_id>/` 下 `meta.json` / `state.json` / `control.json` /
  `log.jsonl` / `result.md`；写文件用「临时文件 + 原子替换」，避免主 Agent 读到半截。
- 控制信号（跨进程）：`control.json.desired = run|pause|stop` + `instructions`（追加指令，
  用 `consumed` 计数避免重复注入）。副 Agent 在每个步骤前后都会 `_check_control`。
- Agent Loop：OpenAI 兼容 Chat Completions + function calling，无工具调用即视为完成；
  `max_steps` 兜底；异常/停止/超步数都会正确落盘状态。
- 工具集：`desktop`（复用 `agent_tools.AgentTools`，26 个 action）、`shell`（PowerShell，
  默认 cwd=workspace）、`read_file` / `write_file`（相对路径基于 workspace）。工具按
  `config.tools.*` 开关动态生成。
- CLI：`config / run / list / status / logs / pause / resume / stop / update / rm / ui`，
  以及内部命令 `_worker`；`run` 默认后台（`spawn()` 用 DETACHED_PROCESS 起独立进程），
  加 `--foreground` 可前台阻塞。
- 已自测通过：`config --show`、`run`（后台起进程）、`status`、`logs`、pause/resume/stop、
  update 追加指令、rm 清理、已结束任务拒绝 resume。

### 踩坑记录（后续 AI 注意）

- `spawn()` 起来的 detached 进程**冷启动较慢**（首次 `import openai` 可能要几十秒），
  所以 `spawn()` 里会**立刻**把 meta 状态写成 running，不等子进程自己更新。
- `run()` 里 `from openai import OpenAI` 必须放在 api_key 校验**之后**，否则没配 Key 时
  也会白白冷启动导入。
- `AgentTools.activate/move/resize/set_topmost/close_window/click_on_window` 都**同时接受
  字符串标题和 WindowInfo**（内部会 `find_window`），所以传 title 即可。
- `_set_desired()` 会立刻改 meta.status（不等副 Agent 醒来），主 Agent 查询零延迟。

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
