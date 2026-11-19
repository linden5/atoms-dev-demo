# MetaGPT 路线：为什么 pip 装不上、Docker 怎么接、产物和调试在哪

> 适用场景：想把「真实 MetaGPT 生成」作为能力接进来，但本地/服务器的 Python 环境装不上 metagpt。
> 现状：**本文是实施方案，代码尚未落地**（`scripts/metagpt_service.py` 与 `app/engine/remote_engine.py` 待实现）。
> 结论先行：Docker 能解决"装不上"，但**不解决"慢"**。交付主路径仍是 [steps/s8.md](steps/s8.md) 的 `ENGINE_MODE=llm`；本文是备用/展示路线。

## 1. 本地 pip 安装为什么行不通（已实测，不是猜测）

### 1.1 直接原因：metagpt 钉了一个不存在的 lancedb 版本

metagpt 全系列把 lancedb 钉成精确版本，而这两个版本如今在 PyPI 上已经不存在：

| metagpt 版本 | 钉的 lancedb | 结果 |
|---|---|---|
| 0.3.0 / 0.4.0 / 0.5.2 | `lancedb==0.1.16` | `No solution found` |
| 0.6.0 – 0.8.2 | `lancedb==0.4.0` | `No solution found` |

PyPI 上 lancedb 的版本从 0.3.x 直接跳到 0.30.0，0.4.0 与 0.1.16 都查不到（`pypi.org/pypi/lancedb/0.4.0/json` 返回 404，清华镜像也没有）。所以失败发生在依赖解析的第一步，**与 Python 版本、操作系统、是否有 wheel 都无关**。

实测（`.venv311`，Python 3.11.16，清华索引）：

```
metagpt==0.3.0 → Because there is no version of lancedb==0.1.16 ...
metagpt==0.5.2 → Because there is no version of lancedb==0.1.16 ...
metagpt==0.7.7 → Because there is no version of lancedb==0.4.0 ...
metagpt==0.8.2 → Because there is no version of lancedb==0.4.0 ...
```

### 1.2 即使放宽 lancedb，还有三层冲突

以本项目固定的 `fastapi==0.163.2` + `uvicorn[standard]==0.54.0` 为基准，用 `uv --override` 放宽 lancedb 后继续实测，会依次撞上：

| 冲突 | 来源 | 实测信息 |
|---|---|---|
| pydantic | metagpt 0.8.0/0.8.1 钉 `pydantic==2.5.3` / `==2.6.4`，fastapi 0.163.2 要 `>=2.9.0` | `fastapi==0.163.2 and metagpt==0.8.1 are incompatible` |
| websockets | metagpt 全系要 `<12` / `~=12.0`，`uvicorn[standard]==0.54.0` 要 `>=13` | `metagpt>=0.8.2 and uvicorn[standard]>=0.54.0 are incompatible` |
| typing-extensions | metagpt 0.8.2 钉 `==4.9.0`，fastapi 的 `typing-inspection>=0.4.2` 要 `>=4.12.0` | `metagpt==0.8.2 and fastapi==0.163.2 are incompatible` |

补充：0.5.x 及更早用的是 pydantic v1（`pydantic==1.10.8`），与当前 fastapi 完全不能共存；而且它们的 `Action/Role` API 与本项目引擎代码也不匹配。

### 1.3 唯一"能解析"的组合（不建议作为交付方案）

三处 override 之后，`uv pip install --dry-run` 能通过，约 150 个包：

```text
# requirements.override.txt（只放本地/临时环境，绝不进 requirements.txt）
lancedb>=0.30.0
websockets>=13.0
typing-extensions>=4.15.0
```

```powershell
uv pip install --python .venv311\Scripts\python.exe --override requirements.override.txt "metagpt==0.8.2"
```

但实际安装会卡在 `volcengine-python-sdk`（metagpt 的依赖，没有 wheel，需要现场从 sdist 构建 7683 个文件），Windows 上极慢且易失败；即使装上，`deploy.sh` 的 `uv pip install -r requirements.txt` 也会在服务器上失败并使部署中止。

> 结论：`requirements.txt` 里 metagpt 保持注释状态。不要用 override 方案改生产依赖。

## 2. 为什么 Docker 可行

官方镜像 `metagpt/metagpt:latest`（Docker Hub，linux/amd64）的关键事实：

| 项 | 值 |
|---|---|
| 压缩体积 / 解压体积 | 约 1.2 GB / 约 3.5 GB |
| 构建时间 | 2024-11-05（`main-20241105`，另有 `v0.8.1` tag） |
| 内含 metagpt | 0.8.1 |
| 基础镜像 | `nikolaik/python-nodejs:python3.9-nodejs20-slim`（Python 3.9 + Node 20 + chromium） |
| 安装方式 | `pip install -r requirements.txt` + `pip install -e .`，**依赖在构建时固化** |
| 工作目录 | `/app/metagpt` |
| 配置文件 | 容器内 `/app/metagpt/config/config2.yaml` |
| CMD | `["sh","-c","tail -f /dev/null"]`（默认不启动任何服务，必须自己覆盖） |
| EXPOSE | 未声明 |

**为什么它绕过了 1.1 的死结**：依赖是 2024 年构建时解析并装进镜像的，那时 lancedb 0.4.0 还在；拉镜像不需要再解析 PyPI，所以那个幽灵版本永远不会被触发。

代价：镜像老（0.8.1）、体积大、单次生成仍然是 5–10 分钟。

## 3. 架构：metagpt 变成"服务"，app 变成"客户端"

本项目的 `app/engine/metagpt_engine.py` 是**进程内** `import metagpt`，跨机器用不了。因此接入 docker 后必须换一种耦合方式：

```
本地(Windows, Python 3.14)                 服务器(Ubuntu, 有 Docker)
┌───────────────────────────────┐  SSH 隧道  ┌────────────────────────────────┐
│ uvicorn app.main              │ ────────▶ │ 127.0.0.1:8090                 │
│ ENGINE_MODE=remote            │           │ docker: metagpt-svc            │
│ RemoteEngine                  │ ◀──────── │  scripts/metagpt_service.py    │
│ 写 workspace/{task_id}/       │  HTML     │  → MetaGPTEngine.run() → LLM   │
└───────────────────────────────┘           │  配置 /app/metagpt/config/...  │
                                            └────────────────────────────────┘
```

两种形态，产物位置不同（见第 6 节）：

| 形态 | app 跑在哪 | 适用 |
|---|---|---|
| A. 本地 app + 服务器容器 | 本地 | 日常开发调试（推荐） |
| B. app 与容器都在服务器 | 服务器 | 公网演示；本地只用浏览器 |

两种形态都需要 `RemoteEngine`，因为**服务器上的 `.venv` 同样装不上 metagpt**。

## 4. 需要新增的代码（两个文件 + 四处接线）

### 4.1 容器内：`scripts/metagpt_service.py`

- 只用标准库 `http.server`，**不要引 fastapi**：镜像里的 pydantic 是 2.5.x，装新 fastapi 会重现 1.2 的冲突。
- `sys.path.insert(0, "/app/atoms")` 后可**直接复用** `app.engine.metagpt_engine`：该模块只依赖 `app/engine/base.py` 与 `html_utils.py`，都不碰 fastapi。
- 每次请求在 `/tmp/gen-<uuid>/` 下调用 `MetaGPTEngine.run()`，读完 `index.html` 返回，不落业务产物。

HTTP 契约：

```text
GET  /health
     → 200 {"ok":true,"metagpt":"0.8.1","configured":true}

POST /generate
     body {"prompt":"做一个番茄钟","timeout":900}
     → 200 {"ok":true,"html":"<!doctype html>...","raw_chars":12345,
            "mode":"role|llm","elapsed_s":312.4}
     → 4xx/5xx {"ok":false,"error":"可读的中文原因"}
```

### 4.2 宿主：`app/engine/remote_engine.py`

- 实现 `Engine` 契约（只写 `workdir`、可取消、阶段尽力而为）。
- `urllib.request` + `asyncio.to_thread`，与 `llm_engine.py` 同风格，**不新增依赖**。
- 收到 HTML 后写 `index.html` + `meta.json`（字段对齐 `metagpt_engine` 的 meta：`engine/mode/raw_chars/elapsed_s`）。
- 超时取 `settings.task_timeout + 30`；401/404/连接失败映射成人话。

### 4.3 接线（每处几行）

| 文件 | 改动 |
|---|---|
| `app/config.py` | `ENGINE_MODE` 增加 `remote`；新增 `METAGPT_REMOTE_URL`（默认空）、`METAGPT_REMOTE_TOKEN`（可选） |
| `app/tasks.py` | `get_engine()` 增加 remote 分支；`auto` 顺序：LLM → remote（URL 已配）→ MetaGPT（可导入）→ MOCK |
| `scripts/gen_once.py` | `--engine remote`；`--engine metagpt` 保持"装不上就明确报错" |
| `app/api.py` | `/health` 的 `engine` 自然回显 `remote`，无需额外字段 |

## 5. 服务器操作（一次性）

### 5.1 腾讯云内网加速（仅腾讯云 CVM 有效）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "registry-mirrors": ["https://mirror.ccs.tencentyun.com"] }
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
docker info | grep -A2 "Registry Mirrors"
```

该地址**只支持内网访问**，非腾讯云主机配了也没用；且只加速 Docker Hub（`docker.io`），对 ghcr/quay 无效。

### 5.2 拉镜像 + 零额度冒烟

```bash
docker pull metagpt/metagpt:latest          # 1.2 GB，内网加速通常 1–3 分钟
docker run --rm metagpt/metagpt:latest metagpt --help   # 不调模型、不计费
```

### 5.3 备份到自己仓库（强烈建议）

镜像 2024-11 构建，一旦 Docker Hub 上的 tag 被删或拉不动就没有退路：

```bash
docker login ccr.ccs.tencentyun.com -u <账号ID>    # 个人版免费
docker tag  metagpt/metagpt:latest ccr.ccs.tencentyun.com/<命名空间>/metagpt:0.8.1
docker push ccr.ccs.tencentyun.com/<命名空间>/metagpt:0.8.1
```

以后重建机器直接 `docker pull ccr.ccs.tencentyun.com/<命名空间>/metagpt:0.8.1`（VPC 内网链路）。

### 5.4 LLM 配置（Key 只留服务器）

```bash
mkdir -p /opt/metagpt/{config,workspace}
docker run --rm metagpt/metagpt:latest \
  cat /app/metagpt/config/config2.yaml > /opt/metagpt/config/config2.yaml
vi /opt/metagpt/config/config2.yaml
# llm:
#   api_type: openai
#   base_url: https://api.deepseek.com/v1    # /v1 不能省
#   model: deepseek-chat
#   api_key: sk-...
```

### 5.5 真实生成冒烟（5–10 分钟，计费）

```bash
docker run --rm \
  -v /opt/metagpt/config/config2.yaml:/app/metagpt/config/config2.yaml \
  -v /opt/metagpt/workspace:/app/metagpt/workspace \
  metagpt/metagpt:latest metagpt "Write a cli snake game"
ls /opt/metagpt/workspace
```

### 5.6 起常驻服务

```bash
docker rm -f metagpt-svc 2>/dev/null
docker run -d --name metagpt-svc --restart unless-stopped \
  -v /opt/metagpt/config/config2.yaml:/app/metagpt/config/config2.yaml \
  -v /home/ubuntu/atoms-dev-demo:/app/atoms:ro \
  -v /opt/metagpt/workspace:/app/metagpt/workspace \
  -p 127.0.0.1:8090:8090 \
  metagpt/metagpt:latest \
  python /app/atoms/scripts/metagpt_service.py --host 0.0.0.0 --port 8090

curl -s localhost:8090/health
docker logs --tail 50 metagpt-svc
```

两个容易踩错的地方：

1. **容器内必须监听 `0.0.0.0`**，否则 `-p` 映射连不上（docker-proxy 连的是容器 IP，不是容器 loopback）。
2. **宿主侧只绑 `127.0.0.1`**（`-p 127.0.0.1:8090:8090`）。写成 `-p 8090:8090` 就是把额度挂到公网上。

## 6. 产物在哪、调试麻不麻烦

### 6.1 产物位置

| 形态 | 产物写入 | 预览 / 版本链 / 发布 | 说明 |
|---|---|---|---|
| A. 本地 app | **本地** `workspace/{task_id}/` | 全部在本地完成 | 容器只回传 HTML 文本，不共享产物目录 |
| B. app 在服务器 | 服务器 `~/atoms-dev-demo/workspace/{task_id}/` | 在服务器完成，本地浏览器访问 | 看文件要 SSH，或走 `/api/tasks/{id}/files/{path}` |

**所以"产物只能在服务器上"不成立**：形态 A 里本地 app 拿到 HTML 后自己写盘，`scan_artifacts`、预览、版本链、ZIP、发布全部走原有逻辑，一行都不用改。服务器容器里那份 `/tmp/gen-*` 用完即弃；`/opt/metagpt/workspace` 只是镜像默认挂载点，与业务无关。

### 6.2 本地连服务器的三种方式

| 方式 | 命令 | 评价 |
|---|---|---|
| SSH 隧道（推荐） | `ssh -N -L 8090:127.0.0.1:8090 ubuntu@<IP>` | 不暴露端口，最安全；断线重连即可 |
| 公网直连 | `-p 8090:8090` + `METAGPT_REMOTE_URL=http://IP:8090` | **不要用**：任何人可烧额度 |
| 公网直连 + Token | 同上 + 云防火墙限源 IP + `METAGPT_REMOTE_TOKEN` | 临时演示可用，需两处都配 |

隧道建议带保活（长任务 5–10 分钟，空闲隧道会被掐）：

```powershell
ssh -N -L 8090:127.0.0.1:8090 `
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
    ubuntu@<公网IP>
```

本地环境：

```powershell
$env:ENGINE_MODE="remote"
$env:METAGPT_REMOTE_URL="http://127.0.0.1:8090"
$env:TASK_TIMEOUT="900"          # 600s 会截断真实生成
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl.exe -s http://127.0.0.1:8000/health     # engine=remote
```

本机不需要 Python 3.11、不需要 metagpt、不需要 metagpt 的 Key——这是这个方案最大的好处。

### 6.3 调试体验：哪一层看哪里

| 你要看什么 | 在哪看 |
|---|---|
| 前端 / 预览 / 版本链 / 发布 | 本地浏览器 + `logs/app.log`，与 MOCK 引擎完全一致 |
| 产物到底是什么 | 本地 `workspace/{task_id}/index.html`、`meta.json` |
| 生成过程中的阶段 | 本地前端只有三段（planning/coding/checking）；远端细节看 `docker logs -f metagpt-svc` |
| 容器里到底输出了什么 | `docker exec metagpt-svc ls -la /tmp/gen-*`（服务保留最后一轮可加 `--keep`） |
| 适配器本身有没有 bug | **本地起假服务**：`python scripts/metagpt_service.py --fake --port 8090`，不需要服务器 |

`--fake` 模式很关键：它让 `RemoteEngine` 的成功 / 超时 / 非 200 / 返回非 HTML 四条路径都能在本地秒级验证，不用每次都等 5 分钟、花一次钱。

### 6.4 断线、超时、取消的语义

| 情形 | 结果 | 用户看到 |
|---|---|---|
| 隧道断了 / 服务没起 | `RemoteEngine` 抛连接错误 → 任务 `failed` | 红色横幅"连不上生成服务" |
| 超过 `TASK_TIMEOUT` | 本地协程被取消；**容器里的生成不会停**，仍会计费 | `timeout after Ns` |
| 本地点取消 | 任务 `cancelled`；容器照旧跑完 | 预览显示"任务已取消" |
| 服务返回非 HTML | `ValueError` → `failed` | 原因透传 |

取消无法中断远端是 HTTP 方案的固有代价。要彻底解决得给 service 加 `/cancel` + 任务注册表，工作量翻倍，当前不做。

## 7. 故障排查

| 现象 | 先查什么 |
|---|---|
| `docker pull` 卡住 / 超时 | 是否腾讯云 CVM；`docker info \| grep -A2 "Registry Mirrors"` 是否含 `mirror.ccs.tencentyun.com` |
| 容器起来就退出 | 覆盖的 CMD 写错（镜像默认只 `tail -f /dev/null`）；`docker logs metagpt-svc` 看 traceback |
| `curl localhost:8090/health` 连不上 | service 是否监听 `0.0.0.0`；`-p` 是否绑到 `127.0.0.1` 后本地走了隧道 |
| 本地 `engine=remote` 但任务一直 failed | 隧道是否活着；`METAGPT_REMOTE_URL` 是否带 `http://` |
| 服务返回 `ok:false` 且提到 API | `config2.yaml` 的 `base_url` 是否漏 `/v1`、Key 是否过期；`docker exec metagpt-svc metagpt --help` 是否正常 |
| 生成成功但本地预览空白 | 本地 `workspace/{task_id}/` 有没有 `index.html`；`scan_artifacts` 的入口判定 |
| 内存告警 / OOM | 镜像含 chromium + node；容器加 `--memory 1g`，宿主加 swap |

## 8. 降级与回滚

| 层级 | 操作 | 影响 |
|---|---|---|
| 只停 metagpt | 本地 `ENGINE_MODE=llm`（或 `mock`）重启 | app 完全不依赖容器，容器可随时 `docker stop` |
| 停容器 | `docker stop metagpt-svc` | 只有 `remote` 引擎失败，其他引擎不受影响 |
| 撤销改动 | 回退 `remote_engine.py` + 四处接线 | `ENGINE_MODE` 里没有 `remote` 时会退回 `auto` 逻辑 |

## 9. 验收清单

1. `docker run --rm metagpt/metagpt:latest metagpt --help` 正常
2. `docker run --rm ... metagpt "Write a cli snake game"` 在 `/opt/metagpt/workspace` 产出文件
3. `curl localhost:8090/health` 返回 `ok:true` 且 metagpt 版本非空
4. `POST /generate` 用短提示词，5–10 分钟内返回 `html` 且含 `<html`
5. 本地 `gen_once.py --engine remote` 退出码 0，`workspace/cli_*/index.html` 非空
6. 本地 `/health` 显示 `engine=remote`；浏览器提交需求 → 三阶段 → 预览可交互
7. 断开隧道再提交 → 任务 `failed` 且提示"连不上生成服务"（不是卡在 running）
8. `--fake` 服务的四个场景（成功/超时/500/非 HTML）在本地都能复现预期行为

## 10. 已知限制（要写进交付说明）

1. **时延不变**：单次生成 5–10 分钟，比 `llm_engine` 慢一个数量级；演示时不适合现场首次生成。
2. **镜像老**：0.8.1，2024-11 构建；`Role/Action` API 若有变化会在第一次 import 暴露（先跑第 5.5 步冒烟）。
3. **两个进程 + 一条隧道**：任何一环断了任务就失败，运维面比单进程大。
4. **取消不中断远端**：见 6.4。
5. **资源**：3.5 GB 磁盘 + 常驻 chromium；2C2G 服务器需加 swap，并考虑 `--memory` 限制。
6. **不能替代 `llm_engine`**：它只是"用了 MetaGPT 框架"的叙事，不带来质量或速度上的收益。
