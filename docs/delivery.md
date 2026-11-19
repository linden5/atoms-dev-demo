# 交付说明（v1.0）

一个 Atoms 风格的 AI 应用生成工作台：输入自然语言需求 → 智能体生成可运行的单文件网页应用 → 右侧沙箱预览 → 可继续修改并回溯历史版本。任务、产物与版本链全部落在服务端 SQLite + 磁盘。

## 1. 交付物清单

| 类别 | 内容 |
|---|---|
| 可运行代码 | `app/`（FastAPI + SQLite + 可替换引擎）、`web/`（原生单页）、`scripts/`（部署与验收）、`deploy/atoms.service` |
| 验收脚本 | `scripts/verify_s1.sh` … `verify_s6.sh`，curl + grep，退出码判定，幂等 |
| 分步记录 | `docs/steps/s1.md` … `s6.md`：每步改了什么、怎么部署、怎么验收、怎么回滚 |
| 回滚单元 | git tag `s1`…`s6`，最终交付 `v1.0` |
| 部署方式 | `git pull` → `bash scripts/deploy.sh` → 探活 `/health`；固定 `0.0.0.0:8000` + systemd |

## 2. 六步与能力演进

| 步骤 | 能力 | 验证了什么 |
|---|---|---|
| S1 | 纯静态工作台 + 演示数据 | 服务器可达、端口、安全组、静态服务 |
| S2 | FastAPI 接管：`/health` + 静态托管 | ASGI 服务、systemd 常驻、日志 |
| S3 | 任务 CRUD + SQLite + MOCK 引擎 + 轮询 | 数据持久化、后台任务、产物路径与白名单 |
| S4 | MetaGPT 引擎接入（auto/mock/metagpt）+ 超时/失败/取消 | 3.11 运行时、依赖安装、单次生成时延 |
| S5 | 继续修改 + 版本历史（`parent_id` 链） | 数据关系与前端状态管理 |
| S6 | CSP/sandbox 加固、空态错误态、全量回归 | 最终验收链路闭环 |

任一步都可以 `git checkout sN` 回到已知可用状态，数据库 schema 只增不改，回滚不用退数据。

## 3. 关键取舍

| 决策 | 取舍与理由 | 代价 |
|---|---|---|
| 引擎懒加载 | `app/` 顶层不 import metagpt，只在 `engine/metagpt_engine.py` 函数内导入 | 换来"没装 MetaGPT 也能跑 S2/S3"，把最高风险项推迟并隔离到 S4 |
| `MOCK_ENGINE` 三态 | `auto`（默认，能导入就用真实引擎）/ `1` 强制 mock / `0` 强制真实 | 同一份 unit 在有无依赖的机器上都可用；显式指定时行为确定 |
| MOCK 与真实引擎同契约 | `Engine.run(task_id, prompt, workdir, emit)` | 前端、队列、产物扫描在 S3 就被真实验证，S4 只换实现类 |
| 产物解析与引擎解耦 | `scan_artifacts()` 统一挑入口 HTML、限体积 | 引擎换结构只改扫描器；多文件产物仍能预览 |
| 单 worker + 并发 1 | 队列与信号量在进程内 | 2G 内存不会 OOM，但无法水平扩展 |
| SQLite 单连接 + WAL | 零运维、标准库自带 | 并发写弱；所有访问都在同一事件循环线程内才安全 |
| 版本链不建表 | 复用 `tasks.parent_id`，前端按树拍平 | 省一张表与一次迁移；分支只按时间线性展示 |
| 轮询而非 SSE | 1.5s 轮询，终态停刷 | 实现成本最低、断网自愈；实时性对分钟级生成无意义 |
| 预览用同源 iframe + sandbox | 无域名、无备案 | 有 XSS 面，靠 CSP + `sandbox`（无 `allow-same-origin`）兜底 |
| 主站严格 CSP / 预览宽松 CSP | 两套策略按路径分流 | 主站不能内联脚本；预览必须放宽才能渲染生成代码 |
| 不引入构建工具 | 原生 HTML/CSS/JS | 没有前端框架的开发体验，换来部署零额外依赖 |

## 4. 完成度

| 需求（design 1.3） | 状态 | 说明 |
|---|---|---|
| 需求 → 生成 | ✅ | 引擎四态可切换（llm / mock / metagpt / auto）；真实生成走 `ENGINE_MODE=llm`，零新增依赖。MetaGPT 因 pip 全版本不可安装，只能 Docker 旁挂（[metagpt-docker.md](metagpt-docker.md)） |
| 生成结果可视化预览 | ✅ | iframe 沙箱，可切 375/768/1000/自适应 |
| 继续对话修改 | ✅ | 「继续修改」以父任务为基础生成新版本 |
| 版本历史与回溯 | ✅ | 版本链 v1…vN，点击切回旧产物 |
| 任务状态可观测 | ✅ | 三阶段卡片 + 事件表 + 失败原因 |
| 产物可下载 | ◐ | `/api/tasks/{id}/files/{path}` 可直接取文件，未做打包下载 |
| 数据持久化 | ✅ | SQLite（WAL）+ `workspace/{task_id}/` |
| 任务 CRUD | ◐ | 创建/查询/取消齐全；未提供删除接口（产物保留优先） |
| 公网可访问 | ◐ | 形态是 IP:8000，需自行放行安全组；本仓库不含线上地址 |
| LLM Key 不下发 | ✅ | 只在服务端环境变量 / `EnvironmentFile=/etc/atoms/llm.env`；`/health` 只回显模型名，不回显 Key 与端点 |

**MetaGPT 真实生成：pip 路线已不可用（2026-11 实测）**。metagpt 0.3.0–0.8.2 全系列钉了已从 PyPI 移除的 `lancedb==0.1.16` / `==0.4.0`，依赖解析第一步即失败，与 Python 版本、平台无关；放宽后还有 pydantic / websockets / typing-extensions 与 fastapi、uvicorn 的冲突。S8 起真实生成改走零依赖的 `ENGINE_MODE=llm`；若确实需要 MetaGPT 框架，只能用 Docker 官方镜像旁挂成服务，方案与产物/调试说明见 [metagpt-docker.md](metagpt-docker.md)。

## 5. 验收方式（可复现）

```bash
# 全量回归（S1–S6），本机
FULL=1 bash scripts/verify_s6.sh

# 公网形态（在外部机器执行，别在服务器上测自己的公网 IP）
cp deploy/verify.env.example deploy/verify.env   # 填 PUBLIC_BASE_URL
TARGET=public FULL=1 bash scripts/verify_s6.sh
```

最近一次本机结果（2026-11-19，Windows + Python 3.14 + MOCK 引擎）：

| 脚本 | 结果 |
|---|---|
| `verify_s1.sh` | 10 通过 / 0 失败 |
| `verify_s2.sh` | 15 通过 / 0 失败 |
| `verify_s3.sh` | 28 通过 / 0 失败 |
| `verify_s4.sh` | 15 通过 / 0 失败 |
| `verify_s5.sh` | 22 通过 / 0 失败 |
| `verify_s6.sh` | 23 通过 / 0 失败（FULL 模式另含 5 项回归汇总） |

浏览器侧（playwright-cli + 本地 Chromium）已逐步骤验证：示例 prompt 填充、提交 → pending → 三阶段 → succeeded、iframe 内真实点击、取消、切任务、继续修改、版本切换、刷新持久化、900px 响应式、严格 CSP 下 0 console error。

## 6. 演示路径（3 分钟）

1. 打开地址 → 顶栏 `后端已连接 · S6`、引擎标签（`MOCK` 或 `METAGPT`）
2. 点「待办清单」→「生成应用」→ 观察阶段卡片 `0.5s / 1.5s / 0.4s` 依次点亮（真实引擎约 5–10 分钟）
3. 右侧预览里点 `+`、点「切换配色」→ 证明产物可交互
4. 点「继续修改」→ 输入「把配色改成绿色」→ 生成 v2，版本链出现 `v1 v2`
5. 点 `v1` → 右侧切回旧版本；点 `v2` → 回到新版本
6. 刷新页面 → 任务、版本、产物都还在
7. 打开 `/health` 与 `/api/docs` → 展示接口与运行状态

## 7. 后续优先级

| 优先级 | 事项 | 为什么 |
|---|---|---|
| P0 | 真实 MetaGPT 生成跑通并调提示词 | 目前唯一未验证的核心环节，决定"AI 生成"的可信度 |
| P0 | 失败可视化（把引擎错误映射成可操作建议） | 真实生成失败率高于 MOCK，需引导重试/降级 |
| P1 | 生成过程流式输出（阶段内增量日志/代码片段） | 5–10 分钟等待期内用户没有反馈 |
| P1 | 版本树可视化（分支不只是拍平） | 同一版本多次修改后线性展示会歧义 |
| P2 | 产物打包下载 / 单文件导出 | 演示之外的实用需求 |
| P2 | 任务删除与产物清理策略 | 磁盘会随演示次数增长 |
| P3 | 鉴权与限流 | 公网裸奔只适合演示 |
| P3 | 多用户隔离与产物子域托管 | 需要域名与备案，超出本次范围 |

## 8. 关键文件索引

| 想知道什么 | 看哪 |
|---|---|
| 架构与契约（权威） | `docs/design.md` |
| 为什么分六步、每步验收标准 | `docs/roadmap.md` |
| 某一步具体改了什么、怎么回滚 | `docs/steps/sN.md` |
| 环境搭建（Linux / Windows） | `docs/setup.md` / `docs/setup-windows.md` |
| 接口清单 | `/api/docs` 或 `app/api.py` |
| 引擎实现 | `app/engine/{base,mock_engine,metagpt_engine}.py` |
| 状态机与产物扫描 | `app/tasks.py` |
| 安全响应头 | `app/security.py` |
| 提交规范 | `docs/agent/git-commit.md` |
