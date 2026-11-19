# Atoms Demo

输入一句自然语言需求，由智能体生成一个可运行的网页应用，在右侧沙箱里实时预览；不满意就继续提需求，每次修改产生新版本，历史版本随时切回。

当前交付版本：**S8（代码完成）**。真实 LLM 三阶段生成 + 发布/分享闭环已可用；任务、产物与版本链持久化在服务端（SQLite + 本地磁盘），刷新或重启进程都不丢。

## 能做什么

| 能力 | 现状 |
|---|---|
| 需求 → 生成 | ✅ 引擎可切换：LLM 三阶段（真实）/ MetaGPT / MOCK（无额度兜底） |
| 发布/分享 | ✅ `publishes` 表 + `/p/{slug}` 静态发布页 |
| 状态可观测 | ✅ 三阶段卡片 + 事件表 + 失败原因 + 取消 |
| 沙箱预览 | ✅ iframe `sandbox="allow-scripts"` + 独立 CSP，可切 375/768/1000/自适应 |
| 继续修改 | ✅ 以当前版本为父任务生成新版本 |
| 版本历史 | ✅ 版本链 `v1…vN`，点击切回旧产物 |
| 持久化 | ✅ SQLite（WAL）+ `workspace/{task_id}/` |

## 快速开始

```bash
# 1) 环境（S2/S3 只需基础依赖；S4 起需要 Python 3.11 + metagpt）
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 2) 启动（单 worker 是硬性约束，不要加 --workers >1）
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Windows PowerShell 等价写法
# .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`；`/health` 看运行状态，`/api/docs` 看接口。

引擎选择（`ENGINE_MODE`）：

| 取值 | 行为 |
|---|---|
| 留空（默认） | auto：`LLM_*` 配齐用 LLM → 能导入 MetaGPT 用 MetaGPT → 否则降级 MOCK |
| `llm` | 真实 LLM 三阶段（planning / coding / checking） |
| `metagpt` | MetaGPT 单角色（依赖重，非 P0） |
| `mock` | MOCK 引擎（演示兜底、本地回归） |

模型配置与调试：
- 能力档：把每套 `LLM_*` 配置存成 `~/.atoms/llm/<档位>.env`，启动时 `LLM_PROFILE=<档位>` 加载；服务器上**一条命令完成启动+切档+校验**：`bash scripts/restart_and_verify.sh <档位>`（先打印该档位用的模型 → 同步代码 → 切档 → 健康检查 → 模型连通冒烟，任何一步失败立即停下；服务未安装时自动先执行 `deploy.sh` 首次安装）。`bash scripts/switch_model.sh use <档位>` 是底层切档工具（写 systemd drop-in、自动探活与回滚）。详见 [docs/setup.md](docs/setup.md) 5.1。
- 厂商专属参数：档位文件里 `LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}'` 可透传任意厂商参数（如关 DeepSeek 思考模式）；`LLM_REASONING_EFFORT=low` 是智谱的思考强度开关。
- 生成缓存：同一 prompt 重复生成命中 `workspace/.cache/`，不再请求模型；`LLM_CACHE=0` 关闭，`meta.json` 的 `cache_hits` 可看命中次数。

预置演示数据（避免现场等生成）：`python scripts/seed_demo.py --count 2`
单独验证引擎：`python scripts/gen_once.py "做一个番茄钟" --engine mock`

## 验收

```bash
FULL=1 bash scripts/verify_s8.sh                 # 全量回归（S1–S8）
bash scripts/verify_s3.sh                        # 单步验收
TARGET=public FULL=1 bash scripts/verify_s8.sh   # 公网（在外部机器执行）
```

每个 `verify_sN.sh` 都是 curl + grep，退出码 0 即通过，可重复执行；公网地址在 `deploy/verify.env`（模板见 `.example`）。
日常启动+校验合并成一条：`bash scripts/restart_and_verify.sh <档位> --full`（`--full` 追加最新 `verify_sN.sh` 全量验收）。

## 部署

```bash
cd ~/atoms-dev-demo
bash scripts/restart_and_verify.sh dev    # 拉代码 -> 切档 -> 健康检查 -> 模型冒烟，服务未装自动先安装
```

等价拆解：`bash scripts/deploy.sh`（同步依赖 → 覆盖 systemd unit → 重启 → 探活）+ `bash scripts/switch_model.sh use <档位>`。
固定形态：`0.0.0.0:8000` + `deploy/atoms.service`，不使用域名与 80/443；安全组需放行 `8000/TCP`。
线上检查：`curl -s localhost:8000/health`（应看到 `step=s8 engine=llm`）、`journalctl -u atoms -n 50 --no-pager`。

## 回滚

```bash
git checkout s7 && bash scripts/deploy.sh          # 回代码
bash scripts/switch_model.sh use <旧档位>          # 回模型档位（restart_and_verify 失败时也会提示此命令）
```

`main` 上的 tag `s1`…`s8` 每一步都是可独立部署的完整版本；数据库 schema 只增不改，回滚不需要退数据。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/delivery.md](docs/delivery.md) | 交付说明：取舍、完成度、验收结果、后续优先级 |
| [docs/design.md](docs/design.md) | 架构与接口契约（权威来源） |
| [docs/roadmap.md](docs/roadmap.md) | 六步计划、每步验收标准、进度与降级策略 |
| [docs/steps/](docs/steps/) | 每步落地记录：改了什么 / 怎么部署 / 怎么验收 / 怎么回滚 |
| [docs/setup.md](docs/setup.md) · [docs/setup-windows.md](docs/setup-windows.md) | Linux / Windows 环境搭建 |
| [docs/dev-debug.md](docs/dev-debug.md) | 开发与调试方式：分层验证、引擎排障、多轮与版本链自检 |
| [docs/agent/discipline.md](docs/agent/discipline.md) | 开发纪律（硬性约束 / 禁止事项 / 工作流，唯一出处） |
| [docs/agent/git-commit.md](docs/agent/git-commit.md) | 提交规范（type/scope、拆分要求） |
| [AGENTS.md](AGENTS.md) | 给 AI 协作者的入口：概览、必读索引与当前状态 |

## 目录结构

```
app/          FastAPI 入口、路由、编排、SQLite、安全头、可替换引擎层
web/          单页前端（原生 HTML/CSS/JS，无构建）
scripts/      deploy.sh、switch_model.sh、restart_and_verify.sh、verify_sN.sh、fake_llm.py、gen_once.py、seed_demo.py
deploy/       systemd unit 与验收地址配置模板
docs/         设计、路线图、环境指南、每步记录、交付说明
workspace/    生成产物，按 task_id 分目录    （运行时生成，不入库）
data/         SQLite 数据文件                （运行时生成，不入库）
logs/         运行日志                       （运行时生成，不入库）
```

## 技术栈

FastAPI + SQLite（WAL）+ LLM 三阶段生成（planning / coding / checking，MetaGPT 可选）+ 原生前端 + systemd。
不引入 ORM / Celery / Redis / 前端框架，队列与并发用 `asyncio` 原生能力，单进程单 worker。

## 已知限制

- 真实 LLM 生成需在 `~/.atoms/llm/<档位>.env` 配齐 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY`；无 Key 的环境可用 `python scripts/fake_llm.py` 验证引擎交互，默认 auto 缺配齐时降级 MOCK。
- 真实 MetaGPT 路线依赖重、时延不可控，已降级为非 P0，仅用 `scripts/fake_metagpt.py` 验证交互。
- 无鉴权、无 HTTPS、无限流：面向演示，不建议直接暴露敏感数据。
- 只支持单 worker，无法水平扩展；产物上限 50MB/任务。
