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
| L1 | 假 metagpt 注入 | `python scripts/gen_once.py "做一个计数器" --fake-metagpt success` | **引擎代码的真实路径** + 假模型返回 | 秒级，无需 3.11 / 无依赖 / 无额度 |
| L2 | MOCK 引擎全链路 | `MOCK_ENGINE=1 ...` 启动服务 | 队列、状态机、产物落盘、前端、版本链 | 秒级 |
| L3 | 真实引擎单次 | `python scripts/gen_once.py "做一个番茄钟" --engine metagpt --timeout 900` | MetaGPT 是否真能产出可预览的 HTML | 分钟级，**会产生调用费用** |
| L4 | 浏览器主链路 | `bash scripts/verify_s7.sh` + 人工点一遍 | 用户视角是否闭环 | 分钟级 |

**默认停在哪一层**：改前端停在 L2；改引擎/模板停在 L1，确认契约没问题再上 L3；只有怀疑"真实模型返回的形态"时才用 L3。

## 3. 日常命令（按你改了什么）

| 你改了什么 | 验证方式 |
|---|---|
| `web/` 前端 | L2 起服务，浏览器刷新；或 `bash scripts/verify_s7.sh` |
| `app/tasks.py` 状态机、产物扫描 | `python scripts/gen_once.py "测试" --engine mock` 先看产物，再看 `verify_s3.sh` |
| `app/engine/metagpt_engine.py` 模板或解析 | L1 四个 scenario 全跑一遍（见第 4 节） |
| `app/planner.py` 澄清规则/上下文拼接 | `python -c` 直接喂 `history` 列表，不启服务 |
| `app/db.py` schema | 删掉 `data/app.db` 重建，再用 `verify_s3.sh` 回归（schema 只增不改） |
| 提交前 | `bash scripts/verify_s6.sh`（或最新一步的脚本）+ 真实浏览器走主链路 |

服务启动（PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 4. 引擎调试：先用假包把契约跑通

`scripts/fake_metagpt.py` 会把 `sys.modules["metagpt"]` 替换成假实现，**引擎代码仍走真实路径**：`_build_role()`、`Role.run`、`LLM` 回退分支、`extract_html`、产物落盘全是真代码，只有"模型返回的那段文本"是假的。所以引擎交互的所有 bug 都能在无依赖机器上复现。

四个 scenario 各自验证什么：

| scenario | 模拟的现象 | 验证的代码路径 |
|---|---|---|
| `success` | 正常返回一段带 HTML 的文本 | 主路径、产物落盘、`scan_artifacts` |
| `no_html` | 模型只回了一句"信息不足"，没有 HTML | `extract_html` 返回 None → 抛 `ValueError` → 失败态与提示 |
| `role_broken` | Role 构造抛 `TypeError`（版本不兼容） | 自动回退到 `metagpt.llm.LLM()` 的兜底分支 |
| `slow:3` | 3 秒后才返回 | `TASK_TIMEOUT` 与任务取消 |

命令行验证（不开服务）：

```bash
python scripts/gen_once.py "做一个计数器" --fake-metagpt success
python scripts/gen_once.py "验证抽取失败" --fake-metagpt no_html
python scripts/gen_once.py "验证 LLM 回退" --fake-metagpt role_broken
python scripts/gen_once.py "验证超时" --fake-metagpt slow:3 --timeout 1
```

想在 Web 里看这些失败态（三阶段卡片、错误提示、取消按钮）：用 `serve_fake_metagpt.py`，注意先确认没有设 `MOCK_ENGINE=1`，否则会强制走 MOCK 引擎。

```bash
python scripts/serve_fake_metagpt.py
python scripts/serve_fake_metagpt.py --scenario no_html
python scripts/serve_fake_metagpt.py --scenario slow:6 --port 8001
```

确认契约没问题后，才上真实引擎：

```bash
python scripts/gen_once.py "做一个番茄钟，深色主题" --engine metagpt --timeout 900
```

真实生成失败时，按这个顺序看，别靠猜：

1. **产物目录里有什么**：`workspace/cli_*/` 下有 `meta.json` 说明模型确实回了内容，看 `raw_chars` 是不是 0 —— 0 表示调用就失败了（Key / `base_url` / 网络），不是解析问题。
2. **报错是 `ValueError` 还是别的**：`ValueError: 模型输出里没有可解析的 HTML` 表示拿到文本但没找到 `<!doctype>`/`<html>`，属于模板约束问题；其他异常属于调用或环境问题。
3. **`PROMPT` 是否被改动**：模板里的硬性约束（单文件、不外联、只输出 HTML）是解析成功的前提，调试时不要顺手放宽。
4. **是否退回 LLM 直调**：日志里出现「Role 路径不可用，退回 LLM 直调」说明 MetaGPT 版本 API 不兼容，此时走的是 `LLM().aask()`，行为差异要按这条分支排查。

## 5. 多轮与版本链调试（S7）

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

## 6. 排障对照表

| 现象 | 先查什么 |
|---|---|
| 顶栏显示 `引擎 MOCK`，但你以为在用真实引擎 | `MOCK_ENGINE` 是否被设成 `1`；`/health` 里的 `engine` 字段 |
| 提交后一直 `pending` | 是否多 worker 启动（必须单 worker）；`logs/app.log` 有无入队记录 |
| 任务 `succeeded` 但预览空白 | `workspace/{task_id}/` 里有没有入口 HTML；`scan_artifacts` 的入口判定 |
| `/files/{path}` 返回 404 | 路径是否真的在 `workspace/{task_id}/` 下（越界一律 404，不是 bug） |
| 预览里样式错乱、交互失效 | 生成产物是否引用了外部 CDN；CSP 是否拦截（看 console） |
| 改了代码没生效 | 是否忘了重启 uvicorn（本项目不做热重载） |
| Windows 上 `bash scripts/*.sh` 报错 | 用 Git Bash 跑脚本，或按 [setup-windows.md](setup-windows.md) 用等价 PowerShell 命令 |

## 7. 从调试到提交

1. 对应步骤的验收脚本通过：`BASE_URL=http://127.0.0.1:8000 bash scripts/verify_sN.sh`
2. 涉及交互的改动，用真实浏览器走一遍主链路（curl 200 不算通过）
3. `git status` 复核，确认没夹带 `workspace/`、`data/`、`logs/`、`__pycache__/` 与任何密钥
4. 提交信息与拆分规则见 [agent/git-commit.md](agent/git-commit.md)

调试用的临时产物（如 `workspace/cli_*`）一律不入库。
