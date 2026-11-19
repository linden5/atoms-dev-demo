# 环境搭建与运行指南（Linux + uv）

本文档覆盖从零到跑通的完整步骤：uv 安装、Python 3.11 环境、依赖安装、LLM 配置、验证、启动服务、部署与故障排查。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Ubuntu 22.04 / 24.04（x86_64） | Windows 请使用 WSL2，勿用原生 Windows Python |
| 系统 Python | 任意（22.04 自带 3.10 / 24.04 自带 3.12） | **不要使用系统 Python 安装依赖** |
| 实际运行版本 | **Python 3.11** | MetaGPT 要求 `>=3.9,<3.12`，3.12 无法安装 |
| 包与版本管理 | **uv** | 负责下载 3.11、建虚拟环境、装依赖 |
| 内存 | ≥ 2 GB | 2C2G 轻量服务器可用，装依赖时建议加 swap |
| 端口 | 8000/TCP | 需在云厂商安全组与防火墙放行 |

> 关键点：服务器自带的 Python 3.12 装不上 MetaGPT（`aiohttp==3.8.6`、`faiss-cpu==1.7.4` 无 3.12 wheel）。不要去降级系统 Python，用 uv 装一个独立的 3.11。

## 快速开始

### 1. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
source ~/.bashrc
uv --version
```

### 2. 安装 Python 3.11（不改动系统 Python）

```bash
uv python install 3.11
uv python list          # 确认 3.11.x 已安装
```

### 3. 创建虚拟环境

```bash
cd ~/atoms-dev-demo
uv venv --python 3.11 .venv
source .venv/bin/activate
python -V               # 必须输出 3.11.x
```

### 4. 安装依赖

```bash
# 基础段（S2 起，必装）
uv pip install fastapi "uvicorn[standard]" aiosqlite loguru httpx

# 引擎段：metagpt 目前装不上，不要执行下面这行，原因见紧随其后的说明
# uv pip install metagpt
```

> **metagpt 无法通过 pip 安装（2026-11 实测，全版本）**：0.6.0–0.8.2 钉 `lancedb==0.4.0`，0.3.0–0.5.2 钉 `lancedb==0.1.16`，这两个发布版本已从 PyPI 移除，依赖解析第一步就失败，与 Python 版本、平台都无关。此外 metagpt 0.8.1 的 `pydantic==2.6.4`、0.8.2 的 `websockets<12` / `typing-extensions==4.9.0` 与本项目的 fastapi/uvicorn 冲突。需要 MetaGPT 能力请走 Docker 路线：[metagpt-docker.md](metagpt-docker.md)。

国内网络较慢时可临时切镜像：

```bash
export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
```

依赖较重（MetaGPT 会拉取约 148 个包，含 `faiss-cpu`、`pandas`、`numpy`），首次安装预计 3–6 分钟。

### 5. 配置 LLM

```bash
metagpt --init-config      # 生成 ~/.metagpt/config2.yaml
```

编辑 `~/.metagpt/config2.yaml`，使用 DeepSeek（按量付费，成本极低）：

```yaml
llm:
  api_type: "openai"
  model: "deepseek-chat"
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-你的key"
```

注意：`base_url` 末尾的 `/v1` 不能省略；`api_type` 必须是 `openai`（DeepSeek 兼容 OpenAI 协议）。

#### 5.1 能力档：一份文件一套模型配置

部署多台机器、或者要在「便宜的档位调试 / 强的档位演示」之间切换时，不要反复手改 `config2.yaml`。把每套配置存成一个档位文件，启动时用 `LLM_PROFILE` 指定加载哪一份。

档位文件是平铺的 `KEY=VALUE`（与 systemd `EnvironmentFile` 同格式），默认目录 `~/.atoms/llm`，可用 `LLM_PROFILE_DIR` 改：

```bash
bash scripts/switch_model.sh init cheap        # 生成 ~/.atoms/llm/cheap.env，权限 600
#    编辑该文件，至少填 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
bash scripts/switch_model.sh check cheap       # 只校验：必填项、权限、属主、/v1 结尾
bash scripts/switch_model.sh use cheap         # 写 drop-in → 重启 → 探活，失败自动回滚
bash scripts/switch_model.sh show              # 看当前生效的档位
bash scripts/switch_model.sh list              # 列出所有档位
```

`use` 在 `/etc/systemd/system/atoms.service.d/llm-profile.conf` 写三行，然后 `daemon-reload` + `restart`，并轮询 `/health` 确认 `profile` 变成目标档位；没变就恢复上一份 drop-in 并重启。

**日常启动/切档一条命令**（推荐，代替上面分步操作）：

```bash
bash scripts/restart_and_verify.sh dev     # 档位必须显式指定（dev / strong …）
```

启动前会先打印该档位使用的模型（`档位 dev 将使用模型：glm-5.3-flash`），确认无误才继续。流程：自动同步代码与依赖（deploy.sh）→ 停旧服务 → 按档位启动 → 健康检查 → 模型连通冒烟。

它做四件事，任何一步失败立即停下（非零退出）并提示回滚命令：① 档位校验与切换（含自动回滚）；② `/health` 断言档位/引擎/模型都对上；③ 模型连通冒烟——按档位文件直接向厂商发一次 `max_tokens=64` 的真实请求（费用可忽略），提前暴露 401、404、思考吃光输出预算这三类"服务起来了但模型用不了"的问题；④ 可选追加 `--full` 跑最新步骤的 `verify_sN.sh` 全量验收。`--no-smoke` 跳过冒烟，`--no-deploy` 跳过代码同步。

**安全约定**（照做即可，不合规时 `check` 会给 WARN）：

| 项 | 要求 | 原因 |
|---|---|---|
| 档位文件 | 权限 `600`、属主 = 服务用户 | 里面是密钥 |
| 版本控制 | 绝不提交（`~/.atoms`、`/etc/atoms` 都不在仓库里） | 密钥进 git 历史就撤不掉 |
| systemd unit | 不写 `LLM_API_KEY`，只写 `EnvironmentFile` | `systemctl show` / `journalctl` 能看到 `Environment=` |
| `/health` | 只回显档位名，不回显 Key 与端点 | 探活接口是公开的 |

没有 systemd 的机器（含 Windows 开发机）直接用环境变量等价实现：

```bash
export LLM_PROFILE=cheap
export LLM_PROFILE_DIR="$HOME/.atoms/llm"
# 之后启动服务即可；已存在的 LLM_* 变量优先级高于档位文件
```

档位文件里可以写任意 `LLM_*` 变量，常用的几个：`LLM_MAX_TOKENS`（输出上限）、`LLM_TIMEOUT`（单次超时）、`LLM_TEMPERATURE`、`LLM_REASONING_EFFORT`。最后这个值得单独说：一部分推理模型无法关闭思考，默认强度会把 `max_tokens` 全花在思考上、`content` 返回空字符串；这种模型建议在档位里加 `LLM_REASONING_EFFORT=low`。踩到时引擎会直接报「模型把输出预算都用在思考上（reasoning_tokens=…）」，不会伪装成「输出过短」。

两个容易踩的点：

- **档位文件里不要写 `ENGINE_MODE`**。`switch_model.sh init` 生成的模板里这一行是注释掉的，原因写在那里：systemd 先应用 unit 的 `Environment=`、再应用 drop-in 的 `EnvironmentFile=`，档位里的 `ENGINE_MODE=llm` 会把 unit 里用来降级的 `ENGINE_MODE=mock` 顶掉。引擎模式归 unit 管，档位只管模型参数。
- **MOCK 模式不受档位影响**。已明确指定 MOCK（`ENGINE_MODE=mock` 或 `MOCK_ENGINE=1`）时，档位文件缺失、名字非法、甚至不可读都只记一条 WARN 并继续启动，`/health` 的 `profile` 为空、`cache.enabled` 为 `false`。演示兜底不该被模型配置挡住；但切回真实引擎前必须先补齐档位，否则启动会直接报错。

切换档位必须重启服务：引擎在进程启动时读配置，运行中换文件会出现"以为切了其实没切"。MetaGPT 引擎启动时会把当前 `LLM_*` 同步写进 `~/.metagpt/config2.yaml`（首次覆盖前留 `.atoms-bak`），所以两个引擎会一起跟着档位走。

#### 5.2 生成缓存（调试省钱）

同一次生成请求（含三段 prompt）会按哈希缓存在 `workspace/.cache/`，命中就不再发请求，`meta.json` 里的 `cache_hits` 能看出命中几次：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_CACHE` | `1` | `0` 关闭缓存 |
| `LLM_CACHE_DIR` | `$WORKSPACE_DIR/.cache` | 缓存目录，随 `WORKSPACE_DIR` 走 |
| `LLM_CACHE_MAX_ENTRIES` | `200` | 超出后按最近使用淘汰 |

缓存键包含引擎、档位、模型、端点与采样参数，所以换档位一定重新生成，不会拿到上一档的旧输出。清空直接删目录即可。

### 6. 验证环境（必须通过再往下做）

```bash
metagpt "Write a cli snake game"
```

通过标准：命令执行完成，当前目录出现 `workspace/` 且在其中有生成产物。单次生成通常需要数分钟。

## 启动服务

```bash
# 开发模式（热重载，接口层可用 mock 引擎）
MOCK_ENGINE=1 uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 生产模式（真实引擎）
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 `http://<公网IP>:8000`。

自测（无需浏览器）：

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/api/generate -H 'Content-Type: application/json' \
     -d '{"prompt":"做一个番茄钟，单文件 HTML"}'
curl -s localhost:8000/api/tasks/<task_id>
```

独立调试引擎层（不经 Web）：

```bash
python scripts/gen_once.py "做一个番茄钟，单文件 HTML"
```

## 网络与端口

```bash
# 服务器本机防火墙
sudo ufw allow 8000/tcp        # 如启用了 ufw

# 云厂商安全组：入方向放行 TCP:8000，来源 0.0.0.0/0
```

不购买域名、不做备案：直接使用公网 IP + 非标准端口访问。80/443 未备案会被拦截，不要使用。

检查服务是否真的在监听：

```bash
ss -ltnp | grep 8000
```

## 后台常驻（可选）

`/etc/systemd/system/atoms.service`：

```ini
[Unit]
Description=Atoms Demo
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/atoms-dev-demo
Environment=MOCK_ENGINE=0
ExecStart=/home/ubuntu/atoms-dev-demo/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now atoms
sudo journalctl -u atoms -f
```

## 常用命令

| 目的 | 命令 |
|---|---|
| 激活环境 | `source .venv/bin/activate` |
| 装包 | `uv pip install <pkg>` |
| 导出依赖 | `uv pip freeze > requirements.txt` |
| 按文件装依赖 | `uv pip install -r requirements.txt` |
| 查看日志 | `tail -f logs/app.log` |
| 查看数据库 | `sqlite3 data/app.db "select id,status,substr(prompt,1,30) from tasks;"` |

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `requires_python <3.12` / `aiohttp` 编译失败 | 用了系统 Python 3.12 | 用 `uv venv --python 3.11` 重建环境 |
| `faiss-cpu` 装不上 | 平台/glibc 不匹配 | 依次尝试：`uv pip install --no-cache "faiss-cpu==1.7.4"` → `uv python install 3.10` 换小版本 → 重装系统镜像为 Ubuntu 22.04（自带 3.10） |
| `metagpt: command not found` | 未激活 venv | `source .venv/bin/activate` |
| 调用 LLM 报 401/404 | Key 错或 `base_url` 缺 `/v1` | 检查 `~/.metagpt/config2.yaml` |
| 生成很慢或超时 | 单次生成 5–10 分钟属正常 | 任务后台化 + 前端轮询 + 硬超时 600s |
| 浏览器打不开页面 | 安全组/防火墙未放行 | 放行 8000/TCP，`ss -ltnp` 确认监听 |
| 安装过程 OOM | 2G 内存偏紧 | `sudo fallocate -l 2G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` |

## 验收标准

1. 打开 `http://<公网IP>:8000`，输入需求后能看到生成中状态与阶段进度
2. 生成完成后可在页面内预览并交互（iframe 沙箱渲染）
3. 刷新页面后任务与产物仍在（SQLite 持久化生效）
4. 仓库与演示链接均为 public，无痕窗口可完整走通
5. 附简要说明文档：实现思路与关键取舍、完成程度、后续扩展与优先级

## 工程约定

- `workspace/`、`data/`、`.venv/`、`__pycache__/` 不纳入版本控制
- 生成任务的产物路径固定为 `workspace/{task_id}/`，接口只允许读取该目录下的白名单文件
- 环境变量 `MOCK_ENGINE=1` 时引擎层返回固定产物，用于在不消耗 LLM 额度的前提下调试接口层
