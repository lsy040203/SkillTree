## 6. 实施计划

- [x] [P0.1] 基础离线 Bundle 构建：构建 `skilltree-core` wheel 与全部基础运行时依赖 wheel，按本章 `skilltree-bundle/v1` Schema 生成带精确 SHA-256 的 `requirements.lock` 和 `runtime/bundle-manifest.json`。完成条件：所有基础 artifact 存在，P0 Manifest 的 `migration_version=1` 且只携带 `0001_p0_runtime.sql`，Manifest/lock/hash/版本一致、仅包含 wheel 而无 OCI archive 或 sdist，并有构建校验测试。
- [ ] [P0.2] BLOCKED_BY=P0.1 专用离线安装与初始迁移：按本章 `skilltree-setup/v1` 契约以显式 bootstrap `-PythonPath` 在暂存目录创建 venv，离线安装、事务应用 `0001_p0_runtime.sql`，并原子切换 `$PLUGIN_DATA/venv`/runtime state。完成条件：干净数据目录、断网且无 system site-packages 时安装成功并创建 schema version 1（仅 P0 三表）；相同 Bundle/迁移幂等；变更 Bundle 安全更新；缺失/篡改 wheel、迁移文件、pip、数据库初始化或切换失败时不降级且保留已验证旧 runtime/数据库。
- [ ] [P0.3] BLOCKED_BY=P0.2 Runtime Doctor：实现/补全 `doctor --json` 的 Plugin/Core/Schema 版本、Bundle hash、venv 完整性与 `hook_observation_state` 校验。完成条件：正常、缺 wheel、版本不符、hash 不符和未观测 Hook 各返回确定的机器可读结果。
- [ ] [P0.4] BLOCKED_BY=P0.1,P0.2,P0.3 P0 端到端验收：从干净 `$PLUGIN_DATA` 执行 Bundle 校验 → `setup.ps1` → `doctor --json` → Plugin validator，并覆盖成功与失败路径。完成条件：端到端测试通过后才将 P0.1–P0.4 全部标记 `[x]`；不得仅凭已有局部测试声明 P0 完成。
- [ ] [P1] BLOCKED_BY=P0.4 安全 Skill 注册表：发布 Bundle 升级至 migration `2`，实现 JSON 文件驱动的 setup/scan/trust/block/status、确认扫描、`pending|trusted|blocked` 信任门、注册表 CLI 和相应单元测试。完成条件：`0002_p1_registry.sql` 仅新增注册表，未确认根目录、越界路径和未信任 Skill 均不能进入候选；hash 变化不能继承旧信任决定，status 不泄露绝对路径。
- [ ] [P2] BLOCKED_BY=P1 受约束路由入口：发布 Bundle 升级至 migration `3`，实现 Top-K、RouteDecision/MemoryExtractionCandidate/RouteEnvelope Schema 校验、Hook Context Bridge 和 `skill-router`。完成条件：`0003_p2_routing.sql` 仅新增路由表；模型只获得 Hook 注入的 trusted Top-K；普通 Skill 不访问 Plugin 数据目录；`Stop` 只接受一次性 HTML 回执并原子提交合法 RouteDecision；Hook 不可用、超时或 Doctor 失败时安全降级；非法模型输出不得持久化。
- [ ] [P3.1] BLOCKED_BY=P2 最小 TurnToken 提前关联：发布 Bundle 升级至 migration `4`，实现 UserPromptSubmit 的内部 `trace-reserve`、TurnTrace、不可暴露的 turn token/session binding proof、provisional RunContext、`route_offers.provisional_run_id` 与无回执的 7 天 `maintenance sweep`，并提供官方 Hook stdin fixture。完成条件：`0004_p3_turn_binding.sql` 只新增关联表；在第一个 Tool 前完成合法 offer 的原子 reserve/bind；本地 fixture 覆盖正常、无 offer、过期、重复、跨 workspace/session 的 bind，以及无回执 provisional Run 不可学习且按清理顺序删除。
- [!] [G0.5] MANUAL_GATE TurnToken Compatibility：按第 2 章 G0.5 规则在真实 Codex 完成验证；仅用户审阅 `docs/verification/G0.5-turn-token-compatibility.md` 后可标记 `[x]`。自动编码器不得选择、执行或自行完成此项。
- [-] [P3.2] BLOCKED_BY=G0.5 完整可学习轨迹：发布 Bundle 升级至 migration `5`，实现 PreToolUse/PostToolUse/Stop、sanitize outbox、单 writer、乱序配对、TraceEvent、Episode、finish/outcome、覆盖状态与可信 verdict。仅 G0.5 为 `[x]` 后改为 `[ ]`。完成条件：`0005_p3_trace.sql` 只新增完整轨迹表；完整 observed Episode 可幂等组装，缺失关联/投递失败绝不进入学习。
- [-] [P4] BLOCKED_BY=G0.5 反馈权重与冻结评估：发布 Bundle 升级至 migration `6`，实现权重更新、生成/验证集分离的固定回放、覆盖检查和 guardrail。仅 G0.5 为 `[x]` 后改为 `[ ]`。
- [-] [P5] BLOCKED_BY=G0.5 授权记忆：发布 Bundle 升级至 migration `7`，实现读/写独立授权、冻结读取、L1/L2、强化与 TTL、管理 CLI、sanitize/audit/断路器及 MemoryRecallResult。仅 G0.5 为 `[x]` 后改为 `[ ]`。
- [-] [P6] BLOCKED_BY=G0.5 受控演进：发布 Bundle 升级至 migration `8`，构建并验证独立 `skilltree-replay-bundle/v1` Extension Bundle，按显式安装/卸载契约管理离线 OCI runner；实现 ReplayCapsule/DPAPI、fixture-only `run_arm`、baseline/candidate 受控演进 Loop、ReplayReport、显式 scan、合法状态迁移和补丁候选。Loop 必须只由 `skilltree evolve scan` 显式启动并受 `max_episodes=200`、`max_iterations=2`、单 arm timeout 和 guardrail 中止约束；仅 G0.5 为 `[x]` 后改为 `[ ]`。
- [-] [P7] BLOCKED_BY=G0.5 发布与人工验证：实现 Plugin 打包、更新说明、运行时隔离、安全测试、开源治理文件/SBOM/兼容矩阵，并在 P0 hash 清单之上实现发布签名、验证公钥分发和密钥轮换治理；真实 Codex 发布验收另设人工 Gate。仅 G0.5 为 `[x]` 后改为 `[ ]`。

`auto-coder` 只可选择 `[~]` 或 `[ ]` 任务，且每次任务开始前必须检查其文本中的 `BLOCKED_BY`。`[!]` 与 `[-]` 不属于可执行任务；所有可执行任务完成后若下一项为 Gate 或 BLOCKED，自动编码器必须停止并报告所需的人工动作，不得自行改写 Gate、解锁下游任务、伪造验证证据或跳过依赖。

每个任务完成时必须补测试、更新本文件任务状态和对应阶段留痕目录、同步 auto-coder 参考文件，并在用户确认后创建原子提交。
