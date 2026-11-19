# 架构设计文档

## 1. 背景与目标

### 1.1 问题定义

构建一个 Atoms Demo：用户用自然语言描述需求，系统由智能体驱动生成一个可运行的网页应用，并在浏览器中可视化预览；支持在已有结果上继续对话修改，所有任务、产物与版本持久化。

### 1.2 交付约束

| 约束 | 内容 | 对设计的影响 |
|---|---|---|
| 时间 | 48h 窗口内投入 6–8h | 必须砍掉一切非必要组件，优先跑通主链路 |
| 成本 | 极低预算 | 单台 2C2G 轻量服务器，按量付费 LLM |
| 交付物 | public 可访问链接 + public GitHub | 部署与仓库必须一次成功，不依赖域名 |
| 硬性要求 | 真实交互（非静态）、数据持久化 | 必须服务端落库，不能只靠浏览器 |
| 时间点 | 截止前需留出文档与兜底时间 | 架构要允许"预置演示数据" |

### 1.3 范围

**做**：需求 → 生成 → 预览 → 迭代修改 → 版本历史；任务状态可观测；产物可下载。

**不做**：用户注册与鉴权、计费、SEO、多租户隔离、生成应用的独立子域托管、Atoms 商业化能力。

## 2. 总体架构

```
                         ┌────────────────────────────┐
   Browser  ────────────►│  web/  单页前端（静态文件）  │
      ▲                  │  输入框 / 任务列表 / 预览区  │
      │ 轮询 task 状态    └────────────┬───────────────┘
      │                              │ HTTP (JSON)
      │                              ▼
      │                  ┌────────────────────────────┐
      └──────────────────│  FastAPI  app/              │
                         │  api.py   路由与校验         │
                         │  tasks.py 任务编排与状态机    │
                         │  db.py    SQLite 访问        │
                         └───────┬────────────┬────────┘
                                 │ 调度        │ 读写
                                 ▼             ▼
              ┌──────────────────────┐   ┌──────────────┐
              │ engine/  引擎层（可替换）│   │ SQLite       │
              │ metagpt_engine        │   │ tasks        │
              │ mock_engine           │   │ artifacts    │
              └──────────┬───────────┘   └──────────────┘
                         │ 生成并落盘
                         ▼
              ┌──────────────────────┐
              │ workspace/{task_id}/  │  生成产物（HTML/CSS/JS）
              └──────────────────────┘
```

分层职责：

| 层 | 职责 | 不做什么 |
|---|---|---|
| 前端 | 输入、状态展示、预览渲染、版本切换 | 不直接调用 LLM，不存储业务数据 |
| API 层 | 校验、任务入队、查询、产物下发 | 不包含业务推理逻辑 |
| 编排层 | 状态机、并发控制、超时、取消、事件记录 | 不感知具体引擎实现 |
| 引擎层 | 调用 LLM / MetaGPT，产出文件到 workspace | 不写数据库，不拼 HTTP 响应 |
| 存储层 | 任务、产物元数据、版本链 | 不存产物正文（正文留在磁盘） |

## 3. 核心流程

```
用户提交需求
   │
   ├─► POST /api/generate  ── 校验 → 建 task(status=pending) → 入队 → 202 {task_id}
   │                                                        (返回耗时 < 200ms)
   ├─► worker 取任务 → status=running, stage=planning → emit 事件
   │        │
   │        ├─ stage=coding   （调用引擎，产物写入 workspace/{task_id}/）
   │        └─ stage=checking
   │
   ├─► 引擎返回 → scan_artifacts(dir) → 写 artifacts 表 → status=succeeded
   │
   └─► 前端轮询 GET /api/tasks/{id}
            ├─ running  → 渲染阶段进度
            ├─ succeeded→ 加载 /api/tasks/{id}/preview 到 iframe
            └─ failed   → 展示 error，保留"重试"入口
```

失败与超时路径：

- 引擎抛异常 → `status=failed`，`error` 存摘要（截断 500 字），产物目录保留便于排查
- 超过 `TASK_TIMEOUT=600s` → 取消 asyncio 任务 → `status=failed, error=timeout`
- 用户主动取消 → `status=cancelled`，不回滚已生成文件
- 服务重启 → 启动时把残留 `running` 任务标记为 `failed, error=interrupted by restart`，避免永久 pending

## 4. 模块设计

### 4.1 API 层

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活与版本信息 |
| POST | `/api/generate` | 创建任务，body: `{prompt, parent_id?}`，返回 `{task_id}` |
| GET | `/api/tasks` | 任务列表，支持 `limit`、`offset` |
| GET | `/api/tasks/{id}` | 任务详情：状态、阶段、错误、产物列表、parent_id |
| POST | `/api/tasks/{id}/cancel` | 取消运行中的任务 |
| GET | `/api/tasks/{id}/preview` | 返回产物主 HTML（`text/html`，带 CSP） |
| GET | `/api/tasks/{id}/files/{path}` | 返回产物中的静态文件（路径白名单校验） |
| GET | `/api/tasks/{id}/events` | 事件流（SSE，可选实现；未实现时前端退回轮询） |
| POST | `/api/sessions` | 新建会话，body `{title?}`，返回 `{session_id, session}` |
| GET | `/api/sessions` | 会话列表，支持 `limit`、`offset`，带消息数与 `last_task_id` |
| GET | `/api/sessions/{id}` | 会话详情：消息流 + 消息引用到的任务快照（含 `entry`） |
| POST | `/api/sessions/{id}/messages` | 发一条消息，返回 `{action:"clarify", message}` 或 `{action:"generate", task_id, status, message}` |
| POST | `/api/tasks/{id}/publish` | 发布/更新公开链接，body `{slug?, title?}`；201 新建 / 200 更新 / 400 非法 slug / 409 被占用 |
| GET | `/api/tasks/{id}/publish` | 该版本所在应用当前绑定的公开地址（按会话查，可能为 `null`） |
| GET | `/api/publishes` | 已发布列表，支持 `limit` |
| DELETE | `/api/publishes/{slug}` | 取消发布（Unpublish） |
| GET | `/api/tasks/{id}/zip` | 导出产物 ZIP（`Content-Disposition: attachment`） |
| GET | `/api/tasks/{id}/data?token=` | 读取应用存储（S9）；无/错 token → 403，未写过 → `{}`；带 `ACAO:*` |
| PUT | `/api/tasks/{id}/data?token=` | 整包覆盖写应用存储（S9）；≤64KB 合法 JSON → 200，超限 413，非法 400 |
| OPTIONS | `/api/tasks/{id}/data` | CORS 预检（204）：PUT + JSON 非简单请求，沙箱内 fetch 会先问 |
| GET | `/p/{slug}` | **公开**入口页，带宽松 CSP 且不带 `X-Frame-Options`（允许被嵌入） |
| GET | `/p/{slug}/files/{path}` | 公开产物的静态资源 |

S7 的会话接口、S8 的发布接口都是新增，`/api/generate`、`/api/tasks/*` 全部保留且签名不变——旧前端与验收脚本仍可用。

响应示例：

```json
{
  "id": "t_20261118_ab12",
  "status": "running",
  "stage": "coding",
  "prompt": "做一个番茄钟，单文件 HTML",
  "parent_id": null,
  "error": null,
  "artifacts": [{"path": "index.html", "kind": "html", "size": 4213}],
  "created_at": "2026-11-18T10:00:00Z",
  "updated_at": "2026-11-18T10:03:12Z"
}
```

### 4.2 编排层

- **状态机**：`pending → running → succeeded | failed | cancelled`，终态不可回退。
- **并发**：单进程 `asyncio.Semaphore(MAX_CONCURRENCY)`，默认 1。理由：2G 内存下同时跑两次 MetaGPT 极易 OOM，且 LLM 费用翻倍。排队任务保持 `pending` 并可被取消。
- **超时**：`asyncio.wait_for(..., TASK_TIMEOUT)`，默认 600s。
- **事件**：阶段变化写 `task_events` 表（同时供 SSE 与排障使用），前端至少能拿到"最后一个 stage"。
- **重启恢复**：启动钩子里做一次 `running → failed(interrupted)` 的清理。

### 4.3 引擎层（关键抽象）

```python
class Engine(Protocol):
    name: str
    async def run(
        self,
        task_id: str,
        prompt: str,
        workdir: Path,
        emit: Callable[[str], Awaitable[None]],   # 上报阶段
    ) -> None: ...
```

契约约定：

1. 引擎**只负责把文件写进 `workdir`**，不改数据库、不返回 HTML 字符串；
2. 引擎必须可被取消（配合超时/取消按钮，内部用 `asyncio` 且不吞 `CancelledError`）；
3. 阶段上报是"尽力而为"，缺失不影响任务成功判定；
4. 产物结构由 `scan_artifacts()` 统一解释，引擎不承诺文件名。

三个实现（同契约，可切换）：

| 实现 | 用途 | 说明 |
|---|---|---|
| `llm_engine` | **默认真实生成**（S8） | 三次调用 OpenAI 兼容 `/chat/completions`：规划 → 编码 → 自检；零新增依赖（标准库 `urllib` + `asyncio.to_thread`） |
| `metagpt_engine` | 备选真实生成 | 调用 MetaGPT（单角色，非完整 Team），产物在 `workspace/{task_id}/` |
| `mock_engine` | 调试与演示兜底 | 写入固定 `index.html`，`asyncio.sleep` 模拟阶段；`ENGINE_MODE=mock` 或 `MOCK_ENGINE=1` 启用 |

引擎选择（`ENGINE_MODE` 四态，见 `app/config.py::_engine_mode`）：

```
llm      → 强制 LLM，未配置 LLM_* 直接抛错（不静默降级）
metagpt  → 强制 MetaGPT，装不上抛错
mock     → 强制 MOCK
auto/空  → LLM 三项配齐用 LLM；否则能导入 metagpt 用 MetaGPT；最后降级 MOCK
```

显式指定时不降级，是为了避免"以为在用真模型、其实在跑模板"。`auto` 下 `/health` 的 `engine` 字段可直接看出实际选择了哪个。

**能力档（`LLM_PROFILE`）**：一个档位 = 一份 `KEY=VALUE` 文件（`LLM_PROFILE_DIR/<档位>.env`，默认目录 `~/.atoms/llm`），里面放 `ENGINE_MODE / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_MAX_TOKENS` 等。启动时先读档位再把值注入环境，**已存在的环境变量优先**，便于临时覆盖单项。档位名只回显在 `/health` 的 `profile` 字段，密钥不出进程。

两个引擎共用同一份档位：LLM 引擎直接读注入后的 `LLM_*`；MetaGPT 引擎在初始化时把同样的值写成它认的 `~/.metagpt/config2.yaml`（未配置 `LLM_*` 时不动该文件，首次覆盖前留 `.atoms-bak`）。

**生成缓存（`workspace/.cache/`）**：按 `sha256(schema|namespace|identity|prompt)` 落盘模型返回的**原始文本**，命中即复用、不再发请求；`identity` 含引擎、档位、模型、端点与采样参数，换档位必然不命中。只缓存文本，不缓存产物——抽取、体检、自检与修正逻辑照常执行。`LLM_CACHE=0` 关闭，`LLM_CACHE_MAX_ENTRIES` 控制条数（默认 200，按最近使用淘汰）。缓存不改变任务与版本语义：新建任务仍会新建记录，只是不再等模型。

**迭代修复（revision 分支，S10）**：编排层（`app/tasks.py::start`）发现任务有 `parent_id` 且父版本入口 HTML 可读时，把父版本代码经 `Engine.run(parent_html=...)` 交给 LLM 引擎，引擎改走 `REVISION_PROMPT`（旧代码 + 用户本轮原话 → 改稿），**跳过 planning**，不再把"修复 XX"当全新需求盲重写。约束：父版本 HTML 超过 60KB 时只瘦身 `<script>` 之前的注释与空白（`_slim_parent_html`），逻辑代码绝不截断；父版本产物缺失或不可读时退回全新生成并记 warning。`Engine.run` 契约新增可选参数 `parent_html` / `requirement`（只对 LLM 引擎有意义，其余引擎忽略），向后兼容。

**自检与体检（S10 强化）**：`REVIEW_PROMPT` 从 6 项结构检查扩为 8 项，新增"沙箱约束（禁 localStorage/sessionStorage/cookie，`window.__ATOMS__` 存储桥除外）"与"功能正确性（对核心交互做代表性输入走查）"，输出新增 `verified` 字段（模型自报已验证的交互），落 `meta.json` 的 `verified_interactions`。本地体检三条硬问题（外链、占位符、存储 API——`html_utils.storage_hits`）**先于模型意见且不受模型 `ok=true` 影响**，命中即触发修正。`meta.json` 新增 `review_status`：`ok` / `parse_failed`（模型输出不是 JSON）/ `call_failed`（审查调用失败）——此前解析失败完全静默，排障时分不清"审查通过"还是"审查没跑"。两种失败下**本地硬体检照跑**（外链/占位符/存储是正则可查的硬伤，不依赖模型），发现即进修正循环；模型未解析的原始输出截断落 `meta.review_raw` 供排障。S10 验证期还修了占位符正则误报（`tokens[pos]` 这类合法下标取值不再被当成 `[pos]`）。

选择单角色而非 `Team` 全流程：全流程不可控且更慢，Web 场景需要的是"一次可复现的生成"，不是多角色编排本身。多角色观感通过前端阶段卡片呈现。

### 4.4 产物管理

- 目录：`workspace/{task_id}/`，任务创建时即创建，失败也保留。
- 扫描规则（`scan_artifacts`）：
  1. 递归收集文件，跳过隐藏目录、`__pycache__`、超过 5MB 的文件；
  2. 主入口优先级：`index.html` → 任意 `*.html`（取路径最短者）→ 无则任务判为 `failed, error=no previewable artifact`；
  3. 记录 `path / kind(html|css|js|image|other) / size`。
- 安全：
  - `/files/{path}` 必须 `Path.resolve()` 后校验前缀仍在任务目录内，阻断路径穿越；
  - 预览响应带 CSP：`default-src 'self' 'unsafe-inline' data: blob:`，禁止生成物外联任意脚本；
    **刻意不放行 `unsafe-eval`**（S10 验证发现：模型生成的计算器普遍用 `Function('...')` 动态
    求值，在沙箱里被 CSP 拦、异常被吞后表现为"所有输入都报无效"，用户怎么提示都修不好）。
    解法在生成端：CODE/REVIEW/REVISION/FIX 提示词禁 eval / new Function，本地硬体检
    （`html_utils.eval_hits`）发现即进修正循环，meta.json 落 `evals` 字段；
  - iframe 使用 `sandbox="allow-scripts allow-forms allow-modals allow-downloads
    allow-orientation-lock"`，**不加** `allow-same-origin`，隔离生成代码与主站。
    S10 修正：`allow-autoplay` 不是合法 sandbox 旗标（autoplay 归 Permissions Policy 管，
    不归沙箱），浏览器会忽略它并报 "invalid sandbox flag"，已移除。
    allow-forms 是 S9 实测补上的：生成的表单类应用（记账/待办）普遍靠 `<form>` 提交，
    无此权限时浏览器在 submit 事件**触发前**就拦截，点"添加"毫无反应；配套在预览 CSP 加
    `form-action 'self'`，防止 form POST 外传数据（fetch 外联已被 connect-src 拦住）。
    modals/downloads/autoplay/orientation-lock 为无害体验项；popups/pointer-lock/fullscreen/
    top-navigation/same-origin 一律不开，新增权限先过安全评审。
- 发布（S8）：`/p/{slug}` 复用同一套 `resolve_artifact()` 校验，但**不套主站 CSP/XFO**：
  CSP 换成预览那份宽松策略，`X-Frame-Options` 不下发（允许被其他站点嵌入），
  并加 `X-Robots-Tag: noindex` 避免公开页被搜索引擎收录。
- 应用存储引导注入（S9）：预览与公开页 HTML 在服务时于 `</head>` 前注入
  `<script>window.__ATOMS__={dataUrl, token}</script>`（见 4.9），产物文件本身不改动。

### 4.5 持久化

SQLite 单文件 `data/app.db`，`PRAGMA journal_mode=WAL`。所有写入走单一连接封装，避免并发写锁。

```sql
CREATE TABLE tasks (
  id          TEXT PRIMARY KEY,
  parent_id   TEXT REFERENCES tasks(id),
  prompt      TEXT NOT NULL,
  status      TEXT NOT NULL,          -- pending|running|succeeded|failed|cancelled
  stage       TEXT,                   -- planning|coding|checking
  error       TEXT,
  workdir     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX idx_tasks_status ON tasks(status, created_at DESC);

CREATE TABLE artifacts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  path       TEXT NOT NULL,
  kind       TEXT NOT NULL,
  size       INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_artifacts_task ON artifacts(task_id);

CREATE TABLE task_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  message    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- S7 多轮对话：只新增表，tasks / artifacts / task_events 一个字段都没动
CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,           -- 首条用户消息前 24 字，可后改
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL,           -- user|assistant
  kind        TEXT NOT NULL,           -- text|question|artifact|error
  content     TEXT NOT NULL,
  task_id     TEXT,                    -- kind=artifact 时指向生成任务
  created_at  TEXT NOT NULL
);

-- S8 发布：只新增表 + 一个 ALTER 列，不改既有列
CREATE TABLE publishes (
  slug       TEXT PRIMARY KEY,         -- 6–30 位小写字母/数字/连字符
  task_id    TEXT NOT NULL,            -- 指向当前"线上"的那个版本
  session_id TEXT,                     -- 同一个会话 = 同一个应用，Update 只改这里
  title      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- ALTER TABLE tasks ADD COLUMN version_title TEXT;   -- 版本名，前端版本卡片显示

-- S9 应用存储：只新增表。行按版本链根任务归档（root_of），同一应用迭代共享一份数据
CREATE TABLE task_data (
  task_id    TEXT PRIMARY KEY,          -- 版本链根任务 id
  token      TEXT NOT NULL,             -- 访问令牌（secrets.token_hex(16)，注入给生成应用）
  data       TEXT NOT NULL DEFAULT '{}',-- 应用状态 JSON 整包，≤64KB
  updated_at TEXT NOT NULL
);
```

版本链用 `parent_id` 表达：一次"继续修改"= 新建任务并指向父任务，不复制旧产物。回滚 = 前端把某个历史版本设为当前预览目标（不改数据），需要"设为当前"时在 `tasks` 之外不动，由前端记住 `current_task_id`。

S7 起版本链的父节点来源变了：不再由前端点"继续修改"，而是**会话里最近一次成功产物**（`db.last_generated_task`）。也就是说"在同一会话里继续说"就自动成为下一个版本，跨会话不会串版本。

S10 加了提示词指定版本的口子：用户消息里显式引用版本时，父节点改用被指的版本（从那里分叉）。**规则先行**（`planner.parse_version_ref`：`v2` / `第2版` / `版本2` / `上一版`，编号与 `/versions` 的 v1…vN 一致，零成本零延迟）；规则未命中但话里疑似有指代（`planner.looks_like_version_reference`）时才花**一次 LLM 调用**做语义解析（`llm_engine.resolve_version_by_llm`，输入版本摘要 title+需求摘要，输出约束为 `{"version": 编号|null}`，只许选列表里存在的编号，防幻构），解析失败/越界一律回退最新版，绝不阻断生成。同一版本被"基于历史版本修改"即形成分支，分支后 `last_generated_task` 顺延到新分支节点。已知边界：需求里恰好出现 vN 字样（如"支持 v1 v2 两个视图"）会被当成版本引用——概率低，暂不做意图消歧。

### 4.6 前端

单页静态文件，无构建步骤（避免引入 node 工具链）：

- 三块区域：左侧会话对话流（Agent 身份行 / 会话 chips / 「历史」弹出面板 / 消息流 / composer）、右侧预览 iframe；
- 会话标题：取首条用户消息前 24 字；若首条是「做个网页」这类模糊话（不足 12 字），首次生成时用那句需求覆盖，保证历史列表里能区分；
- 消息流按 `messages` 顺序渲染：`user` 右侧气泡，`question` Agent 追问块，`artifact` 步骤 + 版本卡片；
- 状态刷新：默认 1.5s 轮询当前任务，进入终态后停止；`stage` 变化时更新步骤点；
- 预览：`iframe.src = /api/tasks/{id}/preview`，点会话里任一版本卡片即切换 src；
- 失败态：展示 `error` 摘要 + 分层建议（先刷新/新窗口 → 再重试 → 再改需求），"重试"按钮**只把上一轮需求填回输入框**，不自动发送（自动重发会在会话里攒重复气泡）；
- 顶栏（S8）：`Publish` 打开发布面板（地址输入 / Publish→Up to date / View app / Unpublish），`Share` 打开分享面板（公开链接 / 导出 ZIP）；预览区常驻一行「预览是工作副本，访客看到的是已发布版本」，已发布时显示线上地址；
- 历史面板（S8）：`版本 / 消息 / 会话` 三标签——版本可 Restore 切回预览、消息可搜索并滚动定位（不改项目状态）、会话即原来的历史会话列表。

### 4.7 多轮对话（S7）

目标：像 Atoms 那样"说一句、Agent 接一句"，而不是一次性的表单提交。

**消息模型**：`sessions` + `messages` 两张新表，`messages.kind` 区分 `text / question / artifact / error`。生成结果不存 HTML，只存一条 `artifact` 消息并挂 `task_id`，产物仍在 workspace 与 `artifacts` 表里。

**澄清判断**（`app/planner.py`）：

1. 默认走规则，不额外调 LLM：延迟、费用、行为都可预测；
2. 触发追问的条件：本轮之前没追问过，且满足其一——累计用户输入为空、命中"做个网页"这类模糊表达、累计不足 12 字；
3. **只拦一次**：已经出现过 `question` 消息就不再拦，避免反复追问把用户挡在门外；
4. 判断不了就放行生成：最坏情况是"需求不够具体也生成了"，不会卡死。

**上下文组装**：`build_prompt(history, requirement)` 把历史压成"用户：… / 助手：…"的纯文本，产物卡片跳过，超长保留最近 1200 字符。这样 **Engine 协议完全不用改** —— 引擎拿到的仍是单个字符串。

**展示与输入分离**：`tasks.prompt` 存用户这一轮的原话（前端展示、任务详情、验收断言都用它），拼好的上下文只作为 `engine_prompt` 在内存里传给引擎、不落库。避免任务列表里出现一坨"对话记录"。

**追问升级的口子**：引擎若实现可选的 `clarify(history)`，planner 可在规则放行后再问一次模型（P1）。当前 MockEngine 与 MetaGPTEngine 都未实现，保证行为一致。

### 4.8 发布与分享（S8）

**语义**：Preview 是工作副本，`/p/{slug}` 才是访客能看到的线上版本。这是 Atoms 文档反复强调、也最容易做错的一点，所以前端把两者做成两条通道并在预览区常驻提示。

**数据**：`publishes` 一张表 + `tasks.version_title` 一列。一个会话（= 一个应用）对应一条 `publishes` 记录；`task_id` 指向当前线上版本。

**发布规则**：

| 场景 | 行为 |
|---|---|
| 首次发布 | 201，`slug` 为空则服务端生成 `app-xxxxxx`（中文标题不可靠音译，不硬转） |
| 同版本重复发布 | 200，`updated:false`（幂等，前端显示 Up to date） |
| 同会话新版本发布同一地址 | 200，`updated:true`，只改 `task_id`（Update 语义） |
| 别的应用占用该地址 | 409，不静默改用户地址 |
| 非法格式 / 任务未成功 | 400 / 409 |
| 取消发布 | 删除记录，`/p/{slug}` 立即 404 |

**实现要点**：

- slug 校验集中在 `models.SLUG_PATTERN`（6–30 位、首尾非连字符），接口层只做"归一化 + 校验"，不改写用户输入；
- 公开页与预览页共用 `resolve_artifact()` 做路径校验，不新写一份路径逻辑；
- `/p/` 前缀进 `CSP_EXEMPT_PREFIXES`，公开页下发宽松 CSP 且**不下发** `X-Frame-Options`（要允许被嵌入），另加 `X-Robots-Tag: noindex`；
- `GET /api/tasks/{id}/zip` 用标准库 `zipfile` 在内存里打包，超过 `MAX_ARTIFACT_BYTES` 返回 413。

### 4.9 应用存储（S9 存储桥）

**问题**：预览 iframe 是 `sandbox="allow-scripts"`（无 `allow-same-origin`）→ opaque origin，
`localStorage / sessionStorage / cookie` 访问直接抛 `SecurityError`。记账这类应用的"刷新后保留"
在预览里必然失效——且这是浏览器规范行为，不能靠加 `allow-same-origin` 解决（生成代码会拿到主站 origin，安全模型崩塌）。

**方案**：数据由平台代管。服务端在预览/公开页 HTML 注入 `window.__ATOMS__ = {dataUrl, token}`，
生成应用用 `fetch` 读写 `GET/PUT /api/tasks/{id}/data?token=…`：

- **令牌**：`task_data.token`（32 hex），建任务时生成（老任务懒创建），比较用 `secrets.compare_digest`；
- **归档**：存储行按版本链**根任务**归档（`db.root_of`），同一应用迭代出的新版本共享同一份数据，
  与发布的 Update 语义对齐——发布地址指向新版本后，访客数据不丢；
- **CORS**：opaque origin 下 fetch 是跨源请求（`Origin: null`），data 端点必须带 `ACAO:*`；
  `PUT + application/json` 触发预检，提供 OPTIONS 204 处理；CSP `connect-src 'self'` 不拦同 host fetch（实测）；
- **限额**：单份数据 ≤64KB（整包覆盖写），超限 413；令牌放 URL（沙箱不带 cookie），
  task_id 本身不可枚举，属"能力 URL"模型，演示场景够用；
- **一致行为**：预览、`/p/` 公开页、`/files/` 子页 HTML 三处入口统一注入，产物文件不做任何修改。

**落选方案**：`allow-same-origin`（安全不可接受）；postMessage 存储桥（localStorage 同步 API 与
异步消息错配，生成代码被迫用私有 shim，且预览/发布两套语义）；URL hash 存状态（容量小、父页面重建 iframe 即丢）。

## 5. 关键技术决策记录

| 决策 | 备选 | 结论 | 理由 | 代价 |
|---|---|---|---|---|
| 生成引擎（S8 改判） | MetaGPT / 自研三次 LLM 调用 | **自研三阶段 LLM 调用**（规划→编码→自检），MetaGPT 降为备选 | `metagpt==0.8.2` 需 3.11 + faiss/老 aiohttp，装依赖就可能 1–2 小时，单次生成 5–10 分钟且失败不可控；三次调用 30–90 秒、可重试、零新增依赖 | 自己维护提示词与 HTML 体检，生成质量取决于模型 |
| 部署形态 | 轻量服务器 / CloudBase / Render / Railway | 轻量服务器（Ubuntu 24.04，2C2G） | 磁盘持久、不休眠、可自由装依赖；免费 Serverless 有文件系统不持久或休眠问题 | 需自理 systemd 与安全组 |
| Python 运行时 | 系统 3.12 / 装 3.11 | uv 装独立 3.11 | MetaGPT 要求 `<3.12`，降级系统 Python 会破坏包管理 | 多一层工具 |
| 运行时与引擎解耦（S8） | 全站锁 3.11 / 引擎各自定版本 | LLM 引擎零额外依赖，3.11–3.14 都能跑；3.11 只留给 MetaGPT 备选 | 本机 3.14 可直接开发验证，服务器 3.11 仍保留 MetaGPT 可能 | 两条路径需要分别验收 |
| LLM 提供方 | 阿里云百炼免费额度 / 腾讯 TokenHub / DeepSeek 按量 | DeepSeek 按量付费 | 无需审批与地域限制、OpenAI 协议兼容、单价极低 | 需预充 10 元，无免费额度 |
| 数据库 | SQLite / Postgres | SQLite | 零运维、单进程足够、标准库自带 | 并发写弱，无法水平扩展 |
| 任务通知 | 轮询 / SSE / WebSocket | 轮询（SSE 可选） | 实现成本最低、断网自愈；生成本身分钟级，实时性收益低 | 状态可见性略差 |
| 多轮澄清 | 每轮先调 LLM 判断 / 规则判断 | 规则（`app/planner.py`） | 省一次调用与延迟，行为可预测；追问只拦一次，不会阻塞用户 | 极简需求可能被放过；LLM 澄清留作 P1 的 `clarify()` 口子 |
| 上下文注入 | 改 Engine 协议传消息数组 / 拼成单字符串 | 拼成单字符串（`engine_prompt`） | 引擎层零改动，MOCK 与 MetaGPT 行为一致 | 历史过长要截断，丢失早期细节 |
| 引擎降级 | 显式指定即报错 / 静默降级 | 显式指定报错，`auto` 才降级 | 避免"以为在用真模型、其实在跑模板"；同时保留一台没配 Key 的机器也能起服务的能力 | 部署时 agent 必须确认 `/health` 的 `engine` 字段 |
| 发布形态 | 复用主站同源 / 独立域名 | 同源 `/p/{slug}` + 宽松 CSP、不发 XFO | 免域名与证书，链接可直接发给别人验收；产物仍在同一台机器同一套校验里 | 公开页与主站同源，安全性依赖 CSP 与"产物只写静态文件" |
| 预览隔离 | 同源 iframe sandbox / 独立子域 | 同源 iframe + `sandbox` + CSP | 免域名、免备案、实现快 | 存在 XSS 面，靠 CSP 与 sandbox 兜底 |
| 公网访问 | 域名 + HTTPS / 公网 IP + 非标端口 | IP + `:8000` | 国内服务器绑域名需 ICP 备案，时间不允许；80/443 未备案会被拦 | 无 HTTPS，演示地址略粗糙 |
| 进程模型 | 单 worker / 多 worker | 单 worker（uvicorn 默认） | 任务队列与信号量在进程内，多 worker 会导致状态不一致 | 无法水平扩展 |
| 代码托管 | 服务器直连 GitHub / 本地推送 | 本地 `git push` | 避免服务器出网代理、Token 泄露等风险 | 需手动同步到服务器 |

## 6. 非功能性设计

**性能目标**

| 指标 | 目标 |
|---|---|
| `POST /api/generate` 响应 | < 200ms |
| mock 模式端到端（含预览） | < 5s |
| 真实生成 | 5–10 分钟，硬超时 600s |
| 并发任务数 | 1（`MAX_CONCURRENCY` 可调） |
| 单任务产物上限 | 50MB |

**成本控制**：单任务一次生成约数百到数千 token 级别调用，按量计费；演示前预生成 2–3 条结果写库，现场命中缓存，避免重复消耗与等待。

**可靠性**：
- 降级：`MOCK_ENGINE=1` 可在 LLM 不可用时保证链路可演示；
- 重试：任务级手动重试，不做自动重试（避免费用翻倍）；
- 幂等：同一 `prompt` 不做去重，前端"重试"显式创建新任务；重复生成命中的是**缓存**（见 4.3），不是任务去重。

**安全**：
- LLM Key 只存在于服务端：档位文件 `~/.atoms/llm/<档位>.env`（或 `/etc/atoms/llm/`）与 `~/.metagpt/config2.yaml`，权限 600，绝不下发前端，也不写进 systemd unit；
- `/health` 只回显档位名与缓存计数，不回显 Key、端点与档位文件路径；
- 产物目录访问做路径前缀校验；
- 预览 iframe 无 `allow-same-origin`，生成代码无法读取主站 Cookie；
- 不提供任何文件上传与任意路径读取能力。

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| MetaGPT 产物结构变化 | 找不到入口 HTML，预览空白 | `scan_artifacts` 多级回退 + 明确失败态，不让前端白屏 |
| 生成失败/超时 | 演示中断 | 预置成功任务 + `MOCK_ENGINE` 兜底 + 重试入口 |
| 2C2G 内存不足 | 进程被杀 | 并发 1、加 swap、限制产物大小 |
| 环境装不上（3.12 / faiss） | 无法启动 | 依次降级：faiss 单独重试 → 换 3.10 → 重装 Ubuntu 22.04 → 回退 `MOCK_ENGINE=1` 交付 |
| 端口未放行 | 打不开页面 | 安全组 + `ss -ltnp` 双重确认；验收前从外网无痕验证 |
| 生成代码外联/恶意脚本 | 演示环境被破坏 | CSP + iframe sandbox，限制外链 |

## 8. 演进路线（按优先级）

1. **P0 版本历史与回滚**：`parent_id` 链 + 前端版本列表（已在数据模型中预留）
2. **P1 生成时延**：流式输出阶段与代码片段，把"等待"变成"可见进度"；同批做 LLM 澄清（引擎实现 `clarify()` 后由 `planner` 接管）
3. **P2 产物质量**：生成后自动做一次结构校验（可运行性检查、缺失资源检测）
4. **P3 多 Agent 可视化**：把 MetaGPT 的角色分工映射为时间轴 UI
5. **P4 模板市场 / 子域隔离部署**：需要域名与备案，超出本次范围

## 9. 部署拓扑

```
Internet ──► 云厂商安全组(TCP:8000) ──► 轻量服务器 Ubuntu 24.04
                                        ├─ systemd: atoms.service
                                        ├─ uvicorn app.main:app (0.0.0.0:8000, 单 worker)
                                        ├─ .venv (uv, Python 3.11)
                                        ├─ data/app.db (SQLite, WAL)
                                        └─ workspace/{task_id}/ (产物)
```

环境搭建细节见 [setup.md](setup.md)（Linux）与 [setup-windows.md](setup-windows.md)（Windows）。
