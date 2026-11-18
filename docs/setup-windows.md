# 环境搭建与运行指南（Windows + uv）

目标：在 Windows 本机跑通 FastAPI + MetaGPT 的开发环境。命令行示例以 PowerShell 为准。

> 首选方案是 **WSL2 + Ubuntu**，环境与线上服务器完全一致，可避免 `faiss-cpu`、`lancedb` 等包在两平台上行为不同的问题。用 WSL 的话直接看 [setup.md](setup.md)，本文档针对**原生 Windows** 场景。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10 1809+ / Windows 11（x64） | 需管理员权限执行部分命令 |
| 系统 Python | 任意（含 3.13/3.14） | **不要用系统 Python 安装依赖** |
| 实际运行版本 | **Python 3.11** | MetaGPT 要求 `>=3.9,<3.12`，3.12+ 无法安装 |
| 包与版本管理 | **uv** | 负责下载 3.11、建虚拟环境、装依赖 |
| 内存 | ≥ 8 GB 建议 | MetaGPT 依赖较重，2G 内存机器不建议本地装 |
| 端口 | 8000/TCP | 仅本机访问时无需放行防火墙 |

> 关键点：本机现有的 Python 3.14 完全够用，不需要卸载或替换。uv 会把 3.11 装到自己的目录，两个版本互不干扰。

## 快速开始

### 1. 安装 uv

任选其一：

```powershell
winget install --id=astral-sh.uv -e
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装后**重开一个终端**，验证：

```powershell
uv --version
```

若提示找不到命令，手动加入 PATH（通常是 `%USERPROFILE%\.local\bin`）：

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"
```

### 2. 安装 Python 3.11

```powershell
uv python install 3.11
uv python list          # 确认 3.11.x 已安装
```

### 3. 创建虚拟环境

```powershell
cd c:\Users\<你>\code\atoms-dev-demo
uv venv --python 3.11 .venv
```

激活（PowerShell）：

```powershell
.\.venv\Scripts\Activate.ps1
python -V               # 必须输出 3.11.x
```

若报“无法加载文件……禁止运行脚本”，先在当前会话放开执行策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

（用 CMD 的话激活命令是 `.\.venv\Scripts\activate.bat`；Git Bash 用 `source .venv/Scripts/activate`。）

### 4. 安装依赖

```powershell
uv pip install metagpt fastapi "uvicorn[standard]" aiosqlite loguru httpx
```

国内网络较慢时切镜像：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
```

首次安装预计 3–6 分钟（约 148 个包）。

### 5. 配置 LLM

```powershell
metagpt --init-config      # 生成 %USERPROFILE%\.metagpt\config2.yaml
```

编辑 `C:\Users\<你>\.metagpt\config2.yaml`：

```yaml
llm:
  api_type: "openai"
  model: "deepseek-chat"
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-你的key"
```

注意：`base_url` 末尾的 `/v1` 不能省略；`api_type` 必须是 `openai`。

### 6. 验证环境

```powershell
metagpt "Write a cli snake game"
```

通过标准：执行完成且当前目录出现 `workspace\` 及生成产物。单次生成通常需要数分钟。

## 启动服务

```powershell
# 开发模式（接口层，走 mock 引擎）
$env:MOCK_ENGINE = "1"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 生产模式（真实引擎）
$env:MOCK_ENGINE = "0"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000`。

自测：

```powershell
curl.exe -s http://127.0.0.1:8000/health
curl.exe -s -X POST http://127.0.0.1:8000/api/generate -H "Content-Type: application/json" -d "{\"prompt\":\"做一个番茄钟，单文件 HTML\"}"
curl.exe -s http://127.0.0.1:8000/api/tasks/<task_id>
```

> PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，写 `curl.exe` 才会走真正的 curl，避免参数不兼容。

独立调试引擎层（不经 Web）：

```powershell
python scripts\gen_once.py "做一个番茄钟，单文件 HTML"
```

## 局域网访问（可选）

默认只监听 127.0.0.1。若要手机或同事的设备访问：

```powershell
# 换监听地址
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 放行防火墙（管理员 PowerShell）
New-NetFirewallRule -DisplayName "Atoms Demo 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

然后用 `http://<本机内网IP>:8000` 访问。

## 同步到服务器

Windows 原生没有 rsync，用 scp 或 git：

```powershell
# 方式一：scp（排除目录手动指定，靠 .gitignore 排除更省事）
scp -r app web scripts requirements.txt ubuntu@<公网IP>:~/atoms-dev-demo/

# 方式二：走 Git（推荐，天然排除 .venv / workspace / data）
git add -A; git commit -m "update"; git push
# 服务器上： git pull
```

如已装 WSL，直接在 WSL 里用 rsync 更方便：

```bash
rsync -avz --exclude .venv --exclude workspace --exclude data --exclude __pycache__ /mnt/c/Users/<你>/code/atoms-dev-demo/ ubuntu@<IP>:~/atoms-dev-demo/
```

## 常用命令（PowerShell）

| 目的 | 命令 |
|---|---|
| 激活环境 | `.\.venv\Scripts\Activate.ps1` |
| 退出环境 | `deactivate` |
| 装包 | `uv pip install <pkg>` |
| 导出依赖 | `uv pip freeze > requirements.txt` |
| 按文件装依赖 | `uv pip install -r requirements.txt` |
| 看本机 Python | `uv python list`、`py --list` |
| 看端口占用 | `netstat -ano \| findstr :8000` |
| 查数据库 | `sqlite3 data\app.db "select id,status from tasks;"`（需单独安装 sqlite3） |

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `requires_python <3.12` / `aiohttp` 编译失败 | 用了系统 Python 3.13/3.14 | 确认 `python -V` 是 3.11；重建环境 `uv venv --python 3.11 .venv` |
| 激活脚本被执行策略拦截 | PowerShell 默认限制 | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `uv` 找不到命令 | 安装后未重开终端 / PATH 未生效 | 重开终端，或手动加 `%USERPROFILE%\.local\bin` |
| `faiss-cpu` 装不上 | Windows wheel 缺失或不匹配 | `uv pip install "faiss-cpu==1.7.4"` 单独试；仍失败改用 WSL2（不要把时间耗在这上面） |
| `lancedb` / `curl-cffi` 安装报错 | 无匹配 wheel | 同上，优先切 WSL2 |
| 中文乱码 | 终端代码页非 UTF-8 | `chcp 65001`；或用 Windows Terminal |
| `metagpt` 找不到配置 | 配置路径不对 | 应放在 `C:\Users\<你>\.metagpt\config2.yaml` |
| 生成很慢 | 单次生成 5–10 分钟属正常 | 任务后台化 + 前端轮询 + 硬超时 600s |
| 端口被占用 | 8000 已有进程 | `netstat -ano \| findstr :8000` 找 PID，`taskkill /PID <pid> /F` |

## 与 Linux 环境的差异（务必注意）

| 项 | Windows | Linux（服务器） |
|---|---|---|
| 路径分隔符 | `\` | `/` |
| 激活命令 | `.\.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| 环境变量 | `$env:MOCK_ENGINE="1"` | `MOCK_ENGINE=1 cmd` |
| 配置文件 | `%USERPROFILE%\.metagpt\config2.yaml` | `~/.metagpt/config2.yaml` |
| 依赖可用性 | 部分包无 wheel | 全部有 wheel |

代码里涉及路径的地方统一用 `pathlib.Path` / `os.path.join`，不要硬编码分隔符，否则部署到服务器会立刻报错。

## 验收标准

1. 本机 `http://127.0.0.1:8000` 能完整走通：输入需求 → 生成中状态 → 预览 → 刷新数据仍在
2. `MOCK_ENGINE=1` 与真实引擎两种模式都能启动
3. 代码同步到服务器后无需修改路径/命令即可运行（见 [setup.md](setup.md)）
