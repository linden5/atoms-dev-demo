# Git 提交规范

本规范以 [Conventional Commits 1.0.0（约定式提交）](https://www.conventionalcommits.org/zh-hans/v1.0.0/) 为基础，补充了本项目的具体约定（scope 取值、tag 规则、提交前检查）。

参考来源：

- Conventional Commits 1.0.0：`<type>[optional scope]: <description>`，`fix` 对应 PATCH、`feat` 对应 MINOR、`BREAKING CHANGE` 对应 MAJOR
- [SemVer 2.0.0](https://semver.org/lang/zh-CN/)
- [git interpret-trailers](https://git-scm.com/docs/git-interpret-trailers)（脚注格式）

## 1. 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

- **type** 必填，英文小写
- **scope** 选填，本项目的模块名（见第 3 节）
- **subject** 必填，中文动宾短语，不加句号，不超过 50 个字符
- **body** 选填，说明「为什么」而不是「做了什么」，每行不超过 72 字符
- **footer** 选填，`BREAKING CHANGE:`、`Refs:`、`Closes:` 等
- type/scope 与 subject 之间的冒号后**必须**有一个空格
- 冒号必须是英文半角 `:`

## 2. type 取值

| type | 用途 | 对应 SemVer |
|---|---|---|
| `feat` | 新增功能 | MINOR |
| `fix` | 修复缺陷 | PATCH |
| `docs` | 仅文档变更 | 无 |
| `style` | 仅格式（空格、分号、缩进），不改语义 | 无 |
| `refactor` | 重构，既不新增功能也不修缺陷 | 无 |
| `perf` | 性能优化 | 无 |
| `test` | 增删改测试、验收脚本 | 无 |
| `build` | 依赖、构建配置（`requirements.txt`、`pyproject`） | 无 |
| `ci` | CI 配置 | 无 |
| `chore` | 杂项（`.gitignore`、`deploy/*.service` 等） | 无 |
| `revert` | 回滚提交，body 里注明被回滚的 commit hash | 视情况 |

`feat` 与 `fix` 之外的类型不会自动影响版本号，除非包含 `BREAKING CHANGE`。

## 3. scope 取值

本项目的 scope 与目录/模块一一对应，避免造新词：

| scope | 含义 |
|---|---|
| `web` | `web/` 前端页面与样式 |
| `api` | `app/api.py` 路由与契约 |
| `tasks` | `app/tasks.py` 编排、状态机 |
| `engine` | `app/engine/`（base / mock / llm / metagpt） |
| `db` | `app/db.py`、`app/models.py`、表结构 |
| `deploy` | `deploy/`、systemd、端口 |
| `scripts` | `scripts/` 下的部署与验收脚本 |
| `docs` | `docs/` 下的文档 |
| `s1`…`s8` | 仅用于「整步收尾」的提交，跨多个模块时使用 |

一个提交只影响一个 scope；跨模块改动请拆成多个提交。

## 4. 破坏性变更

两种标记方式（至少一种，`!` 与脚注可同时使用）：

```
feat(api)!: 任务接口改为返回 artifacts 数组

BREAKING CHANGE: `/api/tasks/{id}` 的 `artifact` 字段被移除，改用 `artifacts` 数组
```

```
feat(api): 任务接口支持 artifacts 数组

BREAKING CHANGE: 移除单数字段 `artifact`
```

- `!` 必须紧贴 `:` 之前，即 `<type>(<scope>)!:`
- 脚注中的 `BREAKING CHANGE` 必须全大写，冒号后跟一个空格
- 本项目已定「DB schema 只增不改」，真正的破坏性变更应当**避免**；一旦出现必须在 body 里说明回滚方法

## 5. 按类别拆分提交

不同类别的文件**必须分开提交**，一个提交只承载一个类别。批量 `git add -A` 一律视为错误操作。

| 类别 | 典型路径 | 推荐的 type / scope |
|---|---|---|
| 文档 | `README.md`、`AGENTS.md`、`docs/**` | `docs` |
| 前端 | `web/**` | `feat(web)` / `fix(web)` / `style(web)` |
| 后端 | `app/**` | `feat(api)` / `feat(tasks)` / `feat(engine)` / `fix(db)` |
| 测试 | `scripts/verify_*.sh`、`tests/**` | `test(scripts)` / `test(db)` |
| 构建 | `requirements.txt`、`pyproject.toml`、锁文件 | `build` |
| 配置 | `.gitignore`、`.gitattributes`、`deploy/**`、`scripts/deploy.sh`、`.vscode/**` | `chore(deploy)` / `chore` |

规则：

- 一次改动涉及多个类别时，拆成多个提交，**按依赖顺序**提交：配置 → 构建 → 后端 → 前端 → 测试 → 文档
- 文档提交排在最后，因为它描述的代码必须已经在库里
- 例外只有 `sN` 收尾提交：允许一次性提交跨类别的收尾改动，但 body 必须逐条列出每个类别的内容
- 同类别内的无关改动（例如顺手修了个样式 bug）也要单独成一次提交

## 6. 原子性

- 一个提交只做一件事：能独立回滚、独立理解
- 禁止把格式化、重命名与功能改动混在一个提交里
- 禁止把不同类别的文件混在一个提交里（见第 5 节）
- 修 bug 时若发现无关问题，另开提交

## 7. 提交前检查（必须全部通过）

1. `git status` 复核暂存内容，确认没有夹带无关文件、没有跨类别混合
2. 对应步骤的验收脚本通过：`BASE_URL=http://127.0.0.1:8000 bash scripts/verify_sN.sh`
3. 涉及交互的改动，用真实浏览器走一遍主链路（不能只看 curl 200）
4. 不提交密钥：任何 `sk-`、`api_key`、`.env`、`~/.metagpt/config2.yaml`
5. 不提交运行时产物：`workspace/`、`data/`、`logs/`、`__pycache__/`、`.venv/`（已在 `.gitignore` 中）

## 8. tag 规则

| tag | 时机 |
|---|---|
| `s1` … `s6` | 每个步骤收尾、验收通过、可独立部署时打 |
| `v1.0` | S6 完成后的最终交付版本（与 `s6` 指向同一提交也可以） |

tag 必须打在「该步骤完整可运行」的提交上；回滚方式见 `docs/roadmap.md` 第 2 节。

## 9. 示例

```bash
# 好的提交
feat(tasks): 支持任务取消与超时回收
fix(web): 修正错误横幅在成功态仍显示的问题
docs(steps): 补充 S1 部署与回滚步骤
chore(deploy): atoms.service 改用 uvicorn 启动
refactor(engine): 抽出 scan_artifacts 与引擎解耦
test(scripts): 新增 S3 验收脚本
build: 拆分 requirements.txt 基础段与 metagpt 段

# 同时带 scope 和破坏性标记
feat(api)!: 任务列表分页返回 total 字段

# 不好的提交（禁止）
update code                      # 无 type、无信息量
fix: 修了一些问题                 # 无法判断改了什么
feat: 增加功能并调整目录结构并修复bug  # 非原子
Feat(Web): Add button.           # type 大写、subject 英文句号结尾
feat: 新增任务页并补文档并加验收脚本   # 跨类别混合，应拆成 feat(web) + docs + test
```

一个多类别的正确拆分示例（同一个功能，5 个提交）：

```bash
chore: 添加 .gitignore
chore(deploy): 添加 systemd unit 与统一部署脚本
feat(web): 交付 S1 静态工作台与演示数据
test(scripts): 添加 S1 验收脚本
docs: 补充设计、路线图、环境指南与 S1 步骤记录
```

## 10. 与 AI 协作时的额外约定

- AI 提交前必须显式报告「将提交哪些文件、为什么」，不得静默提交
- AI 不得修改 git 配置（`user.name`、`user.email`、`core.autocrlf` 等）
- AI 不得使用 `--amend`、`--force`、`--no-verify`，除非用户明确要求
- 未经用户确认，不得执行 `git push`
- 每次提交信息由 AI 起草后展示给用户，确认后再执行
