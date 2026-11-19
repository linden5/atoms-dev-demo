# 交付说明（截至 2026-11-19，代码状态 = `main` @ `5364731`）

一个 Atoms 风格的 AI 应用生成工作台：输入自然语言需求 → 智能体生成可运行的单文件网页应用 → 右侧沙箱预览 → 可继续追问修改并回溯历史版本 → 一键发布成外人能打开的公开链接。任务、会话、消息、版本链与发布记录全部落在服务端 SQLite + 磁盘。

技术栈：FastAPI + SQLite（WAL）+ 自研三阶段 LLM 引擎（Python 3.11，零新增依赖）+ 原生前端（无构建工具、无框架）。MetaGPT 因 pip 全版本不可安装（证据见 [metagpt-docker.md](metagpt-docker.md)）降级为可选 Docker 旁挂，不是主路径。

## 1. 交付物清单

| 类别 | 内容 |
|---|---|
| 可运行代码 | `app/`（FastAPI + SQLite + 三引擎可替换）、`web/`（原生单页：对话落地页 + 工作台 + 历史三标签 + 发布/分享面板）、`scripts/`（部署/验收/假包工具）、`deploy/`（systemd unit + LLM 环境模板） |
| 验收脚本 | `scripts/verify_s1.sh` … `verify_s8.sh`，curl + grep，退出码判定，幂等 |
| 分步记录 | `docs/steps/s1.md` … `s8.md`：每步改了什么、怎么部署、怎么验收、怎么回滚 |
| 回滚单元 | git tag `s1`…`s8`，交付 tag `v1.0`、`v1.1` |
| 部署方式 | `git pull` → `bash scripts/deploy.sh`（或 `deploy_accept.sh` / `deploy_dev.sh` 多实例）→ 探活 `/health`；固定 `0.0.0.0:8000` + systemd，`restart_and_verify.sh` 一键启动校验 |

## 2. 八步与能力演进

| 步骤 | 能力 | 验收 |
|---|---|---|
| S1 | 纯静态工作台 + 演示数据 | 10/10；验证服务器、端口、安全组 |
| S2 | FastAPI 接管：`/health` + 静态托管 + systemd | 15/15 |
| S3 | 任务 CRUD + SQLite + MOCK 引擎 + 轮询 | 28/28；数据持久化、产物白名单 |
| S4 | 引擎可切换（auto/mock/metagpt）+ 超时/失败/取消 | 15/15 |
| S5 | 继续修改 + 版本历史（`parent_id` 链） | 22/22 |
| S6 | CSP/sandbox 加固、空态错误态、全量回归（tag `v1.0`） | 23/23 |
| S7 | 多轮对话：sessions/messages 表、规则澄清、会话驱动前端 | 18/18 |
| S8 | 真实 LLM 生成（三阶段）+ 发布闭环（`publishes`、`/p/{slug}`、ZIP 导出）+ 版本叙事 | 38/38（fake_llm） |

任一步都可以 `git checkout sN` 回到已知可用状态，数据库 schema 只增不改，回滚不用退数据。

## 3. 关键取舍

| 决策 | 取舍与理由 | 代价 |
|---|---|---|
| 引擎懒加载 | `app/` 顶层不 import metagpt，只在引擎文件内部导入 | 没装 MetaGPT 也能跑 S2/S3，最高风险项被隔离 |
| 引擎同契约可替换 | `Engine.run(task_id, prompt, workdir, emit)`；MOCK 与真实引擎同实现 | 前端/队列/产物扫描在 S3 就被真实验证，S8 只换实现类 |
| 自研三阶段 LLM 引擎 | metagpt 全系列 PyPI 装不上且单次 5–10 分钟失败率不可控；planning → coding → checking 三次调用零新增依赖 | 放弃 MetaGPT 的角色/工具生态 |
| 修正只做一次、失败保留原稿 | "宁可原稿能跑"；审查解析失败按无问题处理 | 产物质量有下限无上限 |
| 澄清用规则不用 LLM | 延迟费用行为可预测，只拦一次，判断不了就放行 | 准确率有限，`clarify()` 口子留给 LLM 化 |
| 轮询而非 SSE | 1.5s 轮询、终态停刷，实现成本最低、断网自愈 | 阶段内无流式增量反馈 |
| 单 worker + 进程内并发 1 | 2G 内存不 OOM，状态一致 | 无法水平扩展 |
| SQLite 单连接 + WAL | 零运维、标准库自带 | 并发写弱 |
| 版本链复用 `tasks.parent_id` 不建表 | 省一张表与一次迁移 | 分支只按时间线性展示 |
| 预览 iframe `sandbox="allow-scripts"`（无 `allow-same-origin`） | 无域名无备案 | 有 XSS 面，靠主站严格 CSP / 预览宽松 CSP 分流兜底 |
| 不引入构建工具/框架/ORM/Redis/Celery | 部署零额外依赖（纪律硬性约束） | 没有框架级开发体验 |
| 能力档 `LLM_PROFILE` + 磁盘缓存 | 一档一份 KEY=VALUE（strong=deepseek / dev=glm），密钥只在服务器；prompt 哈希落盘命中不发请求 | 多一层状态，需 `/health` 可观测 |
| `LLM_EXTRA_BODY` 透传厂商参数 | 思考开关各家字段不同，开放一个口；JSON 错误直接启动失败不留静默错误 | 用户需了解厂商参数 |
| 配置不做热加载 | 改环境变量 → 重启，行为不漂移 | 换模型必须重启 |

## 4. 完成度

| 需求（design 1.3） | 状态 | 说明 |
|---|---|---|
| 需求 → 生成 | ✅ | `ENGINE_MODE=llm` 三阶段真实生成；`mock` 一秒降级；metagpt 仅 Docker 旁挂 |
| 多轮对话 | ✅ | S7 起：会话/消息/追问/上下文组装，`planner.py` 规则澄清 |
| 生成结果可视化预览 | ✅ | iframe 沙箱，可切 375/768/1000/自适应 |
| 继续对话修改 | ✅ | 以父任务为基础生成新版本 |
| 版本历史与回溯 | ✅ | 版本链 v1…vN + 版本命名（`version_title`）+ 消息级还原 |
| 发布/分享闭环 | ✅ | `/p/{slug}` 公开访问、幂等 Update、409 冲突防护、Unpublish、ZIP 导出 |
| 任务状态可观测 | ✅ | 三阶段卡片 + 事件表 + 进度事件（重试/修正上报）+ 失败分层建议 |
| 产物可下载 | ✅ | `/api/tasks/{id}/zip` |
| 数据持久化 | ✅ | SQLite（WAL）+ `workspace/{task_id}/` |
| 任务 CRUD | ◐ | 创建/查询/取消齐全；无删除接口（真实产物按纪律不可删） |
| 公网可访问 | ✅ | 已部署公网（2026-11-19，IP:8000 + systemd，`ENGINE_MODE=llm`）；本仓库不含线上地址 |
| LLM Key 不下发 | ✅ | 只在服务端环境变量 / 档位文件（`~/.atoms/llm`，不进仓库）；`/health` 只回显模型名与档位名 |

**MetaGPT 真实生成：pip 路线已不可用（2026-11 实测）**。metagpt 0.3.0–0.8.2 全系列钉了已从 PyPI 移除的 `lancedb==0.1.16` / `==0.4.0`，依赖解析第一步即失败；放宽后还有 pydantic / websockets / typing-extensions 与 fastapi、uvicorn 的冲突。S8 起真实生成走 `ENGINE_MODE=llm`；若确实需要 MetaGPT 框架，只能 Docker 官方镜像旁挂成服务（[metagpt-docker.md](metagpt-docker.md)）。

## 5. 验收方式（可复现）

```bash
# 全量回归（S1–S8），本机
FULL=1 bash scripts/verify_s8.sh

# 无 LLM Key 的环境：假 OpenAI 兼容端点
python scripts/fake_llm.py          # success / retry / no_html / slow / error:401 等场景

# 公网形态（在外部机器执行）
cp deploy/verify.env.example deploy/verify.env   # 填 PUBLIC_BASE_URL
TARGET=public bash scripts/verify_s8.sh
```

最近结果（2026-11-19 本地，`v1.1` 已含 dev(glm)/strong(deepseek) 双档真实生成链路验证）：

| 脚本 | 结果 |
|---|---|
| `verify_s1..s6.sh` | 全部通过（10/15/28/15/22/23） |
| `verify_s7.sh` | 18 通过 / 0 失败 |
| `verify_s8.sh` | 38 通过 / 0 失败 / 1 跳过（DEEP 默认关） |

浏览器侧（playwright-cli + 本地 Chromium）已验证：提交 → 阶段推进 → 预览交互、取消、切任务、继续修改、版本切换与还原、历史三标签、Publish → `/p/` 访问 → Update 状态流转、刷新持久化、严格 CSP 下 0 console error。

## 6. 演示路径（3 分钟）

1. 打开首页（对话优先落地页）→ 输入需求 → 追问澄清或直接生成
2. 观察阶段卡片与进度事件依次点亮（真实引擎数分钟，MOCK 秒级）
3. 右侧预览里操作 → 证明产物可交互
4. 点「继续修改」→ 生成新版本 → 版本卡片显示 `version_title`，历史三标签可切「版本 / 消息 / 会话」
5. Restore 切回旧版本预览
6. Publish → 复制 `/p/{slug}` 链接 → 无痕窗口打开；改一版再 Update → 状态从 `Update` 回 `Up to date`
7. 分享面板导出 ZIP；打开 `/health` 与 `/api/docs` 展示运行状态

## 7. 后续优先级

| 优先级 | 事项 | 为什么 |
|---|---|---|
| P0 | 公网回归收尾：`TARGET=public bash scripts/verify_s8.sh` 全绿并留档 | 公网已部署（2026-11-19），部署态的回归结果尚无记录 |
| P0 | 流式输出（SSE，阶段内增量日志/代码片段） | `task_events` 基建已在，只动传输层；真实生成等待期最需要它 |
| P1 | MetaGPT Docker 旁挂打通（`ENGINE_MODE=remote`） | 兑现"智能体"叙事；先假包后真容器，契约不变 |
| P1 | LLM 澄清（`clarify()` 口子） | 规则澄清准确率有限 |
| P2 | 生成应用包含后端代码 | 动架构：多文件产物 + 不可信代码容器运行 + 预览反向代理，依赖 Docker 基建 |
| P2 | 版本树可视化 | 同一版本多次修改后线性拍平会歧义 |
| P2 | 任务删除与产物清理策略 | 磁盘随使用增长（真实产物按纪律不可删） |
| P3 | 鉴权与限流、多用户隔离 | 公网裸奔只适合演示 |

## 8. 关键文件索引

| 想知道什么 | 看哪 |
|---|---|
| 架构与契约（权威） | `docs/design.md` |
| 为什么分步、每步验收标准、进度表 | `docs/roadmap.md` |
| 协作纪律（硬性约束/禁止事项） | `docs/agent/discipline.md` |
| MetaGPT Docker 旁挂方案 | `docs/metagpt-docker.md` |
| 某一步具体改了什么、怎么回滚 | `docs/steps/sN.md` |
| 环境搭建（Linux / Windows） | `docs/setup.md` / `docs/setup-windows.md` |
| 接口清单 | `/api/docs` 或 `app/api.py` |
| 引擎实现（三阶段 LLM） | `app/engine/{llm_engine,prompts,html_utils}.py` |
| 状态机、引擎选择、进度事件 | `app/tasks.py` |
| 能力档与 LLM 配置 | `app/config.py`、`app/llm_cache.py` |
| 澄清规则与上下文组装 | `app/planner.py` |
| 安全响应头 | `app/security.py` |
| 提交规范 | `docs/agent/git-commit.md` |
