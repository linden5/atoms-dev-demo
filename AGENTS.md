# AGENTS.md

面向在此仓库中协作的 AI Agent 的工作约定（格式参考 [agents.md](https://agents.md/)，即给 agent 看的 README）。开始任何改动前先读这里，再读下面的必读文档。

## 项目是什么

Atoms Demo：输入自然语言需求，由智能体（MetaGPT）生成可运行的网页应用，右侧沙箱预览，可继续迭代，历史版本可回退。技术栈 FastAPI + SQLite + MetaGPT（Python 3.11）+ 原生前端。

## 必读文档

| 文档 | 什么时候读 |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | 每次开工前：当前处在哪一步、该步的验收标准与回滚方式 |
| [docs/design.md](docs/design.md) | 涉及接口契约、数据模型、状态机、安全策略时；实现与它冲突时以它为准 |
| **[docs/agent/git-commit.md](docs/agent/git-commit.md)** | **任何 git 提交之前必读**，提交信息必须符合该规范 |
| [docs/setup.md](docs/setup.md) / [docs/setup-windows.md](docs/setup-windows.md) | 需要装环境、跑服务、查端口时 |
| `docs/steps/sN.md` | 了解某一步具体改了什么、怎么部署与回滚 |

## 提交规范（摘要，完整版见 docs/agent/git-commit.md）

1. 格式固定为 `<type>(<scope>): <subject>`，例如 `feat(tasks): 支持任务取消与超时回收`
2. `type` 只用 feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert
3. `scope` 只用 web / api / tasks / engine / db / deploy / scripts / docs / s1…s6
4. 一个提交只做一件事，禁止把功能、格式化、重命名混在一起
5. **按类别拆分提交**：文档 / 前端 / 后端 / 测试 / 构建 / 配置必须分开提交，禁止 `git add -A` 一把梭；顺序为 配置 → 构建 → 后端 → 前端 → 测试 → 文档
6. 提交前必须：`git status` 复核 + `bash scripts/verify_sN.sh` 通过 + 不夹带密钥与运行时产物
7. 每个步骤收尾打 `sN` tag，最终交付打 `v1.0`

## 每一步的工作流

```
读 docs/roadmap.md 确认当前步骤与验收标准
  → 实现（只新增必要文件，尽量不改已验收文件的签名）
  → 补 scripts/verify_sN.sh 并跑通
  → 用真实浏览器走一遍主链路（curl 200 不算通过）
  → 写 docs/steps/sN.md（改了什么 / 怎么部署 / 怎么回滚 / 验收命令）
  → 更新 docs/roadmap.md 的进度跟踪表
  → 提交（信息符合规范）+ 打 tag sN
```

## 硬性约束

- **不引入构建工具、前端框架、ORM、Celery、Redis**。前端是原生 HTML/CSS/JS，状态机与队列用 `asyncio` 原生能力
- **单 worker**：任务队列与信号量在进程内，多 worker 会导致状态不一致
- **引擎懒加载**：`app/` 顶层不得 `import metagpt`，只在 `engine/metagpt_engine.py` 内部导入，保证没装 MetaGPT 的环境也能跑 S2/S3
- **产物路径固定** `workspace/{task_id}/`，`/files/{path}` 必须 `Path.resolve()` 后校验前缀，禁止路径穿越
- **预览隔离**：iframe 用 `sandbox="allow-scripts"`，不加 `allow-same-origin`
- **DB schema 只增不改**，不删列、不改列类型，保证任意 tag 都能启动
- **端口与部署形态不变**：始终 `0.0.0.0:8000` + systemd，不使用域名与 80/443
- **代码跨平台**：路径统一 `pathlib.Path`，不硬编码分隔符

## 禁止事项

- 不修改 git 配置，不 `--amend` / `--force` / `--no-verify`，未经确认不 `git push`
- 不提交任何密钥（`sk-`、`api_key`、`~/.metagpt/config2.yaml`、`.env`）
- 不提交 `workspace/`、`data/`、`logs/`、`__pycache__/`、`.venv/`
- 不为了让验收通过而削弱验收脚本；验收失败就修代码或如实记录限制
- 不静默扩大范围：计划外的改动先提出，等确认

## 当前状态

| 步骤 | 内容 | 状态 |
|---|---|---|
| S1 | 静态单页 + 演示数据 | 已完成，tag `s1` |
| S2 | FastAPI：`/health` + 静态托管 | 已完成，tag `s2` |
| S3 | 任务 CRUD + SQLite + MOCK 引擎 | 已完成，tag `s3` |
| S4 | 接入 MetaGPT 引擎（与 MOCK 可切换） | 已完成，tag `s4`；真实生成待装 3.11 + metagpt 验证 |
| S5 | 继续修改 + 版本历史（parent 链） | 已完成，tag `s5` |
| S6 | 加固、交付文档与全量回归 | 已完成，tag `s6` / `v1.0` |

后续工作看 `docs/delivery.md` 第 7 节（P0：真实 MetaGPT 生成跑通与失败可视化）。

详见 [docs/roadmap.md](docs/roadmap.md)。
