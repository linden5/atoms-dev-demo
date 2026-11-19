# AGENTS.md

面向在此仓库中协作的 AI Agent 的工作约定（格式参考 [agents.md](https://agents.md/)，即给 agent 看的 README）。开始任何改动前先读这里，再读下面的必读文档。

**纪律的单一出处是 [docs/agent/discipline.md](docs/agent/discipline.md)**（硬性约束 / 禁止事项 / 每步工作流），本文件只保留概览与状态，规则不在这里重复。

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

## 当前状态

| 步骤 | 内容 | 状态 |
|---|---|---|
| S1 | 静态单页 + 演示数据 | 已完成，tag `s1` |
| S2 | FastAPI：`/health` + 静态托管 | 已完成，tag `s2` |
| S3 | 任务 CRUD + SQLite + MOCK 引擎 | 已完成，tag `s3` |
| S4 | 接入 MetaGPT 引擎（与 MOCK 可切换） | 已完成，tag `s4`；真实生成待装 3.11 + metagpt 验证 |
| S5 | 继续修改 + 版本历史（parent 链） | 已完成，tag `s5` |
| S6 | 加固、交付文档与全量回归 | 已完成，tag `s6` / `v1.0` |
| S7 | 多轮对话（会话 / 消息 / 追问 / 上下文） | 已完成，tag `s7` |
| S8 | 真实 LLM 生成（三阶段）+ 发布/分享闭环 + 版本叙事 | 代码完成，本地 `verify_s8.sh` 38/38；**公网部署与真 Key 待执行**，见 [docs/steps/s8.md](docs/steps/s8.md) |

后续工作：

- **S8（代码完成，待公网部署）**：`app/engine/llm_engine.py` 已串起 planning / coding / checking 三次调用，`ENGINE_MODE=llm|mock|metagpt|auto` 可切换；`publishes` 表与 `/p/{slug}` 发布闭环已可用。**剩下的事只有部署**：配好 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 重启，确认公网 `/health` 为 `step=s8 engine=llm`，再跑 `bash scripts/verify_s8.sh`。没有 Key 的环境可用 `python scripts/fake_llm.py` 验证引擎交互。落地记录见 [docs/steps/s8.md](docs/steps/s8.md)。
- MetaGPT 路线（`docs/delivery.md` 第 7 节）不再是 P0：依赖重、时延不可控，仅在有富余时间时用 `scripts/fake_metagpt.py` 继续验证交互，不阻塞交付。
- P1 仍是流式输出与 LLM 澄清（`docs/design.md` 4.7 的 `clarify()` 口子）。

详见 [docs/roadmap.md](docs/roadmap.md)。
