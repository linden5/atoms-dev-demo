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
uv pip install metagpt fastapi "uvicorn[standard]" aiosqlite loguru httpx
```

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
