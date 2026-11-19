# AGENTS.md

面向在此仓库中协作的 AI Agent 的工作约定（格式参考 [agents.md](https://agents.md/)，即给 agent 看的 README）。开始任何改动前先读这里，再读下面的必读文档。

**纪律的单一出处是 [docs/agent/discipline.md](docs/agent/discipline.md)**（硬性约束 / 禁止事项 / 每步工作流），本文件只保留概览，规则与项目进度不在这里重复。

## 项目是什么

Atoms Demo：输入自然语言需求，由智能体（MetaGPT）生成可运行的网页应用，右侧沙箱预览，可继续迭代，历史版本可回退。技术栈 FastAPI + SQLite + MetaGPT（Python 3.11）+ 原生前端。

## 必读文档

| 文档 | 什么时候读 |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | 每次开工前：当前处在哪一步、该步的验收标准与回滚方式 |
| **[docs/agent/discipline.md](docs/agent/discipline.md)** | **开工前必读**：硬性约束、禁止事项、每步工作流（纪律的唯一出处） |
| [docs/design.md](docs/design.md) | 涉及接口契约、数据模型、状态机、安全策略时；实现与它冲突时以它为准 |
| **[docs/agent/git-commit.md](docs/agent/git-commit.md)** | **任何 git 提交之前必读**，提交信息必须符合该规范 |
| [docs/setup.md](docs/setup.md) / [docs/setup-windows.md](docs/setup-windows.md) | 需要装环境、跑服务、查端口时 |
| [docs/dev-debug.md](docs/dev-debug.md) | 改完代码怎么验证、问题在哪一层、引擎怎么排障 |
| [docs/metagpt-docker.md](docs/metagpt-docker.md) | 想接 MetaGPT 时：pip 全版本装不上的证据 + Docker 旁挂方案（产物落在本地） |
| `docs/steps/sN.md` | 了解某一步具体改了什么、怎么部署与回滚 |

## 当前状态与后续工作

不在这里维护：进度跟踪表与后续工作以 [docs/roadmap.md](docs/roadmap.md) 第 5 节为准，每次开工前先读它确认当前步骤、验收标准与回滚方式。
