# Atoms Demo

输入一句自然语言需求，由智能体生成一个可运行的网页应用，在右侧沙箱里实时预览；不满意就继续提需求，每次修改产生新版本，历史版本随时切回。

当前交付版本：**v1.0（S6 完成）**。任务、产物与版本链持久化在服务端（SQLite + 本地磁盘），刷新或重启进程都不丢。

## 能做什么

| 能力 | 现状 |
|---|---|
| 需求 → 生成 | ✅ 引擎可切换：MetaGPT（真实）/ MOCK（无额度兜底） |
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

引擎选择（`MOCK_ENGINE`）：

| 取值 | 行为 |
|---|---|
| 留空（默认） | auto：能导入 MetaGPT 用真实引擎，否则降级 MOCK |
| `1` | 强制 MOCK（演示兜底、本地回归） |
| `0` | 强制真实引擎，装不上直接报错 |

模型配置与调试：
- 能力档：把每套 `LLM_*` 配置存成 `~/.atoms/llm/<档位>.env`，启动时 `LLM_PROFILE=<档位>` 加载；服务器上用 `bash scripts/switch_model.sh use <档位>` 切换（写 systemd drop-in、自动探活与回滚）。详见 [docs/setup.md](docs/setup.md) 5.1。
- 生成缓存：同一 prompt 重复生成命中 `workspace/.cache/`，不再请求模型；`LLM_CACHE=0` 关闭，`meta.json` 的 `cache_hits` 可看命中次数。

预置演示数据（避免现场等生成）：`python scripts/seed_demo.py --count 2`
单独验证引擎：`python scripts/gen_once.py "做一个番茄钟" --engine mock`

## 验收

```bash
FULL=1 bash scripts/verify_s6.sh                 # 全量回归（S1–S6）
bash scripts/verify_s3.sh                        # 单步验收
TARGET=public FULL=1 bash scripts/verify_s6.sh   # 公网（在外部机器执行）
```

每个 `verify_sN.sh` 都是 curl + grep，退出码 0 即通过，可重复执行；公网地址在 `deploy/verify.env`（模板见 `.example`）。

## 部署

```bash
cd ~/atoms-dev-demo
git pull
bash scripts/deploy.sh      # 同步依赖 -> 覆盖 systemd unit -> 重启 -> 探活
```

固定形态：`0.0.0.0:8000` + `deploy/atoms.service`，不使用域名与 80/443；安全组需放行 `8000/TCP`。
线上检查：`curl -s localhost:8000/health`、`journalctl -u atoms -n 50 --no-pager`。

## 回滚

```bash
git checkout s5 && bash scripts/deploy.sh
```

`main` 上的 tag `s1`…`s6` 每一步都是可独立部署的完整版本；数据库 schema 只增不改，回滚不需要退数据。

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
scripts/      deploy.sh、verify_sN.sh、gen_once.py、seed_demo.py
deploy/       systemd unit 与验收地址配置模板
docs/         设计、路线图、环境指南、每步记录、交付说明
workspace/    生成产物，按 task_id 分目录    （运行时生成，不入库）
data/         SQLite 数据文件                （运行时生成，不入库）
logs/         运行日志                       （运行时生成，不入库）
```

## 技术栈

FastAPI + SQLite（WAL）+ MetaGPT（单角色，懒加载）+ 原生前端 + systemd。
不引入 ORM / Celery / Redis / 前端框架，队列与并发用 `asyncio` 原生能力，单进程单 worker。

## 已知限制

- 真实 MetaGPT 生成需 Python 3.11 + `metagpt` + LLM Key，本仓库默认 auto 会降级 MOCK；真实生成的时延与稳定性需在服务器上验证。
- 无鉴权、无 HTTPS、无限流：面向演示，不建议直接暴露敏感数据。
- 只支持单 worker，无法水平扩展；产物上限 50MB/任务。
