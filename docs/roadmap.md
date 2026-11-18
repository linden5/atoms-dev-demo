# 实施路线图（六步增量交付）

本文档是把 `docs/design.md` 落地为可交付产品的执行计划。核心原则：**每一步都是一个独立可部署、可交互的完整版本**，不做半成品，并用最早的一步验证部署环境本身是否通。

## 1. 为什么要分步

| 问题 | 分步如何解决 |
|---|---|
| 最后才发现服务器/端口/安全组不通 | S1 只有一个静态页就先部署上去，15 分钟内验证公网可达 |
| 功能做一半无法演示 | 每一步结束都要求页面能打开、能点、能刷新不丢数据 |
| MetaGPT 装不上导致整体报废 | 引擎懒加载，S4 才引入；S3 的 MOCK 版本本身可交付 |
| 出问题无法定位是哪一步引入的 | 每步一个 git tag，`git checkout sN` 立刻回到已知可用状态 |
| 时间超支无感知 | 每步约 1h，超过即触发降级策略（见第 6 节） |

## 2. 通用约定

- **同一服务**：始终是 `0.0.0.0:8000` 上的同一个 systemd 服务（`atoms.service`），部署方式从头到尾不变。
- **部署流程**：`git pull` → `./scripts/deploy.sh` → `sudo systemctl restart atoms`。
- **回滚流程**：`git checkout sN`（或反向 cherry-pick）→ 重启服务。S3 起数据库 schema 只增不改，回滚不需要回退数据。
- **每步必须产出四件套**：
  1. 可运行代码（该步的完整版本，不是 diff）
  2. `scripts/verify_sN.sh`：curl + grep 判定，退出码 0 即通过，可重复执行
  3. `docs/steps/sN.md`：改了什么 / 怎么部署 / 怎么回滚 / 验收命令
  4. 演示数据（示例 prompt 或预置任务），保证打开页面不是空白
- **验收脚本幂等**：不依赖上一次执行的结果，允许在任意状态下重跑。

## 3. 总览

| 步骤 | tag | 交付能力 | 这一步验证什么 | 预计 |
|---|---|---|---|---|
| S1 | `s1` | 纯静态单页 + 内嵌演示数据，点击切换示例任务与预览 | 服务器可达、端口、安全组、静态服务 | 1h |
| S2 | `s2` | FastAPI 接管：`/health` + `web/` 静态托管 | ASGI 服务、systemd 常驻、日志 | 0.5h |
| S3 | `s3` | 任务 CRUD + SQLite + MOCK 引擎 + 前端接真实接口与轮询 | 数据持久化、后台任务、产物路径与白名单 | 1.5h |
| S4 | `s4` | 接入真实 MetaGPT 引擎（与 MOCK 可切换）+ 超时/失败/取消 | Python 3.11 运行时、依赖安装、单次生成时延 | 1.5h |
| S5 | `s5` | 继续修改 + 版本历史（parent 链、历史版本预览切换） | 数据关系与前端状态管理 | 1h |
| S6 | `s6` / `v1.0` | CSP 与 sandbox 加固、空态错误态、交付文档、全量回归 | 最终验收链路是否闭环 | 1h |

## 4. 各步详情

### S1 静态单页与演示数据

**交付物**：`web/index.html`、`web/styles.css`、`web/app.js`（内置 2–3 条演示任务与假产物）、`deploy/atoms.service`（此阶段跑静态服务）、`scripts/deploy.sh`、`scripts/verify_s1.sh`、`docs/steps/s1.md`。

**可交互标准**：点击示例 prompt 会填入输入框；点击演示任务会切换右侧预览；切换设备宽度有响应。

**验收**：

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/    # 200
curl -s http://127.0.0.1:8000/ | grep -q 'Atoms'                  # 页面含项目标识
curl -s http://<公网IP>:8000/ -o /dev/null -w '%{http_code}'      # 外网 200（无痕窗口再确认一次）
```

**回滚**：`git checkout s1`，此阶段无数据依赖。

### S2 FastAPI 接管

**交付物**：`app/main.py`（挂载 `web/`、注册 `/health`、启动钩子）、`app/api.py`、`requirements.txt`（基础段：fastapi/uvicorn）、`deploy/atoms.service`（改 uvicorn）、`scripts/verify_s2.sh`、`docs/steps/s2.md`。

**关键约束**：`requirements.txt` 分两段注释（基础依赖 / metagpt），S2–S3 只装基础段即可运行，metagpt 留到 S4。

**验收**：

```bash
curl -s http://127.0.0.1:8000/health | grep -q '"status":"ok"'
curl -s http://127.0.0.1:8000/ | grep -q '<title>'
systemctl is-active atoms                              # active
journalctl -u atoms -n 20 --no-pager | grep -q 'Uvicorn running'
```

**回滚**：`git checkout s1` 并改回静态服务的 unit 文件。

### S3 任务 CRUD + SQLite + MOCK 引擎

**交付物**：`app/config.py`、`app/db.py`、`app/models.py`、`app/tasks.py`、`app/engine/base.py`、`app/engine/mock_engine.py`、`app/api.py` 扩展路由、前端改为 fetch + 1.5s 轮询、`scripts/verify_s3.sh`、`docs/steps/s3.md`。

**可交互标准**：输入需求 → 看到 pending→running→succeeded 阶段推进 → 右侧渲染 MOCK 产物 → 刷新页面任务仍在。

**验收**：

```bash
TASK=$(curl -s -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' -d '{"prompt":"做一个番茄钟"}' | grep -o '"id":"[^"]*' | cut -d'"' -f4)
curl -s http://127.0.0.1:8000/api/tasks/$TASK | grep -q '"status":"succeeded"'
curl -s http://127.0.0.1:8000/api/tasks/$TASK/preview | grep -qi '<html'
sudo systemctl restart atoms
curl -s http://127.0.0.1:8000/api/tasks/$TASK | grep -q '"status":"succeeded"'   # 重启后仍在 = 持久化生效
```

**回滚**：`git checkout s2`；数据库文件保留但旧版本代码不读，无副作用。

### S4 接入真实 MetaGPT 引擎

**交付物**：`app/engine/metagpt_engine.py`（内部懒加载 `import metagpt`，单角色，非 Team 全流程）、引擎选择逻辑（`MOCK_ENGINE`）、超时与取消落地、`scripts/gen_once.py`、`scripts/seed_demo.py`（预置 1–2 条成功任务）、`requirements.txt` metagpt 段、`scripts/verify_s4.sh`、`docs/steps/s4.md`。

**风险控制**：按代价从低到高逐级降级，不引入本项目架构之外的部署形态（本项目不使用容器，部署路径始终是 venv + systemd + 8000 端口）：

1. `uv pip install --no-cache "faiss-cpu==1.7.4"` 单独重试；仍失败则 `uv python install 3.10` 换小版本（几分钟）
2. 重装系统镜像为 Ubuntu 22.04（自带 3.10），按 `setup.md` 重来（10–15 分钟；S4 之前数据均为 mock，可丢弃）
3. 只在本地 WSL 验证引擎能否产出，服务器保持 `MOCK_ENGINE=1`
4. 保持 `MOCK_ENGINE=1` 交付，MetaGPT 在交付文档中标注"已设计未启用"

第 4 级落地时，本步失败不影响 S3 产物可用性，也不阻塞 S5/S6。

**验收**：

```bash
ps aux | grep -c "[u]vicorn"                                  # 单 worker
python scripts/gen_once.py "做一个倒计时"                      # 产物落在 workspace/<task_id>/
MOCK_ENGINE=0 TASK_TIMEOUT=120 uvicorn ...                    # 真实生成一次，状态推进到 succeeded
curl -s -X POST .../api/tasks/$TASK/cancel | grep -q 'cancelled'
curl -s .../api/tasks/$TASK/files/../../main.py               # 必须 404（路径穿越防护）
```

**回滚**：设 `MOCK_ENGINE=1` 重启即可，无需回退代码。

### S5 继续修改与版本历史

**交付物**：`parent_id` 链写入逻辑、"继续修改"入口、版本列表与历史预览切换、`scripts/verify_s5.sh`、`docs/steps/s5.md`。

**可交互标准**：对已有结果点"继续修改" → 生成新版本 → 列表出现两条 → 点击旧版本右侧切回旧产物。

**验收**：

```bash
A=$(... 建任务)
B=$(curl -s -X POST .../api/generate -d "{\"prompt\":\"把颜色改成绿色\",\"parent_id\":\"$A\"}")
curl -s .../api/tasks/$B | grep -q "\"parent_id\":\"$A\""
curl -s .../api/tasks/$A/preview | grep -qi '<html'   # 旧版本仍可独立预览
```

**回滚**：`git checkout s4`（数据库多一列 `parent_id`，旧代码忽略该列）。

### S6 加固与交付

**交付物**：CSP 响应头、iframe `sandbox="allow-scripts"`、空态/错误态/加载态、`docs/delivery.md`（实现思路与关键取舍、完成程度、后续扩展与优先级）、README 进度更新、全量 `verify_s1..s6` 回归、tag `v1.0`。

**验收**：

```bash
for f in scripts/verify_s*.sh; do bash "$f" || echo "FAIL $f"; done
curl -sI http://127.0.0.1:8000/api/tasks/$TASK/preview | grep -i 'content-security-policy'
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/tasks/not-exist   # 404
```

**回滚**：`git checkout s5`。

## 5. 进度跟踪

图例：✅ 完成｜◐ 部分完成｜⬜ 未开始

| 步骤 | 代码 | verify 脚本 | 步骤文档 | 已部署验证 | tag |
|---|---|---|---|---|---|
| S1 | ✅ | ✅ 11/11 | ✅ [s1.md](steps/s1.md) | ◐ 本地真实浏览器通过，服务器待部署 | ✅ `s1` |
| S2 | ✅ | ✅ 15/15 | ✅ [s2.md](steps/s2.md) | ◐ 本地 curl + 前端契约通过；浏览器交互与服务器部署待人工确认 | ✅ `s2` |
| S3 | ✅ | ✅ 28/28 | ✅ [s3.md](steps/s3.md) | ◐ 本地 curl + 真实浏览器交互通过；服务器部署待人工确认 | ✅ `s3` |
| S4 | ✅ | ✅ 15/15 | ✅ [s4.md](steps/s4.md) | ◐ 本地 curl + 浏览器通过（auto 降级 MOCK）；真实 MetaGPT 生成待服务器验证 | ✅ `s4` |
| S5 | ✅ | ✅ 22/22 | ✅ [s5.md](steps/s5.md) | ◐ 本地 curl + 真实浏览器（playwright-cli）通过；服务器部署待人工确认 | ✅ `s5` |
| S6 | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

每完成一格就更新本表，避免"看起来做完了"与"实际能演示"之间的偏差。

## 6. 时间与降级策略

| 情形 | 处理 |
|---|---|
| 单步超过预估 1.5 倍 | 停止加新功能，先把该步收尾并打 tag，再评估是否跳过后续步骤 |
| S4 环境装不上（3.12 / faiss / 网络） | 保持 `MOCK_ENGINE=1` 交付，把 MetaGPT 作为文档中的"已设计未启用"，优先保证 S5/S6 |
| S5 时间不足 | 版本历史降级为"仅保留列表 + 切换预览"，不做回滚写入 |
| S6 时间不足 | 至少完成 CSP/sandbox 与 `docs/delivery.md`，verify 只跑 s3 之后 |
| 任何一步部署失败 | 先回滚到上一个 tag 保证线上可用，再本地排查 |

## 7. 与其它文档的关系

| 文档 | 作用 |
|---|---|
| [design.md](design.md) | 架构与契约的权威来源；实现有冲突时以它为准 |
| [roadmap.md](roadmap.md) | 本文档：执行顺序、验收标准、进度 |
| [setup.md](setup.md) / [setup-windows.md](setup-windows.md) | 环境搭建（uv / Python 3.11 / LLM 配置） |
| `docs/steps/sN.md` | 每步落地后补写：改了什么、怎么部署、怎么回滚 |
| `docs/delivery.md` | 最终交付说明（S6 产出） |
