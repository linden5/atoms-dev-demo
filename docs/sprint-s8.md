# S8：8 小时冲刺计划（真实生成 + 发布闭环 + 版本叙事）

本轮不是"再加一个功能"，而是把 demo 从「MOCK 套壳 + 本地跑通」推到「真生成 + 公网可验收」。
时间预算：**8 小时**（写于 2026-11-19）。本文档是执行清单，落地后另写 `docs/steps/s8.md` 记录实际改动与回滚方式。

> **进度（2026-11-19 更新）**：T1 / T3 / T4 / T5 / T6 代码已完成，本地 `scripts/verify_s8.sh` 38/38 通过，
> s6/s7 回归无退化。**T0（Key）与 T2（公网部署）待执行**——见 [steps/s8.md](steps/s8.md) 第 3 节。

## 1. 一句话目标与完成定义

**目标**：评委打开公网链接，输入一句自然语言，看到一个**由 LLM 真实生成**的、可交互的应用；改一版；点 Publish 拿到一个别人能打开的链接。

**Definition of Done（全部满足才算本轮完成）**

| # | 判定项 | 验证方式 |
|---|---|---|
| 1 | 公网 `/health` 返回 `step=s8`、`engine=llm` | `curl http://<公网IP>:8000/health` |
| 2 | 三个不同需求生成三个**互不相同**且可交互的应用 | 浏览器手测：记账 / 作品集 / 计算器 |
| 3 | 发布后 `/p/{slug}` 在**无痕窗口**可打开并操作 | 手测 + `curl -sI /p/{slug}` 200 |
| 4 | 刷新页面、重启服务后会话与版本仍在 | `systemctl restart atoms` 后复查 |
| 5 | `bash scripts/verify_s8.sh` 全绿，s1–s7 回归不退化 | 见第 9 节 |
| 6 | 3 分钟演示脚本可一次走完 | 见第 8 节 |

## 2. 现状与差距（实测）

| 项 | 本机 `127.0.0.1:8000` | 公网 `129.204.28.104:8000` |
|---|---|---|
| `/health` | `step=s7 engine=mock python=3.14.4` | `step=s6 engine=mock python=3.11.18` |
| 多轮对话 | ✅ | ❌（旧版本） |
| 生成引擎 | MOCK（写死待办清单页） | MOCK |
| 真实生成 | 未验证（`requirements.txt` 里 metagpt 仍注释） | 未验证 |
| 发布/分享 | 无 | 无 |

结论：**最大风险是"AI 生成"是假的**；其次是公网版本落后一个步骤。

## 3. 评分项对照

| 要求 | 现状 | 本轮动作 |
|---|---|---|
| 真实交互（非静态） | ✅ 已有 | 不动 |
| 数据持久化 | ✅ SQLite + `workspace/{task_id}/` | 新表 `publishes` 沿用同一套 |
| 基本使用流程 | ✅ 需求→生成→预览→迭代 | 真实引擎接入后保持不变 |
| 至少一个延展能力 | ◐ 多轮对话 + 版本链 | 新增**发布/分享**闭环（T3） |
| 可测试的在线链接 | ◐ 有但跑 s6+mock | T2 部署 + T1 真引擎 |
| 实际可用、非 PoC | ❌ MOCK | T1 换成真实 LLM 生成 |

## 4. 排期

| 时段 | 编号 | 任务 | 完成信号 |
|---|---|---|---|
| 0:00–0:30 | T0 | 拿 LLM Key、确认公网 SSH 与部署路径 | 能调通一次 chat/completions |
| 0:30–2:30 | T1 | 真实 LLM 引擎（三阶段） | 本地生成 3 个不同应用 |
| 2:30–3:30 | T2 | 部署 s8 前置版本到公网 | 公网 `/health` 对 |
| 3:30–5:30 | T3 | 发布/分享闭环 | `/p/{slug}` 无痕可开 |
| 5:30–6:30 | T4 | 版本叙事（版本名 / History 双标签 / 消息级还原） | 演示可指着"版本 2"回退 |
| 6:30–7:15 | T5 | 生成中体验（步骤流 + 失败建议） | 60 秒等待不空转 |
| 7:15–8:00 | T6 | 收尾：验收脚本、步骤文档、演示数据、tag | 见第 9 节 |

超过 1.5 倍预估即触发第 7 节降级，不追加新功能。

## 5. 任务详述

### T0 准备（0:30）

- 确认一个 OpenAI 兼容端点与 Key（`base_url` / `model` / `key` 三件事）。
- 确认公网机器可 SSH、`/home/ubuntu/atoms-dev-demo` 可 `git pull`、systemd 可改 `Environment=`。
- **不把 Key 写进仓库**：只用 systemd 环境变量或 `~/.metagpt/config2.yaml`（沿用既有约束）。

### T1 真实 LLM 引擎（2h，P0）

**为什么不用 MetaGPT**：`metagpt==0.8.2` 需 3.11 + `aiohttp==3.8.6`/`faiss-cpu==1.7.4`，安装本身可能吃掉 1–2 小时，单次生成 5–10 分钟且失败率不可控。本轮改为**自己串三次 LLM 调用**，契约不变、时延可控（30–90s）、失败可重试。

**新增/改动文件**

| 文件 | 改动 |
|---|---|
| `app/engine/llm_engine.py` | 新增：实现 `Engine.run(task_id, prompt, workdir, emit)` |
| `app/engine/html_utils.py` | 新增：把 `metagpt_engine.extract_html()` 抽出来共用（围栏 / 裸 HTML / 截断三段兜底） |
| `app/config.py` | 新增 `ENGINE_MODE=llm\|mock\|metagpt\|auto`（auto 优先 llm → metagpt → mock）、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TIMEOUT` |
| `app/tasks.py` | `get_engine()` 增加 llm 分支；其余状态机不动 |
| `requirements.txt` | 引擎段注释更新：llm 引擎**零新增依赖** |

**流水线（三次调用，天然对应三阶段）**

```
emit("planning")  → LLM#1 出简报：页面版块 / 交互点 / 配色 / 移动端要点 → 写 meta.json
emit("coding")    → LLM#2 出单文件 index.html（内联 CSS+JS）
emit("checking")  → LLM#3 自检：方括号占位符、空 href、缺 viewport、点击无响应 → 命中则输出补丁 HTML
最终 scan_artifacts() 走原有逻辑挑入口
```

**提示词硬约束（复用 `metagpt_engine.PROMPT` 的取舍）**

- 单文件 `index.html`，内联 `<style>`/`<script>`，不引用任何外链 CDN/字体/图片；
- 必须真实可交互（至少 1 个有状态的交互，如增删/筛选/计算），不许"展示型落地页"敷衍；
- 必须 `<meta name="viewport">` + 响应式；
- 不输出解释文字，只输出 HTML。

**实现取舍**

- 只新增零依赖实现：`urllib.request` + `asyncio.to_thread`（项目刻意不引入 httpx 之外的新依赖）。
- HTML 校验：必须包含 `<html` 且正文 ≥ 800 字符，否则重试 1 次；两次都失败则任务 `failed`，错误信息给人话（"模型未返回可用的完整页面，请重试或换更具体的需求"）。
- `meta.json` 额外写 `{engine:"llm", model, tokens, duration_s, steps:[...]}` —— 供 T5 的"耗时/用量"展示。
- `MOCK_ENGINE=1` 仍然保留为**演示保底开关**，出问题 1 秒降级。

**验收**

```bash
MOCK_ENGINE= ENGINE_MODE=llm python scripts/gen_once.py "做一个记账小工具，能增删和合计" --out /tmp/g1
ls /tmp/g1 && grep -c "<script" /tmp/g1/index.html
# 连跑 3 条不同需求，产物 html 两两不同
```

### T2 部署（1h，P0）

1. 提交 + push → 服务器 `git pull && bash scripts/deploy.sh`（部署形态不变：`0.0.0.0:8000` + systemd 单 worker）。
2. `deploy/atoms.service` 增加：
   ```
   Environment=ENGINE_MODE=llm
   Environment=LLM_MODEL=<model>
   Environment=LLM_BASE_URL=<url>
   # Key 用 EnvironmentFile=/etc/atoms/llm.env，不进仓库
   ```
3. `STEP="s8"`，`/health` 回显 `engine`/`engine_mode`/`model`。
4. 公网跑一次完整链路（生成 → 预览 → 再改），确认不是本机假象。

**验收**：`curl http://<公网IP>:8000/health | grep -E 'step.*s8|engine.*llm'`

### T3 发布/分享闭环（2h，P1 —— 本轮"延展能力"）

对应文档 `12129577-publish-your-project`、`12129574-share-a-project` 的主流程，语义要点：**Preview ≠ 已上线**。

**数据（只增，不改既有表）**

```sql
CREATE TABLE IF NOT EXISTS publishes (
  slug       TEXT PRIMARY KEY,   -- 6–30 位，小写字母/数字/连字符
  task_id    TEXT NOT NULL,
  session_id TEXT,
  title      TEXT,
  created_at TEXT, updated_at TEXT
);
```

**接口**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks/{id}/publish` | body `{slug}`；400 非法 / 409 冲突 / 404 任务不存在；返回 `{slug,url}` |
| GET | `/api/publishes` | 已发布列表（首页"已发布"区） |
| DELETE | `/api/publishes/{slug}` | Unpublish |
| GET/HEAD | `/p/{slug}` | 返回该任务的入口 HTML，宽松 CSP |
| GET/HEAD | `/p/{slug}/files/{path}` | 公开产物静态文件，路径校验复用 `tasks.resolve_artifact()` |
| GET | `/api/tasks/{id}/zip` | Export 用，`zipfile` 打包产物 |

**前端**

- 顶栏 `Publish`（紧邻 `Share`）；发布面板：建议 URL + 铅笔改名 + 状态机文案 `Publish → Publishing → Your app is live! → View app`，再次发布为 `Update → Updating... → Up to date`。
- `Share` 面板：Public/Private 说明（Demo 中 Private 仅隐藏入口，不实现权限）、`Copy link`、`Export ZIP`。
- 预览区顶部常驻一行轻提示：**"预览是工作副本，访客看到的是已发布版本"**（文档反复强调的坑）。
- 有任务 `running` 时发布按钮置灰并说明原因。

**安全**：`/p/` 前缀加入 `app/security.py` 的 `CSP_EXEMPT_PREFIXES`；`X-Frame-Options` 仅对主站生效，公开页允许被嵌入。

**验收**

```bash
SLUG=demo-$RANDOM
curl -s -X POST localhost:8000/api/tasks/$TASK/publish -H 'Content-Type: application/json' -d "{\"slug\":\"$SLUG\"}" | grep -q "$SLUG"
curl -s -o /dev/null -w '%{http_code}' localhost:8000/p/$SLUG          # 200
curl -s -X POST localhost:8000/api/tasks/$TASK/publish -d '{"slug":"BadSlug"}'   # 400
```

### T4 版本叙事（1h，P2）

- **版本名**：`tasks` 增列 `version_title`（只增），生成成功时写入（llm 引擎取 LLM#3 总结首句，mock 用固定映射）；前端版本卡片显示"版本 2：绿色主题"。
- **History 双标签**：现 `history-panel` 拆 `Messages` / `Versions`。Messages = 时间 + 内容预览 + `Jump to message`（`scrollIntoView`，**不改项目状态**，文案要写明）+ 顶部 `Search messages`（前端过滤）。
- **消息级还原**：每条产物消息加 ↩，点击把当前预览切回该消息产生前的成功版本（复用现有父链，纯前端约 30 行）。

### T5 生成中体验（45min，P3）

- 阶段卡片标题改为「已处理 N 个步骤」，可展开列出工具动作（读取需求 / 生成简报 / 写入 index.html / 自检移动端）。
- 失败横幅分层建议：先刷新 → 再新窗口打开 → 再重试，并提示"只重试一次"；把引擎 error 映射成人话。
- **不做** SSE：轮询 + 阶段事件足够，`GET /api/tasks/{id}/events` 继续留在设计文档中。

### T6 收尾（45min）

- 新增 `scripts/verify_s8.sh`（见第 9 节），并纳入 `verify_s6.sh` 的 `FULL=1` 回归链。
- 写 `docs/steps/s8.md`（改了什么 / 怎么部署 / 怎么回滚 / 验收命令），更新 `docs/roadmap.md` 进度表。
- `scripts/seed_demo.py` 预置 1 个真实生成的演示会话，保证首页不空。
- 打 tag `s8`。

## 6. 本轮明确不做

| 不做 | 原因 |
|---|---|
| 元素点选编辑（Select element） | iframe 是 `sandbox="allow-scripts"` 且**无 `allow-same-origin`**，父页面读不到 iframe DOM，必须给产物注入 postMessage 桥，2.5–3h + 跨域坑 |
| Queue 面板 / Plan-Approve 卡片 / Team Mode 多角色 | 属多智能体编排，收益低于发布闭环 |
| 流式 token、SSE | 8 小时内风险大于收益 |
| 积分账单、套餐、域名/SSL、SEO/Ads、Atoms 云（Database/Jobs/Keys）、账户权限 | 商业化与云侧能力，与核心体验无关 |
| 视频/幻灯片输出、赛马模式、深度研究 | 文档中的旁支，Demo 不碰 |
| 引入 pytest / CI / 前端框架 / ORM | 违反 `AGENTS.md` 硬性约束 |

## 7. 风险与降级

| 风险 | 触发信号 | 降级动作 |
|---|---|---|
| 拿不到 Key / 额度不足 | T0 调不通 | 保持 `ENGINE_MODE=mock`，演示重心改为"过程可视化 + 版本链路"，**不在现场做首次生成** |
| LLM 产出不稳定（缺 `</html>`、外链 CDN） | T1 三次里两次失败 | 提高校验门槛 + 重试 1 次；再不行把 T3 提前（发布闭环不依赖真引擎） |
| 公网部署失败 | T2 超 1h | 本地 `uvicorn` 演示 + 公网旧版本兜底，材料里给两个链接 |
| T3 超时 | 4:30 仍未通 | 砍 Share 面板，只保留 Publish + `/p/{slug}` |
| 时间到 7:15 仍未收尾 | — | 冻结功能，只修阻断演示的 bug，写文档打 tag |

## 8. 演示脚本（3 分钟）

1. 打开公网链接 → 顶栏显示 `后端已连接 · S8`、引擎标签 `LLM`。
2. 输入"做一个咖啡店菜单页，能筛选冷热饮并合计价格" → 阶段卡片依次点亮（简报 / 编码 / 自检）→ 右侧出现**可点筛选项**的真实应用。
3. 点筛选项、加两项到合计 → 证明交互真实（不是截图）。
4. 继续对话："把主色改成绿色，并加一个营业时间区块" → 生成版本 2，版本卡片显示名字。
5. History → Versions 列表 → `Load preview` 看版本 1 → `Restore` 回退；Messages 标签演示 `Jump to message`。
6. Publish → 得到 `/p/xxx` → 无痕窗口打开 → 说明"预览 ≠ 已上线"。
7. 收尾一句：数据在 SQLite，重启不掉；`/api/docs` 可看全部接口。

## 9. 验收脚本 `scripts/verify_s8.sh`（计划项）

1. `/health` 含 `step:"s8"`、`engine:"llm"`、`engine_mode`
2. `POST /api/sessions` → 201，返回 `session_id`
3. 发具体需求 → 202 `action=generate`，拿到 `task_id`
4. 轮询到 `succeeded`（超时 180s），`artifacts` 含 `index.html`
5. 产物 HTML 含 `<script` 且长度 > 800（证明不是空壳）
6. 两次不同需求产物 html 内容不同（`md5` 不同）
7. `/api/tasks/{id}/preview` 200 + `Content-Type: text/html` + 宽松 CSP
8. 路径穿越 `../../main.py` 返回 404
9. 第二轮消息 `parent_id` 指向首版
10. `GET /versions`：`root_id`/`total>=2`/顺序升序
11. `POST /publish` 成功；重复 slug → 409；非法 slug → 400
12. `GET /p/{slug}` 200 且含生成页标记；`/p/{slug}/files/index.html` 200
13. `DELETE /api/publishes/{slug}` → 之后 `/p/{slug}` 404
14. `GET /api/tasks/{id}/zip` 200 且 `Content-Type: application/zip`
15. 主站安全头不退化（CSP/nosniff/XFO/no-store）
16. 预览 iframe 仍为 `sandbox="allow-scripts"`（不含 `allow-same-origin`）
17. 前端契约元素存在：publish 按钮、publish-panel、preview-note、history tab
18. 消歧需求 → `action=clarify` 且不建任务
19. 取消在跑任务 → `cancelled`
20. `FULL=1` 串跑 s1–s8 全绿

## 10. 与其它文档的关系

| 文档 | 关系 |
|---|---|
| [roadmap.md](roadmap.md) | S1–S7 为已交付步骤；S8 见本文档，落地后补 `docs/steps/s8.md` 并入进度表 |
| [design.md](design.md) | 接口契约与状态机的权威来源；T1/T3 新增接口落地后需回写 |
| [delivery.md](delivery.md) | 交付说明；本轮结束后更新"真实生成"与"公网可达"两项状态 |
| [agent/discipline.md](agent/discipline.md) | 硬性约束与禁止事项（纪律唯一出处）：单 worker、schema 只增不改、路径穿越校验、sandbox 不加 `allow-same-origin`、端口与部署形态不变 |
