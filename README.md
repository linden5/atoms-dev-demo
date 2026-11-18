# Atoms Demo

一个可运行的 Atoms Demo：用户输入一段自然语言需求，由智能体（MetaGPT）驱动生成一个可运行的网页应用，并在浏览器中以可视化方式预览。任务状态、生成产物与历史版本持久化在服务端 SQLite。

## 快速开始

当前是 **S2（FastAPI 接管）**：`/health` 可用，`web/` 由 FastAPI 挂载，页面仍使用内置演示数据。

```bash
# 建环境并装基础依赖（S2/S3 只需要这一段；S4 的 metagpt 在 requirements.txt 里注释着）
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 启动（单 worker，不要加 --workers >1）
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
# 打开 http://127.0.0.1:8000，/health 可直接看到 JSON
```

验收：

```bash
bash scripts/verify_s2.sh                 # 15 项
bash scripts/verify_s1.sh                 # S1 的 11 项仍然要过
TARGET=public bash scripts/verify_s2.sh   # 公网（先在 deploy/verify.env 配 PUBLIC_BASE_URL）
```

部署到服务器：

```bash
bash scripts/deploy.sh      # 拉代码 -> 同步依赖 -> 更新 systemd unit -> 重启 -> 探活
```

S4 起才需要 MetaGPT（Python 3.11 + uv），环境准备见 [docs/setup.md](docs/setup.md)（Linux）与 [docs/setup-windows.md](docs/setup-windows.md)。服务器需放行 8000/TCP。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | 实施路线图：六步增量交付、每步交付物与验收标准、部署与回滚方式、进度跟踪 |
| [docs/design.md](docs/design.md) | 架构设计文档：分层架构、核心流程、模块设计、数据模型、接口契约、技术决策记录、非功能目标、风险与演进 |
| [docs/setup.md](docs/setup.md) | Linux 环境搭建与运行指南：uv / Python 3.11 / 依赖 / LLM 配置 / 启动 / systemd / 故障排查 |
| [docs/setup-windows.md](docs/setup-windows.md) | Windows 环境搭建与运行指南：uv / PowerShell 激活 / 局域网访问 / 同步到服务器 / 双平台差异 |
| [docs/steps/](docs/steps/) | 每个步骤的落地记录：改了什么 / 怎么部署 / 怎么回滚 / 验收命令 |

## 当前进度

| 步骤 | 内容 | 状态 |
|---|---|---|
| S1 | 静态单页 + 演示数据 | 完成，`tag s1` |
| S2 | FastAPI：`/health` + 静态托管 | 完成，`tag s2` |
| S3 | 任务 CRUD + SQLite + MOCK 引擎 | 未开始 |
| S4 | 接入真实 MetaGPT 引擎 | 未开始 |
| S5 | 继续修改 + 版本历史 | 未开始 |
| S6 | 安全加固与交付 | 未开始 |

详见 [docs/roadmap.md](docs/roadmap.md)。

## 技术栈

FastAPI + SQLite + MetaGPT（Python 3.11）+ 单页静态前端（iframe 沙箱预览）。

## 目录结构

```
web/          单页前端（S1 起）
deploy/       systemd unit（S1 起）
scripts/      deploy.sh、verify_sN.sh（S1 起）；gen_once.py、seed_demo.py（S4 起）
app/          FastAPI 接口层、SQLite 访问、可替换的引擎层（S2/S3/S4 逐步加入）
workspace/    生成产物，按 task_id 分目录（S3 起，不入版本控制）
data/         SQLite 数据文件（S3 起，不入版本控制）
logs/         运行日志（S2 起，不入版本控制）
docs/         设计、路线图、环境指南、每步记录
```

## 工程约定

- 产物路径固定 `workspace/{task_id}/`，接口只读该目录下的白名单文件
- `MOCK_ENGINE=1` 时引擎层返回固定产物，用于不消耗 LLM 额度地调试接口层
- `workspace/`、`data/`、`.venv/` 不纳入版本控制
