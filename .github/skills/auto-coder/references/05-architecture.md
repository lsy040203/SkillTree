## 5. 架构与模块

```text
packages/skilltree-core/
  registry/        # 发现和安全解析
  routing/         # 候选、Schema 校验、排序约束
  tracing/         # TraceEvent、Episode 组装
  hooks/           # Hook stdin 解码、清洗、outbox、TurnTrace 关联
  learning/        # 反馈和权重
  memory/          # 授权、Profile、程序化候选
  evolution/       # Pattern、回放、补丁候选、状态机
  security/        # 路径、清洗、敏感数据和审计
  storage/         # SQLite 仓储与迁移
packages/skilltree-cli/
LICENSE
README.md
CONTRIBUTING.md
SECURITY.md
PRIVACY.md
SUPPORT.md
CHANGELOG.md
codex-plugin/
  .codex-plugin/plugin.json
  skills/skill-router/SKILL.md
  hooks/hooks.json
  runtime/skilltree_bootstrap.ps1
  runtime/skilltree_hook.py
  scripts/setup.ps1
```

模型负责理解请求和重排，Core 负责可用性、白名单、持久化和状态迁移。Profile/已批准记忆在请求开始时冻结；本轮更新只影响下一轮。

### 阶段实施留痕契约

每个已完成的实施阶段（`P0.1`、`P0.2`、`P0.3`、`P0.4`、`P1`、`P2`、`P3.1`、`P3.2`、`P4`、`P5`、`P6`、`P7`）必须在仓库内创建或更新 `docs/implementation/<phase-id>/`；`<phase-id>` 精确等于任务标识的小写形式（例如 `p0.1`、`p3.1`）。这些文件是与代码、测试和任务状态同等的交付物；没有通过验收的工作不得写成完成事实。每个目录必须包含：

```text
docs/implementation/<phase-id>/
  WORKLOG.md
  ARCHITECTURE.md
  STATE_FLOW.md
  STORAGE.md
  IMPLEMENTATION.md
  MILESTONES.md
```

- `WORKLOG.md` 记录阶段目标、开始/完成状态、实际改动文件、实际执行的命令及其摘要结果、失败/重试、已知风险、与 DEV_SPEC 的偏差及人工确认；计划命令、推测结果或未执行验证不得表述为事实。
- `ARCHITECTURE.md` 使用 GitHub 可渲染 Mermaid 模块分层图，列出本阶段新增或变更的关键模块、类、函数和文件职责，以及依赖方向；不把未实现模块画成已存在实现。
- `STATE_FLOW.md` 使用 Mermaid 表达本阶段新增或修改的状态、触发事件、合法迁移、失败/降级分支和终态；若本阶段不引入状态机，必须明确写“本阶段不新增状态流转”，并说明理由。
- `STORAGE.md` 记录本阶段目标 migration、完整新增/变更表与索引、主键/外键/删除策略、权威数据位置与保留语义；没有 SQLite 变化时必须明确写“本阶段不新增或修改 SQLite 表/索引/迁移”，不得省略。
- `IMPLEMENTATION.md` 按实际依赖顺序列出关键接口、输入/输出 Schema、调用边界、失败处理与实现先后；引用具体文件和符号，避免只复述产品愿景。
- `MILESTONES.md` 拆分本阶段子任务、依赖、验收标准、完成状态、阻塞项和下一人工动作；它必须与 DEV_SPEC 的 `[ ]/[~]/[x]/[!]/[-]` 状态一致。

`auto-coder` 在开始一个可执行阶段前，先创建或更新该阶段六份留痕文件的骨架并将状态写为 `in_progress`；实现和测试过程中同步更新，但不得以文档代替测试。只有代码、测试、规格同步和这六份文档均完成且内容与实际工作区一致时，才可把该阶段标记为可供用户验收；用户确认前不得创建原子提交。P0.1 还必须记录 Bundle artifact 清单、各 hash、构建命令和离线验证边界。G0.5 是人工 Gate 而非可执行阶段，其证据仍只写入既定的 `docs/verification/G0.5-turn-token-compatibility.md`，不创建 `docs/implementation/g0.5/`。
