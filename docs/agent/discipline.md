# 开发纪律

本仓库所有「必须 / 禁止」的**单一出处**：`AGENTS.md` 是入口与索引，规则正文只在这里维护；
`docs/design.md` 的「安全」段是接口契约，实现细节以它为准，协作纪律以本文为准。

## 提交规范（摘要，完整版见 [git-commit.md](git-commit.md)）

1. 格式固定为 `<type>(<scope>): <subject>`，例如 `feat(tasks): 支持任务取消与超时回收`
2. `type` 只用 feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert
3. `scope` 只用 web / api / tasks / engine / db / deploy / scripts / docs / s1…s8
4. 一个提交只做一件事，禁止把功能、格式化、重命名混在一起
5. **按类别拆分提交**：文档 / 前端 / 后端 / 测试 / 构建 / 配置必须分开提交，禁止 `git add -A` 一把梭；顺序为 配置 → 构建 → 后端 → 前端 → 测试 → 文档
6. 提交前必须：`git status` 复核 + `bash scripts/verify_sN.sh` 通过 + 不夹带密钥与运行时产物
7. 每个步骤收尾打 `sN` tag，最终交付打 `v1.0`

## 每一步的工作流

```
读 docs/roadmap.md 确认当前步骤与验收标准
  → 实现（只新增必要文件，尽量不改已验收文件的签名）
  → 补 scripts/verify_sN.sh 并跑通
  → 用真实浏览器走一遍主链路（curl 200 不算通过）
  → 写 docs/steps/sN.md（改了什么 / 怎么部署 / 怎么回滚 / 验收命令）
  → 更新 docs/roadmap.md 的进度跟踪表
  → 提交（信息符合规范）+ 打 tag sN
```

## 硬性约束

- **不引入构建工具、前端框架、ORM、Celery、Redis**。前端是原生 HTML/CSS/JS，状态机与队列用 `asyncio` 原生能力
- **单 worker**：任务队列与信号量在进程内，多 worker 会导致状态不一致
- **引擎懒加载**：`app/` 顶层不得 `import metagpt`，只在 `engine/metagpt_engine.py` 内部导入，保证没装 MetaGPT 的环境也能跑 S2/S3
- **产物路径固定** `workspace/{task_id}/`，`/files/{path}` 必须 `Path.resolve()` 后校验前缀，禁止路径穿越
- **真实生成的产物不可删除**：凡是真实调用模型产生的目录（`workspace/{task_id}/`、`workspace/cli_*/`），无论通过脚本、接口、界面还是手工触发，都**不允许删除或覆盖**——它是唯一可复现的证据；要重跑就开新目录、新 prompt
- **生成结束必须输出产物路径**：命令行看 `[产物] <绝对路径>`，服务端看日志 `产物目录=<绝对路径>`；新增调用入口时必须带上这一行，不允许"生成完只报成功"
- 清理只允许针对 MOCK / 假包 / 假模型产生的临时产物，且删前先确认来源
- **预览隔离**：iframe 用 `sandbox="allow-scripts"`，不加 `allow-same-origin`
- **DB schema 只增不改**，不删列、不改列类型，保证任意 tag 都能启动
- **端口与部署形态不变**：始终 `0.0.0.0:8000` + systemd，不使用域名与 80/443
- **代码跨平台**：路径统一 `pathlib.Path`，不硬编码分隔符
- **文档必须随改动同步**：设计（`docs/design.md`）或部署脚本（`scripts/deploy*.sh`、`scripts/switch_model.sh`、`scripts/restart_and_verify.sh`、`deploy/**`）有更新时，必须同步更新对应文档（`docs/design.md`、README 的部署节、`docs/steps/` 落地记录），文档滞后视同改动未完成

## 禁止事项

- 不修改 git 配置，不 `--amend` / `--force` / `--no-verify`，未经确认不 `git push`
- 不提交任何密钥（`sk-`、`api_key`、`~/.metagpt/config2.yaml`、`.env`、`config/llm/`）
- 不提交 `workspace/`、`data/`、`logs/`、`__pycache__/`、`.venv/`
- 不为了让验收通过而削弱验收脚本；验收失败就修代码或如实记录限制
- 不静默扩大范围：计划外的改动先提出，等确认

## 修订记录

- 2026-11-19：从 `AGENTS.md` 迁入，作为纪律的单一出处；新增「真实生成的产物不可删除」「生成结束必须输出产物路径」两条；scope 的步骤区间更正为 `s1…s8`，engine 一行补上 `llm`
- 2026-11-19：新增硬性约束「文档必须随改动同步」：设计或部署脚本更新时必须同步更新文档
