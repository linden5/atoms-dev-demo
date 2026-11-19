# 开发与调试方式

本文只讲**怎么开发、怎么调试**：改完代码用什么命令验证、问题出在哪一层、失败后先看什么。
环境安装见 [setup.md](setup.md) / [setup-windows.md](setup-windows.md)，接口契约见 [design.md](design.md)。

## 1. 原则：分层验证，一次只改一层

链路是「HTTP → 编排 → 引擎 → 模型 → 产物扫描 → 前端」。任何一个问题，**先在能复现它的最低一层解决**，不要一上来就启动服务、开浏览器、跑真实生成。低层验证一条命令几秒完成，高层验证要开服务、等生成、还可能消耗额度。

同一时间只改一层。同时改了模板和状态机，失败时你不知道是哪边的问题。

## 2. 五层调试阶梯

| 层 | 手段 | 一条命令 | 能验证什么 | 代价 |
|---|---|---|---|---|
| L0 | 直接调函数 | `python -c "from app.engine.metagpt_engine import extract_html; print(extract_html(...))"` | 纯逻辑：解析、截断、路径校验、状态机分支 | 秒级，零依赖 |
| L1 | 假模型注入 | `python scripts/gen_once.py "做一个计数器" --fake-metagpt success`<br>`LLM_BASE_URL=http://127.0.0.1:8099/v1 ... --engine llm` | **引擎代码的真实路径** + 假模型返回 | 秒级，无需 3.11 / 无依赖 / 无额度 |
| L2 | MOCK 引擎全链路 | `ENGINE_MODE=mock ...` 启动服务 | 队列、状态机、产物落盘、前端、版本链 | 秒级 |
| L3 | 真实引擎单次 | `python scripts/gen_once.py "做一个番茄钟" --engine llm --timeout 900` | 真模型是否真能产出可预览的 HTML | 分钟级，**会产生调用费用** |
| L4 | 浏览器主链路 | `bash scripts/verify_s8.sh` + 人工点一遍 | 用户视角是否闭环 | 分钟级 |

**默认停在哪一层**：改前端停在 L2；改引擎/模板停在 L1，确认契约没问题再上 L3；只有怀疑"真实模型返回的形态"时才用 L3（有缓存时同一 prompt 第二次几乎零成本，见第 5 节）。

## 3. 日常命令（按你改了什么）

| 你改了什么 | 验证方式 |
|---|---|
| `web/` 前端 | L2 起服务，浏览器刷新；或 `bash scripts/verify_s8.sh` |
| `app/tasks.py` 状态机、产物扫描 | `python scripts/gen_once.py "测试" --engine mock` 先看产物，再看 `verify_s3.sh` |
| `app/engine/llm_engine.py` 或 `prompts.py` | 起假端点跑 L1（见第 4 节），先看 `meta.json` 的 `steps/issues/cache_hits` |
| `app/engine/metagpt_engine.py` 模板或解析 | L1 四个 scenario 全跑一遍（见第 4 节） |
| `app/config.py`、档位或缓存 | `bash scripts/verify_profile_cache.sh`（13 项断言，不需要服务与 Key） |
| `app/planner.py` 澄清规则/上下文拼接 | `python -c` 直接喂 `history` 列表，不启服务 |
| `app/db.py` schema | 删掉 `data/app.db` 重建，再用 `verify_s3.sh` 回归（schema 只增不改） |
| 每次部署/切档启动 | `bash scripts/restart_and_verify.sh <档位>`：先打印所用模型，再同步代码、停旧服务、启动、健康检查、模型冒烟，失败即停 |
| 提交前 | 对应步骤的 `verify_sN.sh` + 真实浏览器走主链路 |

服务启动（PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 4. 引擎调试：先用假模型把契约跑通

两个真实引擎都有对应的假实现，**引擎代码全部走真实路径**，只有"模型返回的那段文本"是假的。所以引擎交互的 bug 都能在无依赖、无 Key 的机器上复现。

### 4.1 LLM 引擎（S8 起的默认真实引擎）

`scripts/fake_llm.py` 起一个假的 OpenAI 兼容端点，按提示词内容区分「规划 / 编码 / 审查 / 修正」四类请求：

```bash
python scripts/fake_llm.py --port 8099 --scenario success &
LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_MODEL=fake LLM_API_KEY=dummy \
  python scripts/gen_once.py "做一个待办清单" --engine llm
```

| scenario | 模拟的现象 | 验证的代码路径 |
|---|---|---|
| `success` | 规划 + 合法 HTML，审查通过 | 主路径、`inspect_html`、产物落盘 |
| `retry` | 第一次编码是残页，第二次合法 | 编码阶段带原因重试（不合法才重试一次） |
| `issues` | 审查报问题，修正稿合法 | 自检 → 修正（`fixed=true`） |
| `no_html` | 两次都不含 HTML | 错误映射成人话、失败态提示 |
| `slow:N` | 每次响应前睡 N 秒 | `--timeout` 与 `TASK_TIMEOUT` |
| `error:500` / `error:401` | 服务端错误 / 鉴权失败 | 可重试与不可重试的分支（401 不重试） |

跑完看 `workspace/cli_*/meta.json`：`steps` 是每次调用的阶段与耗时，`issues`/`fixed` 说明自检是否生效，`cache_hits` 说明有没有走缓存。

### 4.2 MetaGPT 引擎（备选，需 Python 3.11）

`scripts/fake_metagpt.py` 会把 `sys.modules["metagpt"]` 替换成假实现：`_build_role()`、`Role.run`、`LLM` 回退分支、`extract_html`、产物落盘全是真代码。

| scenario | 模拟的现象 | 验证的代码路径 |
|---|---|---|
| `success` | 正常返回一段带 HTML 的文本 | 主路径、产物落盘、`scan_artifacts` |
| `no_html` | 模型只回了一句"信息不足"，没有 HTML | `extract_html` 返回 None → 抛 `ValueError` → 失败态与提示 |
| `role_broken` | Role 构造抛 `TypeError`（版本不兼容） | 自动回退到 `metagpt.llm.LLM()` 的兜底分支 |
| `slow:3` | 3 秒后才返回 | `TASK_TIMEOUT` 与任务取消 |

```bash
python scripts/gen_once.py "做一个计数器" --fake-metagpt success
python scripts/gen_once.py "验证抽取失败" --fake-metagpt no_html
python scripts/gen_once.py "验证 LLM 回退" --fake-metagpt role_broken
python scripts/gen_once.py "验证超时" --fake-metagpt slow:3 --timeout 1
```

想在 Web 里看失败态（三阶段卡片、错误提示、取消按钮）用 `serve_fake_metagpt.py`，注意先确认没有设 `ENGINE_MODE=mock`，否则会强制走 MOCK 引擎。

```bash
python scripts/serve_fake_metagpt.py --scenario no_html
python scripts/serve_fake_metagpt.py --scenario slow:6 --port 8001
```

### 4.3 上真实模型

```bash
python scripts/gen_once.py "做一个番茄钟，深色主题" --engine llm --timeout 900
python scripts/gen_once.py "做一个番茄钟，深色主题" --engine metagpt --timeout 900
```

真实生成失败时，按这个顺序看，别靠猜：

1. **`meta.json` 有没有、`raw_chars` 是不是 0**：有 `meta.json` 说明拿到了返回；`raw_chars=0` 说明调用就失败了（Key / `base_url` / 网络），不是解析问题。
2. **报错类型**：`ValueError` 或「模型没返回可用的完整页面」属于模板与体检问题；`LLM 鉴权失败(401)` / `接口不存在(404)` / `限流(429)` 属于配置与额度问题。
3. **是否命中缓存**：`cache_hits` 与 `steps[].cached` 能区分"真的调了模型"和"复用了上次输出"，别把缓存命中当成"模型变快了"。
4. **MetaGPT 是否退回 LLM 直调**：日志出现「Role 路径不可用，退回 LLM 直调」说明版本 API 不兼容，此时走的是 `LLM().aask()`。
5. **`PROMPT` / `prompts.py` 是否被改动**：模板里的硬性约束（单文件、不外联、只输出 HTML）是解析与体检成功的前提，调试时不要顺手放宽。

## 5. 缓存与能力档对调试的影响

这两层最容易造成"改了代码却没变化"的错觉，动手前先确认。

**缓存（`workspace/.cache/`）**

- 键是 `sha256(场景 | 身份 | prompt)`，身份含引擎、档位、模型、端点、`temperature`、`max_tokens`；改了任何一项都不命中
- 只缓存模型返回的**原始文本**，抽取、体检、自检、修正照常跑，所以行为差异只体现在"有没有真的发请求"
- 改了 `prompts.py` 却没清缓存 → 新 prompt 必然生成新键，**不会命中旧缓存**；反过来说，想验证同一 prompt 的新行为，也不用清
- 想复现"首次调用"的路径：`LLM_CACHE=0` 或删掉 `workspace/.cache/`
- `verify_profile_cache.sh` 会关掉假端点再跑第二次来证明"真的没发请求"

**能力档（`LLM_PROFILE`）**

- 档位只在进程启动时读取，**必须重启**；不重启会出现"以为切了其实没切"
- `/health` 的 `profile` 字段是唯一可信的确认方式，别只看环境变量
- 临时覆盖单项：直接设对应的 `LLM_*` 环境变量，它的优先级高于档位文件
- 档位切换后缓存自动失效（身份变了），不需要手工清

## 6. 多轮与版本链调试（S7）

会话接口按这个顺序调，缺一步会看不懂现象：

```bash
curl -s -X POST localhost:8000/api/sessions -H 'content-type: application/json' -d '{}'
curl -s -X POST localhost:8000/api/sessions/<sid>/messages -H 'content-type: application/json' \
  -d '{"text":"做一个待办清单"}'
curl -s localhost:8000/api/sessions/<sid>          # 消息流 + 引用到的任务快照
curl -s localhost:8000/api/tasks/<tid>/versions    # 版本链
```

三个容易踩的点：

- **澄清只拦一次**：`planner.decide()` 判断到已经追问过（历史里有 `kind=question` 的助手消息）就直接放行生成。调试"为什么没生成"时先看消息流里有没有那条追问，别去改规则。
- **历史是有上限的**：`HISTORY_MAX_CHARS = 1200`，超出只保留尾部并加省略提示。放宽它会让每轮输入变长，属于成本决策，不是调试手段；要验证长会话行为，造够字符数再跑 `verify_s7.sh`。
- **父节点来自数据库而非前端**：`db.last_generated_task(session_id)` 决定 `parent_id`，所以"跨会话串版本"是最常见的误判——先确认你是在同一个 `session_id` 里继续说的。

## 7. 排障对照表

| 现象 | 先查什么 |
|---|---|
| 顶栏显示 `引擎 MOCK`，但你以为在用真实引擎 | `ENGINE_MODE` 是否为 `mock`（或旧的 `MOCK_ENGINE=1`）；`/health` 里的 `engine` 字段 |
| 切换了档位但模型没变 | 是否重启、`/health` 的 `profile` 是否等于目标档位；`systemctl show atoms -p Environment` 看 drop-in 是否写入 |
| 同一需求第二次明显变快 | 大概率是缓存命中，看 `meta.json` 的 `cache_hits`；要验证真实调用用 `LLM_CACHE=0` |
| MOCK 下 `/health` 的 `cache.enabled=false` | 正常：MOCK 不调模型，缓存不参与，如实上报 |
| MOCK 启动日志有档位相关 WARN | 正常：MOCK 时档位问题只告警不阻断；切真实引擎前必须补齐 |
| 提交后一直 `pending` | 是否多 worker 启动（必须单 worker）；`logs/app.log` 有无入队记录 |
| 任务 `succeeded` 但预览空白 | `workspace/{task_id}/` 里有没有入口 HTML；`scan_artifacts` 的入口判定 |
| `/files/{path}` 返回 404 | 路径是否真的在 `workspace/{task_id}/` 下（越界一律 404，不是 bug） |
| 预览里样式错乱、交互失效 | 生成产物是否引用了外部 CDN；CSP 是否拦截（看 console） |
| 改了代码没生效 | 是否忘了重启 uvicorn（本项目不做热重载） |
| Windows 上 `bash scripts/*.sh` 报错 | 用 Git Bash 跑脚本，或按 [setup-windows.md](setup-windows.md) 用等价 PowerShell 命令 |

## 8. 从调试到提交

0. **真实生成的产物一律不删**。只要这次调用打到了真模型（不是 `--fake-metagpt` / `fake_llm.py` / MOCK），产物目录就是唯一证据：脚本触发看 `[产物] <绝对路径>`，服务端触发看日志 `产物目录=<绝对路径>`；要重跑就换新目录或新 prompt。完整规则见 [agent/discipline.md](agent/discipline.md)
1. 对应步骤的验收脚本通过：`BASE_URL=http://127.0.0.1:8000 bash scripts/verify_sN.sh`
2. 涉及交互的改动，用真实浏览器走一遍主链路（curl 200 不算通过）
3. `git status` 复核，确认没夹带 `workspace/`、`data/`、`logs/`、`__pycache__/` 与任何密钥
4. 提交信息与拆分规则见 [agent/git-commit.md](agent/git-commit.md)

调试用的临时产物（如 `workspace/cli_*`）一律不入库。
