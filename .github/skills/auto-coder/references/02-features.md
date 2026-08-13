## 2. 功能规格

### F1 技能注册表

- 扫描唯一、经用户明确确认的 `skill_root` 下的 `SKILL.md`；`skill_root` 不得硬编码用户名或机器路径。
- 首期容量上限为 500 个已注册 Skill。`scan` 在读取 frontmatter 前按规范化真实路径计数受控根目录内的 `SKILL.md`；超过 500 时返回 `registry_capacity_exceeded`、不写注册表且保留原有注册状态，不得静默截断或只登记前 500 项。首期不支持多用户、团队共享、上万 Skill 的实时索引或文件监听。
- `skilltree registry setup` 只显示检测到的候选目录，用户以 `selected_root` 与精确确认词明确确认后才写入 `skill_root`。首次 setup 未确认或配置失效时，`scan` 返回 `authorization_required`，不得扫描任何目录。
- `setup` 的候选发现集合固定为：请求 JSON 内用户提供的 `provided_root`、已设置的 `CODEX_HOME/skills` 与当前用户主目录下 `.codex/skills`；仅检查这些目录是否存在并去重，不得读取 `SKILL.md`、frontmatter 或目录内容。只有选中并确认后的根目录可进入 scan。
- `skill_root` 必须是规范化绝对本地路径；首期拒绝相对路径、通配符、网络共享根目录和符号链接逃逸。每次 scan 前解析真实路径，并验证每个候选 `SKILL.md` 的真实路径仍在已确认根目录内。
- 首期“管理的 Skill”专指 `skill_root` 中的注册项；系统内置 Skill、Plugin cache/Marketplace Skill、其他 Plugin 自带 Skill 均不扫描。SkillTree 自带的 `skill-router` 仅作为入口，不进入注册表、Top-K、权重或演进候选。
- 修改根目录必须显式执行 `skilltree registry setup --input <absolute-json-file>` 并使用 `confirm:"SET_SKILL_ROOT"`；变更后旧注册项标为 `out_of_scope`，保留历史轨迹但不得进入 Top-K、权重或演进候选。
- 安全解析 frontmatter，保存 name、description、path、hash、更新时间、状态和诊断。仅安全解析成功、name 合法且 description 经 `sanitize` 后为 1–500 UTF-8 bytes 的非空字符串时才写入该 description；缺失、空白、超长、sanitize 拒绝或 frontmatter 无效均只写 `state=invalid` 与不含原文的诊断码，不得进入 Top-K。
- 损坏文件标为 `invalid`，不影响其他技能。
- 技能采用 `pending → trusted / blocked` 信任生命周期；新发现或内容哈希变化的技能保持 `pending`，在用户确认前不得进入模型 Top-K 或执行候选集。

### F2 Codex 路由入口

- Plugin 包含 `skill-router` Skill，支持自然语言自动触发和 `$skill-router` 显式触发。
- 显式调用其他 Skill 时不得拦截。
- `$skill-router` 是可复现的验收入口；自然语言自动触发仅作为真实 Codex 手动验证目标，不得作为 CI 的确定性断言。
- `skill-router` 不得读取、配置或猜测 `$PLUGIN_DATA`，也不得直接启动 Core、Doctor 或 SQLite。唯一运行时桥接是 Plugin `UserPromptSubmit` Hook：它在已安装专用 venv 中执行只读运行时检查、受约束候选准备和授权记忆的冻结读取，并仅以 Hook developer context 向当前回合提供冻结结果；Hook 不可用、超时、Doctor `failed` 或候选准备失败时必须零上下文 fail-open，路由器报告本地运行时不可用而不访问其他路径。该桥接不授予任何被推荐的用户 Skill 对 Core、SQLite 或 Plugin 数据目录的访问权。
- Core 提供 Top-K 可用候选；当前 Codex 模型按 JSON Schema 输出意图、约束、排序和理由。
- 模型只能从 Top-K 选择；无效输出安全降级。
- `skill-router` 在同一受控回合产生两个独立 Schema 对象：`RouteDecision` 和 `MemoryExtractionCandidate`。后者只能是候选，不能直接写入画像或程序化记忆；模型输出不合法时，路由降级且跳过记忆写入。

### F3 轨迹与 Episode

- 以 `run_id` 记录路由语义，以 `session_id + turn_id` 记录 Codex Hook 观测上下文，以 `tool_use_id` 记录一次受支持的实际 Tool 调用。`run_id` 仅由 Core 创建，Hook 不得自行伪造；Hook event 的 `event_id` 必须为以 `session_id + turn_id + tool_use_id_or_event_type + event_type` 生成的确定性 UUIDv5。
- Plugin 默认捆绑 `UserPromptSubmit`、`PreToolUse`、`PostToolUse` 与 `Stop` Hook。`trace_capture_enabled=true` 时，`UserPromptSubmit` 由 Core 在同一事务创建 TurnTrace；若本回合已有 RouteOffer，则同时创建 provisional RunContext 并以内部 `turn_token` 提前 bind。`turn_token` 只能由 Hook/Core 使用，绝不返回给模型；模型只取得 P2 的 `route_token`。Token 具有 `soft_expires_at=created_at+90s` 和 `hard_expires_at=created_at+5min`：soft 到期后仅在精确 token、同 workspace/session、TurnTrace 未关闭且未消费时允许内部 bind，并审计为 `late_bind`；hard 到期后一律 `correlation_missing`。没有 RouteOffer 或 reserve 失败的 Hook 事件保留为 `unattributed`，不得被猜测归属某个 run。
- P3 按风险分为 `P3.1 → G0.5 → P3.2`：P3.1 只交付最小 `UserPromptSubmit → TurnTrace → turn_token/session_id_hash → trace bind → RunContext` 链路及其 fixture；G0.5 在真实 Codex 中验证该关联；P3.2 才交付 Pre/Post/Stop、Tool 事件、Episode、outcome 和任何学习关联。G0.5 未通过时，项目只能运行注册表与 Top-K 路由，所有 Hook 事件为 `unattributed`，不得创建可学习 Episode、更新权重、写入程序化记忆或创建 ReplayCapsule。
- `PreToolUse` 记录受支持调用的开始；`PostToolUse` 是实际 Tool 输入/输出结果的主来源；`Stop` 关闭当前 TurnTrace，并以 `unknown` 建立初始 outcome；在 P2 中它还仅可按 `RouteEnvelope` 协议校验并提交 RouteDecision，不能把计划记为执行。路由器适配器只补充推荐、Skill 加载和用户可见工作流语义，不能把计划记为执行。
- 首期 `observed` 覆盖 Shell/`exec_command`、`apply_patch`、MCP 和大多数本地 function Tool；Hosted Tool（例如 WebSearch）、未触发 Hook 的专用路径及 Hook 未信任/未启用期间的调用为 `unobserved`。`write_stdin` 不是独立调用，后续 poll 可能交付原命令的 `PostToolUse`。覆盖状态为 `observed|partial|unobserved|unattributed`，其中后三者不得产生因果学习结论。
- Episode 保存目标、意图、实际 Skill/Tool 序列、结果、耗时和轨迹完整性。
- verdict 为 `success`、`failed`、`cancelled` 或 `unknown`；失败必须保留脱敏诊断。
- 定义 Hook 与路由器适配器的事件协议：入站 Hook event 必须包含 `event_id`、`turn_trace_id`、`event_type`、`source`、`observed_at`、`payload_hash`、脱敏 payload 摘要和可选可信证据引用。Hook 事件另含 `session_id`、`turn_id`、`tool_use_id` 和 `tool_name`；仅关联成功时才含 `run_id`。`event_id` 幂等；模型自述仅记为 `claimed_outcome`，不能决定 verdict。`ingest_sequence` 由 Core flusher 在单写入器事务中分配，仅表示确定性持久化顺序，不能推断不同 Tool 调用之间的因果顺序。
- `run_closed` 是每个 TurnTrace 唯一的技术终态，不能等同任务成功。`user_feedback`、`verifier_outcome` 或具有可验证证据的 `tool_adapter` 事件可在关闭后追加 outcome assessment；Core 以可信来源优先级计算 verdict。没有可信 assessment 时 verdict 必为 `unknown`。
- 只有已关闭、覆盖状态为 `observed`、完整快照和全部 Hook 事件持久化成功的关联 run 才可组装可学习 Episode；崩溃恢复、缺失关闭事件、Hook 未信任、事件投递失败或关联缺失均标为 `trace_incomplete`，不得参与学习或演进。
- 每个路由创建不可变 `RunContext`，至少含 `run_id`、`workspace_id`、`user_id`、开始时间、路由时的 trusted Skill 快照及其内容哈希；所有路由器适配器从该上下文取得关联键，不能自行猜测或重建。
- Hook 必须在持久化前同步执行 `sanitize`，不得把原始 `tool_input`、`tool_response`、用户 prompt 或 transcript 写入 SQLite。Hook 进程只将已脱敏、带 `tool_use_id` 的事件写入唯一随机文件 outbox；Core flush 后才写 SQLite。SQLite 不可用、锁冲突或 flush 失败时，outbox 文件保留并记录 `trace_flush_failed`，不阻塞 Codex 结果但使关联 run 不可演进。
- Hook 不得分配 `ingest_sequence` 或直接写 SQLite。每次投递先写 `$PLUGIN_DATA/outbox/staging/<random>.tmp`、fsync 后原子重命名到 `ready/<random>.json`；Core 按 `event_id` 与 `payload_hash` 去重：同 ID 同 hash 忽略， 同 ID 不同 hash 移至 `quarantine/`、写入 `event_collision` 审计并将 TurnTrace 标为 `incomplete`。解析失败或超重试上限移至 `failed/`。
- Core 通过带 PID、启动时间和租约的跨进程单写入锁消费 ready 文件；仅在租约到期且只读健康检查通过后接管崩溃 writer。对同一 `tool_use_id`，`tool_finished|tool_failed` 早于 `tool_started` 时先保留为 pending；Stop 到达后等待 `close_grace_ms=5000`，仍未配对、未消费或失败的事件使 Episode 为 `trace_incomplete`。持久化排序键固定为 `observed_at + phase_rank + event_id`，其中 `turn_started=0`、`tool_started=1`、`tool_finished|tool_failed=2`、`run_closed=3`、outcome assessment 为 `4`。
- Episode 由专用 assembler 以 `run_id` 幂等组装，并存储 `objective_hash`、已脱敏 objective preview、Skill 快照、`snapshot_partial`、事件数和 outcome 引用；重复组装返回既有 Episode，不复制轨迹。

### F4 学习与授权记忆

- 权重只由显式选择、拒绝、改选或可信结果更新。
- `trace_capture_enabled=false`、`memory_read_enabled=false` 与 `memory_write_enabled=false` 均为默认值且相互独立。轨迹开关关闭时不得创建 TurnTrace、outbox、Episode 或权重更新；读取/写入开关关闭时对应记忆路径不得降级为隐式启用。
- `replay_capture_enabled=false` 为默认值，且必须逐 run 由用户明确授权；它独立于 trace/memory 三个开关。没有该次 run 的授权时不得保存任何可 replay 输入、Skill 内容副本或 Tool fixture，Episode 只能用于非因果统计。
- 读取控制冻结 Profile/已批准程序化记忆能否注入，写入控制候选抽取和持久化。任一开关关闭时对应路径不得降级为隐式启用。
- `SkillWeight.weight` 为闭区间 `[-10, 10]` 内的整数，初始为 `0`：用户显式选中候选 `+2`，显式拒绝 `-2`，改选等于旧候选 `-2` 且新候选 `+2`；可信 `success` 对被选中且实际 observed 的 Skill `+1`，可信 `failed` 对有直接失败证据的实际调用 Skill `-1`；`cancelled|unknown|partial|unobserved|unattributed` 不改变权重。每 30 天将未更新权重向 0 收缩 1；每次变更记录 evidence handle、旧值、新值与规则版本，撤销相关 outcome 后必须可重算。
- 开启后提取可管理的 L1 Profile 字段和程序化记忆候选；敏感信息禁止持久化。
- 用户可查看、编辑、删除和清空画像。
- 管理 CLI 必须提供 `export`、逐 handle `delete`、`clear-profile`、`clear-workspace-data` 和 `status`；每个删除响应包含受影响 handle、保留/匿名化的审计数量与完成时间。撤销 `memory_read_enabled` 或 `memory_write_enabled` 必须在下一次读取/写入前立即生效，不能等待 TTL 或后台任务。
- Profile 默认作用域为本地用户全局；程序化记忆、SkillWeight、RouteRun、TraceEvent、Episode 和 ReplayCapsule 默认作用域为 `workspace_id`。每条可删除数据记录 `scope`、`created_at`、`retention_until` 和来源；`retention_until=NULL` 的唯一语义是“永久对象，不参与自动清理”，首期只允许 Profile 与 SkillWeight 使用，不能由外部调用方自行指定。维护 sweep 只选择 `retention_until IS NOT NULL AND retention_until <= now_utc` 的行；“清空画像”删除全局 Profile 及其全部 Profile 候选，“清空工作区数据”删除该工作区的轨迹、权重、ReplayCapsule blob/元数据、procedures 和全部 workspace 候选；审计仅保留对象 handle hash。
- 写入管线固定为：规则初筛 → 可选模型 write gate → Schema 校验 → `sanitize` → 作用域/授权校验 → 字段级 upsert 或候选保存 → 审计。任何阶段失败都不能阻塞路由；被拒绝、脱敏或同值 no-op 的结果同样写入审计。
- `memory_candidates` 只承载待审批正文：创建时固定 `status=pending`、`expires_at=created_at+7 days`，期间用户可 `approve|reject`。批准在一个事务中把候选转为已批准的 L1/L2 结果；拒绝、到期或用户清空候选时必须物理删除候选行及其 `payload_json`，不得保留候选正文、payload hash 或可恢复副本，只保留 30 天的脱敏 `candidate_rejected|candidate_expired|candidate_deleted_by_user` 审计。以后再次提取到相同内容时创建新的候选，绝不自动复活已拒绝或已过期的候选；只有新候选被批准才进入 L1/L2 的去重、强化与 TTL 管线。
- 每个实际写入项必须返回可寻址 handle（例如 Profile `namespace.key` 或 `memory_candidate_id`），供用户查看、修正和删除；不得只返回“写入 N 条”的不可验证汇总。
- 请求开始时仅在 `memory_read_enabled` 下以预算读取 Profile 和已批准记忆并冻结。当前 run 新产生的字段或候选不得改变本轮模型上下文。
- L1 只保存 identity 与 preference 的稳定字段；路由偏好以 `preference` 字段表达，不另设可绕过用户管理的 routing 命名空间。L2 只保存 procedural knowledge（“在何种条件下，如何完成工作”）。一次性事实、原始对话、原始 Tool 参数/输出、外部实体关系均不得写入 L2，也不得从 L2 编译进 `SKILL.md`。
- P5 的候选、L1 与 L2 都以本地 SQLite 为权威存储；不得为画像或程序化记忆隐式引入 Mem0、Milvus、云端数据库或外部向量库。实现可借鉴 HugAgentOS 的分层、字段级脱敏、近重复强化与 TTL 语义，但运行时依赖和删除语义必须由本项目的迁移与 CLI 独立保证。
- Profile 每个字段须独立 `sanitize`；敏感命中时拒绝写入原文，仅记 `write_rejected` 的原因码和对象 hash 审计。相同 key/value 为 `no_op`；超过 `profile_max_chars=1500` 时可异步调用低温模型压缩，压缩、校验或写回任一步失败均保留原字段集。Profile 仅可由用户逐 handle 删除或 `clear-profile` 物理删除；不使用 TTL 隐藏。
- L2 规则先以规范化 `rule_hash` 精确去重，再以确定性 token-shingle 指纹做同一 `(workspace_id,user_id,applies_to)` 内近重复候选匹配；匹配成功只强化原条目，不能新增近重复行。LLM 只能输出 `[0,1]` 的 `importance_prior` 初始先验，不能直接决定最终 `strength` 或 TTL；Core 在事务内根据强化次数与最近强化时间计算分数并执行滞回状态机。统一 TTL 为 `weak=30` 天、`strong=365` 天；到期任务只将可可靠判定到期的 `active` 行转为 `hidden`，后续 sweeper 才物理删除。`hidden` 条目不参与读取或模型注入，但保留 30 天供用户查看和审计；该期间若同一已批准候选精确或近重复匹配，必须原子复用并 `hidden → active`，清空 `hidden_at`、按当前有效强化重新计算分数/状态/过期时间，禁止新增重复行。缺少或无法解析到期时间时不得猜测删除。
- write gate 只能缩小已通过规则初筛的候选类别；gate 超时、模型不可用或输出不可解析时，保留规则候选继续走 Schema、sanitize、授权和审计，不能把“gate 没结果”误判为“无需记忆”。
- L2 近重复命中时只强化原条目：有效强化事件使 `reinforcement_count += 1`、`seen_count` 同步增加、更新 `last_reinforced_at`，并按状态映射重新计算 `expires_at`；失败、取消、未知结果和仅检索不计为强化。分数公式固定为：`usage_score = 1 - exp(-reinforcement_count / 3)`，`recency_score = exp(-days_since_last_reinforced * ln(2) / 70)`，`score = 100 * (0.40 * importance_prior + 0.35 * usage_score + 0.25 * recency_score)`。初次创建使用 `reinforcement_count=0`、`last_reinforced_at=created_at`。`weak → strong` 仅在 `score >= 70` 且 `reinforcement_count >= 2` 时发生；`strong → weak` 仅在一次 sweep 计算 `score < 35` 且 `low_score_sweeps` 达到 2 次时发生，低于阈值的单次 sweep 只递增计数，任何达到强阈值的强化立即清零该计数。最终状态决定 TTL：`weak=30` 天、`strong=365` 天；每次创建或有效强化都将 `expires_at` 重置为该次 `now_utc + timedelta(days=ttl_for_current_strength)`，不得在旧 `expires_at` 上累加；状态变更和到期重算必须幂等。过期条目立即设为 `hidden`，同时设 `hidden_at=now_utc`、`retention_until=hidden_at+30 days`，且不参与读取；超过 `retention_until` 后才可被物理删除。清理失败不重新暴露 hidden 条目。

### F5 受控演进

- 聚合成功完整序列、顺序约束和重复失败，产生带证据的候选。
- 文档补丁候选包含基线哈希、回放集、风险和回滚方案。
- 首期只实现 `draft → replay_passed`；用户批准前不得修改 `SKILL.md`。
- 候选生成集与回放验证集必须按 Episode 划分且不可重叠；回放保存不可变 `dataset_snapshot`、基线/候选指标、最小样本量、效果阈值和失败/安全回归 guardrail。
- 只有独立验证集达到覆盖要求（成功样本、失败恢复和负例）且无 guardrail breach 时，候选才能从 `draft` 迁移到 `replay_passed`；证据不足时保持 `draft`，不把中性结果解释为通过。
- 所有候选状态迁移经唯一的合法迁移检查；回放路径不具备进入 `shadow`、`canary` 或 `active` 的权限。
- 仅 `snapshot_partial=false`、具有完整 trusted Skill 内容快照、非历史回填且关联 `ReplayCapsule.status=ready` 的 Episode 可进入反事实 replay；不完整、未授权、无 Capsule 或 Capsule 被拒绝的 Episode 仍可用于模式统计与失败归因，但不能用于声称某个 `SKILL.md` 补丁带来因果改善。
- 回放执行器以本规格定义的 `run_arm` request/result 契约注入：它负责冻结的输入、技能快照和受控运行环境；演进模块只比较基线/候选报告并持久化结果，不拥有执行或发布权限。
- SkillTree 只实现受控演进评估 Loop，不实现或接管 Codex 的主 Agent Loop。该 Loop 仅由用户显式执行 `skilltree evolve scan` 启动：为同一不可变 `dataset_snapshot` 顺序运行 baseline arm 与 candidate arm，收集每个 arm 的 `run_arm` 结果，聚合为 `ReplayReport`，执行 coverage 与 guardrail 判定，再将候选保持为 `draft` 或迁移到 `replay_passed`。Loop 不自动执行正式 Skill、不修改 `SKILL.md`、不推进 `shadow|canary|active`，且不因模型建议自行重试外部 Tool。
- Loop 的单次执行上限固定为 `max_episodes=200`、`max_iterations=2`（baseline/candidate 各一轮）、每 arm `timeout_ms` 受 `run_arm` 的 `[1000,300000]` 限制；任一 guardrail breach、输入 hash 变化、workspace 不一致、Capsule 非 `ready` 或结果 Schema 无效立即停止剩余 arm，生成 `verdict=insufficient` 或 `regressed` 的报告并记录失败 reason code。Loop 本身没有发布权限，人工审核是唯一进入后续补丁流程的授权边界。

### F6 安全

- 所有扫描文本和模型输出均视为不可信数据。
- 模型上下文只包含 `trusted` 技能的最小化元数据；`pending`、`blocked` 或哈希已变化的技能不得参与候选、重排或记忆抽取。
- 防止提示注入、路径逃逸、未授权记忆、敏感信息写入和未批准补丁。
- Plugin-bundled Hook 是本地命令代码：安装、启用或内容哈希变化后必须由用户在 Codex `/hooks` 审阅并信任；未信任、被策略禁用或超时的 Hook 一律视为不可观测，Core 不得以历史或模型信息补造事件。
- Hook 输入中的 `prompt`、`transcript_path`、`tool_input` 与 `tool_response` 均为不可信且可能含敏感数据。仅允许将 `sanitize` 后的摘要、哈希、长度、类型、允许的错误代码和本地 artifact handle 进入 outbox/SQLite；`transcript_path` 仅用于 Codex，不得读取或持久化。
- Hook 命令只允许调用位于 `$PLUGIN_ROOT` 的只读程序，并只写入 `$PLUGIN_DATA` 下的 outbox、SQLite、审计和受限 venv；manifest、Hook 配置和补丁路径必须规范化且保持在 Plugin 根目录内。Hook 不得自动执行 Skill、网络请求、外部命令、`SKILL.md` 补丁或记忆写入。
- ReplayCapsule 的原始回放内容只可在单次明确授权后写入 `$PLUGIN_DATA/replay-blobs/` 的加密 blob；SQLite 只保存元数据、内容哈希、授权记录和 blob handle。首期 `SecretProtector` 使用 Windows DPAPI，密钥材料不得进入 SQLite、日志、outbox、环境变量或工作区。密钥/Token/密码、不可脱敏个人信息、Hosted Tool 交互、不可模拟外部副作用或清洗失败时必须拒绝 Capsule，绝不以部分原文降级保存。
- `run_arm` 必须在独立子进程和临时工作目录执行：默认禁网，最小化环境变量并剥离凭据，输入与 trusted Skill 快照只读，输出只可写入本次 artifact 目录；设置 CPU、内存、进程数、磁盘和 wall-clock 限额。超时、越界写入、网络尝试、未允许命令或清洗失败均返回 `unknown` 并产生 `guardrail_breached`，不得计作改进。
- 审计状态变更及关联哈希；审计只存 reason code、对象 handle、时间与策略版本，不存被拒绝的原文。
- `sanitize/v1` 在 TraceEvent 摘要、Profile/程序化记忆写入、Episode preview、回放证据和补丁候选五个边界一致执行；首期仅使用发行包内版本化规则集，不支持数据库动态规则、用户自定义正则、网络规则更新或由模型修改规则。它返回 `clean|redacted|rejected`：`redacted` 以 `[REDACTED:<rule_name>]` 替换后可继续写入，`rejected` 禁止持久化原文、替换文本、命中值或可恢复副本，只保留 reason code；常见 API Key/Bearer Token、私钥块、密码赋值、连接串归为 `rejected`，手机号、邮箱、身份证号、银行卡号归为 `redacted`。记忆写入还受本地断路器/失败冷却保护，避免连续故障重复写入。

### F7 版本化数据与命令契约

所有持久化对象及 CLI JSON 输入/输出使用 `schema_version: "skilltree/v1"`。未知字段拒绝；所有字符串 UTF-8 编码；字段长度在校验前计数。CLI 成功与失败统一返回：

```json
{
  "schema_version": "skilltree/v1",
  "ok": true,
  "data": {},
  "error": null
}
```

失败时 `ok=false`、`data=null`，`error` 为 `{ "code": "...", "message": "...", "retryable": false }`。错误码首期固定为：`invalid_schema`、`untrusted_skill`、`out_of_scope`、`not_found`、`conflict`、`authorization_required`、`registry_capacity_exceeded`、`invalid_bootstrap_request`、`route_context_unavailable`、`route_token_invalid`、`route_token_expired`、`route_decision_missing`、`hook_unconfirmed`、`hook_unavailable`、`correlation_missing`、`trace_incomplete`、`snapshot_incomplete`、`memory_write_degraded`、`replay_authorization_required`、`replay_capsule_rejected`、`replay_capsule_missing`、`replay_runtime_unavailable`、`guardrail_breached`、`internal_error`。`memory_write_degraded` 的 `retryable=true`，其余错误除特别说明外为 `false`。所有命令请求对象必须明示 `required`、可省略字段和 `null` 语义；示例中标为 `optional` 的字段可省略，但出现时不得为 `null`，除非该字段的 Schema 明确写为 `null` 可接受。

#### 记忆写入熔断器

每个 `workspace_id` 有一条持久化的 `memory_write_breakers` 状态，固定策略版本 `memory-breaker/v1`：`state` 枚举 `closed|open|half_open`，连续基础设施失败阈值为 3，`open_until=now_utc+60 seconds`。只将 `sqlite_busy`、`sqlite_io`、`disk_full`、`migration_error` 计为失败；`sanitize` 拒绝、Schema/作用域/授权校验失败、用户 reject、候选过期、`no_op`、确定性去重、模型或 write gate 异常均不得计入。`closed` 下第三次连续失败原子转 `open`；`open` 期间跳过候选持久化和所有 L1/L2 写入，返回 `memory_write_degraded`，但绝不阻断路由、Trace/outbox 或已批准记忆读取。`open_until` 到期后首次真实写入原子转 `half_open`，同 workspace 同时只允许一个试探写入：成功即 `closed` 并清零失败次数，失败即重新 `open` 60 秒；其它并发写入返回 `memory_write_degraded`。任一成功持久化写入都将 closed 状态的连续失败数清零。状态变更仅尽力写 `memory_breaker_opened|memory_breaker_recovered` 审计，审计失败不得递归计作 breaker 失败。

#### 记忆管理 CLI

首期记忆管理命令固定为 `skilltree memory candidate-list|approve|reject|list|delete|export --input <absolute-json-file>` 及 `skilltree clear-profile|clear-workspace-data --input <absolute-json-file>`。`--input` 是唯一请求载体，拒绝 stdin、命令行 JSON、相对路径、额外位置参数和交互式确认；输入文件须为 UTF-8、至多 16 KiB 的单个 JSON object。所有请求必填 `schema_version:"skilltree/v1"` 和 `user_id:"local"`，`user_id` 不接受其他值或 `null`；涉及 workspace 的请求必填 `workspace_id:"sha256:<64 hex>"`。所有响应复用本章 envelope，输出不得包含绝对路径、审计原文、TraceEvent、outbox、Replay blob 或 SecretProtector 内容。

```json
// skilltree memory candidate-list --input <file>
{"schema_version":"skilltree/v1","user_id":"local","workspace_id":"sha256:<64 hex>"}
// data: {"candidates":[{"candidate_id":"UUID","layer":"profile|procedure","kind":"identity|preference|procedure","payload":{},"created_at":"RFC3339 UTC","expires_at":"RFC3339 UTC"}],"count":0}

// skilltree memory approve|reject --input <file>
{"schema_version":"skilltree/v1","user_id":"local","workspace_id":"sha256:<64 hex>","candidate_id":"RFC4122 UUID"}
// approve data: {"candidate_id":"UUID","write_results":[{"layer":"L1|L2","handle":"...","action":"write|update|created|reinforced|no_op","reason_code":"optional"}],"completed_at":"RFC3339 UTC"}
// reject data: {"deleted_handles":["candidate:UUID"],"audit_retained_count":1,"completed_at":"RFC3339 UTC"}

// skilltree memory list --input <file>
{"schema_version":"skilltree/v1","user_id":"local","layer":"L1|L2","workspace_id":"sha256:<64 hex optional only when layer=L2","include_hidden":false}
// data: {"layer":"L1|L2","items":[],"count":0}

// skilltree memory delete --input <file>
{"schema_version":"skilltree/v1","user_id":"local","layer":"L1|L2","handle":"namespace.key|RFC4122 UUID","workspace_id":"sha256:<64 hex required only when layer=L2"}
// skilltree memory export --input <file>
{"schema_version":"skilltree/v1","user_id":"local","workspace_id":"sha256:<64 hex"}
// data: {"profile_fields":[],"active_procedures":[],"pending_candidates":[],"exported_at":"RFC3339 UTC"}

// skilltree clear-profile --input <file>
{"schema_version":"skilltree/v1","user_id":"local","confirm":"DELETE_PROFILE"}
// skilltree clear-workspace-data --input <file>
{"schema_version":"skilltree/v1","user_id":"local","workspace_id":"sha256:<64 hex","confirm":"DELETE_WORKSPACE_DATA"}
// clear/delete data: {"deleted_handles":[],"audit_retained_count":0,"completed_at":"RFC3339 UTC"}
```

`candidate-list` 只返回 pending 候选，最多 50 条，固定按 `created_at ASC,candidate_id ASC`，不分页；`approve|reject` 只接受一个 candidate，不支持批量。`memory list` 必须显式 layer，L1 不接受 `workspace_id` 或 `include_hidden`，L2 默认仅列 active，只有 `include_hidden:true` 才同时列 active/hidden；返回项仅含该层可见字段与 handle，不含 source Run、score 内部中间量或绝对路径。`memory delete` 必须精确匹配单个 handle，禁止模糊匹配；L1 delete 仅删除一个 `namespace.key`，L2 delete 立即物理删除一个 procedure。`export` 只读且固定导出全部 L1、指定 workspace active L2 和 pending 候选；不导出 hidden L2、审计、轨迹、outbox、ReplayCapsule blob 或绝对路径。`clear-profile` 与 `clear-workspace-data` 只有 `confirm` 精确匹配时才执行；确认失败返回 `authorization_required` 且不得修改数据。每个 delete/clear 响应必须如示例返回已影响 handle、保留审计数与完成时间；`not_found`、`out_of_scope`、`authorization_required`、`conflict` 或 `invalid_schema` 时不得产生部分删除。

#### Replay Extension CLI

P6 扩展管理命令固定为 `skilltree replay install-extension|uninstall-extension --input <absolute-json-file>`。它复用 F7 envelope 和同一 `--input` 文件限制：仅接受单个 UTF-8 JSON object、至多 16 KiB、未知字段/重复 JSON key/`null`/额外 CLI 参数一律 `invalid_schema`，不得接受 stdin、交互确认或命令行 JSON。两个请求都必填且仅允许 `schema_version:"skilltree/v1"`、`user_id:"local"` 和下列专属字段：

```json
// skilltree replay install-extension --input <file>
{
  "schema_version": "skilltree/v1",
  "user_id": "local",
  "extension_root": "normalized absolute local path",
  "confirm": "INSTALL_REPLAY_EXTENSION"
}
// data: {"extension_version":"...","extension_bundle_hash":"sha256:<64 hex>","image_digest":"sha256:<64 hex>","completed_at":"RFC3339 UTC"}

// skilltree replay uninstall-extension --input <file>
{
  "schema_version": "skilltree/v1",
  "user_id": "local",
  "confirm": "UNINSTALL_REPLAY_EXTENSION"
}
// data: {"removed_image_digest":"sha256:<64 hex>","completed_at":"RFC3339 UTC"}
```

`extension_root` 只在 install 时必填且必须是用户提供的、已存在的规范化绝对本地目录；它不得为网络共享、符号链接逃逸路径、工作区、Plugin 根、`$PLUGIN_DATA` 或它们的父/子目录。install 在任何 Docker/load 动作前完整校验 Extension Bundle 的结构、manifest、兼容范围与 archive hash；确认不匹配返回 `authorization_required`，非法/越界路径或结构返回 `out_of_scope`，Schema/字段错误返回 `invalid_schema`，Docker 不可用、离线 load 失败或 image digest 不符返回 `replay_runtime_unavailable`。所有失败均不得创建/替换 replay runtime state 或删除旧扩展。相同 bundle 已安装时返回成功且不重新 load；不同 bundle 仅在原子更新成功后替换旧 state。uninstall 只删除 replay runtime state 和该 state 指向的本地镜像引用，不删除 Capsule、Episode、Report 或基础 Plugin 数据；无已安装扩展返回 `not_found`，删除镜像或 state 失败返回 `internal_error` 并保留 state，防止状态与实际镜像不一致。响应绝不返回 `extension_root` 或 Docker 可执行路径。

#### RuntimeConfig

所有业务 RuntimeConfig 仅存于 SQLite 的单行 `runtime_config(config_id=1)`，不再创建或读取 `$PLUGIN_DATA/config.json`。其中 `skill_root` 是受控内部字段：不进入模型上下文、TraceEvent、导出、status 或外部审计；只有 Core 的路径验证和 scan 可读取。配置对象为：

```json
{
  "schema_version": "skilltree/v1",
  "config_version": 1,
  "skill_root": "normalized absolute local path or null before first setup",
  "skill_root_hash": "sha256:<64 hex> or null before first setup",
  "trace_capture_enabled": false,
  "memory_read_enabled": false,
  "memory_write_enabled": false,
  "replay_capture_enabled": false,
  "updated_at": "RFC3339 UTC"
}
```

`status`、`export` 与审计只能输出 `skill_root_hash` 和 `config_version`，不得输出绝对路径。`setup` 必须在单一 SQLite 事务内先验证路径、将旧 in-scope Skill 标为 `out_of_scope`、更新 runtime_config 的 root/hash/version/time，并写入脱敏 audit；任一步失败回滚全部数据库变更。P0 初始 migration 创建该单行，固定四个授权开关为 `false`、root/hash 为 `NULL`；失败时保留原行。基础 runtime state、Replay Extension state 和 RuntimeConfig 属于不同文件/表，任何一方不得作为另一方的回退来源。

#### RuntimeConfig 授权 CLI

四个授权开关是用户可撤销的全局开关，而不是 Hook、模型或某个 Skill 可以自行改变的偏好。命令固定为 `skilltree config status|set-consent --input <absolute-json-file>`。它复用 F7 envelope 和 JSON 文件限制：只接受一个 UTF-8 JSON object、至多 16 KiB、绝对输入文件路径；未知字段、重复 JSON key、`null`、额外 CLI 参数、stdin、交互确认或命令行 JSON 一律返回 `invalid_schema`。请求中的 `user_id` 必须恒为 `local`。`status` 是只读操作，`set-consent` 是唯一能改写四个开关的生产接口；不得提供单独的 `--enable`、环境变量、配置文件或 Hook 内部回退写入路径。

```json
// skilltree config status --input <file>
{"schema_version":"skilltree/v1","user_id":"local"}
// data: {"config_version":1,"skill_root_hash":"sha256:<64 hex>|null","consents":{"trace_capture_enabled":false,"memory_read_enabled":false,"memory_write_enabled":false,"replay_capture_enabled":false},"updated_at":"RFC3339 UTC"}

// skilltree config set-consent --input <file>
{
  "schema_version":"skilltree/v1",
  "user_id":"local",
  "expected_config_version":1,
  "consents":{
    "trace_capture_enabled":true,
    "memory_read_enabled":false,
    "memory_write_enabled":false,
    "replay_capture_enabled":false
  },
  "confirm":"SET_RUNTIME_CONSENT"
}
// data: {"config_version":2,"consents":{"trace_capture_enabled":true,"memory_read_enabled":false,"memory_write_enabled":false,"replay_capture_enabled":false},"changed_keys":["trace_capture_enabled"],"completed_at":"RFC3339 UTC"}
```

`consents` 必须恰含四个上述布尔字段，不能省略、额外添加或设为 `null`；这让每次提交都同时表达完整期望状态，而不是由调用方猜测旧值。`expected_config_version` 必须是当前正整数版本，`confirm` 必须精确等于 `SET_RUNTIME_CONSENT`。Core 在一个 `BEGIN IMMEDIATE` SQLite 事务中按 `config_id=1 AND config_version=expected_config_version` 比较并更新；版本不匹配返回 `conflict`，确认词不匹配返回 `authorization_required`，两者都不得产生任何写入。有效状态变化时，Core 原子写入四个开关、`config_version += 1`、`updated_at` 和每个变化字段一条不含路径、prompt、候选正文或原始输入的 `runtime_consent_changed` 审计（`object_handle_hash=sha256("runtime_config/<field>")`，`reason_code=enabled|disabled`，策略版本 `runtime-consent/v1`）。完全相同的提交在版本匹配时成功且不递增版本、不写审计，返回空 `changed_keys`；该幂等结果仍返回当前完整 `consents`。

关闭任一开关在事务提交后立即生效：后续 Hook/CLI 操作必须在各自开始时重新读取 RuntimeConfig，正在进行的读取还必须在冻结块提交前复查；它们不得以旧快照完成新的写入或注入。关闭 `trace_capture_enabled` 不删除既有轨迹，但从下一回合起不再创建 TurnTrace、outbox、Episode 或权重更新；关闭 `memory_read_enabled` 或 `memory_write_enabled` 不删除既有 L1/L2 数据，分别禁止下一次读取注入或候选/写入；关闭 `replay_capture_enabled` 禁止新的 Capsule 捕获，但不撤销已有逐 run consent 或提前删除既有 Capsule。重新开启只影响未来操作，绝不补采集、补写或补建历史对象；即使全局 replay 开关开启，每个 Capsule 仍必须取得该 run 的一次性明确授权。

#### Registry CLI

P1 注册表命令固定为 `skilltree registry setup|scan|trust|block|status --input <absolute-json-file>`；废弃 `skilltree setup --path` 与 `skilltree config set-skill-root --path`，不得保留兼容别名。所有五个命令复用 F7 envelope 和 JSON 文件限制：仅接受单个 UTF-8 JSON object、至多 16 KiB、未知字段/重复 JSON key/`null`/额外 CLI 参数一律 `invalid_schema`，不得接受 stdin、交互确认、命令行 JSON 或相对输入文件路径；每个请求必填且仅允许 `schema_version:"skilltree/v1"` 与 `user_id:"local"` 加下列专属字段。

```json
// skilltree registry setup --input <file>
{"schema_version":"skilltree/v1","user_id":"local","provided_root":"absolute local path or omitted","selected_root":"absolute local path","confirm":"SET_SKILL_ROOT"}
// data: {"candidate_root_hashes":["sha256:<64 hex>"],"skill_root_hash":"sha256:<64 hex>","config_version":2,"completed_at":"RFC3339 UTC"}

// skilltree registry scan --input <file>
{"schema_version":"skilltree/v1","user_id":"local"}
// data: {"scanned_count":0,"pending_count":0,"invalid_count":0,"out_of_scope_count":0,"completed_at":"RFC3339 UTC"}

// skilltree registry trust|block --input <file>
{"schema_version":"skilltree/v1","user_id":"local","name":"lowercase kebab-case","content_hash":"sha256:<64 hex>"}
// data: {"name":"...","content_hash":"sha256:<64 hex>","state":"trusted|blocked","completed_at":"RFC3339 UTC"}

// skilltree registry status --input <file>
{"schema_version":"skilltree/v1","user_id":"local"}
// data: {"skill_root_hash":"sha256:<64 hex>|null","config_version":1,"skills":[{"name":"...","description":"...","content_hash":"sha256:<64 hex>","state":"pending|trusted|blocked|invalid|out_of_scope","diagnostic_code":"string|null","updated_at":"RFC3339 UTC"}],"count":0}
```

`provided_root` 仅 setup 可省略；出现时和 `selected_root` 均必须为规范化绝对本地目录，且不得为网络共享、Plugin 根、`$PLUGIN_DATA`、工作区或这些路径的父/子目录，解析真实路径后不得经符号链接越界。setup 候选仅为 `provided_root`（若有）、`CODEX_HOME/skills`（若有）和当前用户 `.codex/skills`（若存在）的去重集合；响应只含其 hash，不含路径。`selected_root` 必须精确等于该候选集合之一且 `confirm` 必须精确等于 `SET_SKILL_ROOT`，否则分别返回 `out_of_scope|authorization_required`，不写 RuntimeConfig 或注册表。setup 成功时按 RuntimeConfig 的单一 SQLite 事务将旧 in-scope Skill 标为 `out_of_scope` 并更新 root/hash/version；该调用不扫描 `SKILL.md`，用户必须随后单独调用 scan。

scan 没有 `skill_root` 时返回 `authorization_required`；开始前先按真实路径枚举候选 `SKILL.md`，超过 500 返回 `registry_capacity_exceeded` 且不改动任何注册行。否则单事务应用完整扫描结果：新增和 content hash 变化的合法条目写为 `pending`，保留未变的 `trusted|blocked`，无效条目写为 `invalid`；原根内本次未再发现的条目标为 `out_of_scope`。scan 不得改变任何 pending/trusted/blocked 条目的 content hash 或信任状态之外的历史证据。trust/block 仅可作用于当前 `pending` 且 `(name,content_hash)` 精确匹配的条目；名称不存在返回 `not_found`、hash 不匹配或非 pending 返回 `conflict`、invalid/out_of_scope 返回 `out_of_scope`，并且不产生部分写入。status 固定按 `name ASC` 返回最多 500 条，只返回列出的安全字段；未配置根目录时 `skill_root_hash=null`、skills 为空且命令仍成功，不泄露候选目录。

#### RunContext

路由开始时由 Core 创建，调用方不得自造或修改：

```json
{
  "schema_version": "skilltree/v1",
  "run_id": "RFC4122 UUID",
  "workspace_id": "sha256:<64 hex>",
  "user_id": "local",
  "started_at": "RFC3339 UTC",
  "trusted_skill_snapshot": [
    {"name": "analyze", "content_hash": "sha256:<64 hex>", "path": "absolute path"}
  ],
  "trace_capture_enabled": false,
  "memory_read_enabled": false,
  "memory_write_enabled": false,
  "replay_capture_enabled": false,
  "retention_until": "RFC3339 UTC"
}
```

`trusted_skill_snapshot` 至多 200 条；路径只用于本地校验，不得进入模型上下文或外部审计输出。

#### TurnTrace 与关联令牌

`UserPromptSubmit` Hook 在 `trace_capture_enabled=true` 时创建下列对象；它对用户 prompt 只保存 `prompt_hash` 与不超过 500 字符的 `sanitize` 后 preview，永不保存原文。`turn_token` 是一次性、随机、不可从 session/turn ID 推导的关联令牌；它可作为 Hook 返回的额外 developer context 出现，但不得写入模型长期记忆、日志或外部审计。

```json
{
  "schema_version": "skilltree/v1",
  "turn_trace_id": "RFC4122 UUID",
  "turn_token": "base64url random string, 32 characters",
  "session_id": "opaque string, at most 128 characters",
  "session_id_hash": "sha256:<64 hex>",
  "turn_id": "opaque string, at most 128 characters",
  "workspace_id": "sha256:<64 hex>",
  "prompt_hash": "sha256:<64 hex>",
  "soft_expires_at": "RFC3339 UTC",
  "hard_expires_at": "RFC3339 UTC",
  "consumed_at": "RFC3339 UTC or null",
  "coverage_state": "observed",
  "created_at": "RFC3339 UTC"
}
```

`turn_token` 与 `session_id_hash` 不再进入 developer context、模型输出或 `skill-router`；它们只在同一 UserPrompt Hook 的 Core 内部事务中使用。`trace-reserve` 是 Hook 专用 Core 操作，不暴露为普通 Skill 或用户 CLI：它在创建 TurnTrace 后，若当前未过期 RouteOffer 存在，则以新建 provisional RunContext 的 `run_id`、内部 `turn_token` 和 `session_id_hash` 执行 compare-and-consume，并把该 `run_id` 写入 `route_offers.provisional_run_id` 与 `run_turn_bindings`。同一事务必须验证 workspace/session 一致、TurnTrace 未关闭且尚未绑定；令牌只能使用一次。不存在 RouteOffer 时仅保留未关联 TurnTrace；不存在 token、hard 到期、跨 workspace/session、TurnTrace 已关闭或已绑定时返回 `correlation_missing|conflict`，不得创建或绑定 provisional Run。Token 原文永不持久化，过期清理只保留不含 Token 的匿名审计。reserve/bind 失败时，路由仍可工作，但该回合 Hook 事件保持 `unattributed`，不得学习或 replay。

#### RouteDecision

模型只接收 Core 输出的 trusted Top-K 元数据，并返回下列对象。`ranked_candidates[].name`、`selected_skill_name` 和 `ordered_skill_names` 的每个成员必须来自输入 Top-K，候选不得重复；`ordered_skill_names` 的首项必须等于 `selected_skill_name`。Top-K 最多 8，排序结果最多 3，理由最长 500 字符。

```json
{
  "schema_version": "skilltree/v1",
  "intent": {"name": "repository_analysis", "confidence": 0.0},
  "constraints": ["read_only", "chinese"],
  "ranked_candidates": [
    {"name": "analyze", "rank": 1, "reason": "..."}
  ],
  "selected_skill_name": "analyze",
  "ordered_skill_names": ["analyze", "lsp"],
  "degraded": false
}
```

`intent.name` 使用 `[a-z][a-z0-9_]{0,63}`，`confidence` 在 `[0,1]`；`constraints` 至多 12 项、每项最长 64 字符。Schema、候选归属或顺序校验失败时，Core 不采用模型排序，写入 `model_output_invalid` 事件并返回本地候选排序。

#### RouteEnvelope 与 Hook Context Bridge

每个非 bootstrap 的 `UserPromptSubmit` 仅在 Doctor 返回 `ready|degraded` 时调用 Core 的确定性 `route-prepare`。该调用在内存中使用 prompt 做候选召回；prompt 原文、原文派生摘要、token 原文和模型输出不得进入 SQLite、outbox、审计或日志。Core 生成 256-bit 随机 `route_token`，只在当前回合的 developer context 出现一次；SQLite 仅保存其 SHA-256、session/workspace hash、完整 trusted Skill 快照、候选快照、时间和状态。`route_token` 独立于内部 `turn_token`，不更新权重且不表示任何 Tool 已执行；P2 中它不创建 RunContext，P3.1 且 `trace_capture_enabled=true` 时由同一 UserPrompt 事务为其创建 provisional RunContext 并提前 bind。

注入给模型的 `RouteEnvelope` 固定为单个 JSON 对象，不含 prompt、路径、凭据、记忆内容、PluginData 或 session 原文：

```json
{
  "schema_version": "skilltree-route-envelope/v1",
  "route_token": "base64url-256-bit-token",
  "expires_at": "RFC3339 UTC",
  "candidate_snapshot_hash": "sha256:<64 hex>",
  "candidates": [
    {"name": "analyze", "description": "sanitized trusted description", "content_hash": "sha256:<64 hex>"}
  ]
}
```

`candidates` 最多 8 项，按本地确定性分数和 name 排序；`description` 必须为 trusted frontmatter description 经 `sanitize` 后的非空字符串，最长 500 字符。Hook 在 Doctor `failed`、Core 超时、候选为空、Schema/hash 校验失败或 venv 不可用时不注入该对象、不写 `route_offers`，并以零输出 fail-open；`hook_observation_state=unknown` 不阻止该只读桥接。RouteEnvelope 只为当前模型回合提供候选，`skill-router` 未触发时它不得导致 RouteRun、RouteDecision、TraceEvent、权重或记忆写入；RouteOffer 的 hard expiry 固定为 `prepared_at + 5 minutes`，Stop 缺失合法回执时立即删除，只有 Hook 崩溃或未抵达 Stop 才由 expiry sweeper 删除。

当且仅当 `skill-router` 实际触发时，模型在最终助手消息末尾输出且只输出一次下列独占 HTML 注释；正常用户可见答复不得依赖该注释内容：

```html
<!-- skilltree-route-decision:{"schema_version":"skilltree-route-commit/v1","route_token":"base64url-256-bit-token","decision":{...RouteDecision...}} -->
```

注释 UTF-8 编码不得超过 4096 bytes，必须位于最终非空行且不得有其他 `skilltree-route-decision:` 标记。`Stop` Hook 只在内存中从 `last_assistant_message` 提取该末行注释；缺失、多条、解析失败、超长、超时或不合法时不阻塞答复，仅记录无原文的 `route_decision_missing|model_output_invalid|route_token_expired` 审计。`route-commit` 在单一 SQLite 事务中验证 token hash、session/workspace hash、5 分钟有效期、未消费状态、候选快照 hash 与 RouteDecision 的完整 Schema；当 `route_offers.provisional_run_id` 为 `null` 时创建不可变 RunContext，否则只能向该已提前绑定的 provisional RunContext 写入 RouteDecision。随后写入 token hash 并删除 route offer。任何校验失败均不得创建新 RunContext 或 RouteDecision；同一 token 的重放或并发提交只有一个成功，其余返回 `route_token_invalid|conflict`。没有合法回执的 provisional RunContext 为未路由 Run：Stop 在删除 RouteOffer 的同一事务中将它、已绑定 TurnTrace 和 TraceEvent 的 `retention_until` 收紧为 `closed_at + 7 days`；Stop 缺失时以 `created_at + 7 days` 为上限。它没有 `route_decisions` 行，因此不可学习、不可 replay；P3.1 后其已绑定 Tool 事件仅可作为非因果诊断查看，绝不更新权重或程序化记忆。

每次显式 `skilltree maintenance sweep` 只读选择到期 RouteOffer 与未路由 provisional Run，再按每 workspace 最多 100 项执行独立删除事务：先删除已到期 RouteOffer；对未路由 Run，先删除其 TurnTrace（级联删除 TraceEvent 和 binding），再删除 RunContext。有效 RouteDecision、Episode、ReplayCapsule、Profile、L2 procedure 或其他 workspace 数据不得受此 sweeper 影响。每次物理删除只写 `unrouted_trace_purged` 审计，审计仅含 workspace/object handle hash、数量、策略版本与时间，`retention_until=purged_at+30 days`；清理失败保留原数据并在下轮重试。`clear-workspace-data` 仍立即物理删除，不等待该 sweeper。

#### MemoryExtractionCandidate

该对象与 RouteDecision 同回合产生，但仅在 `memory_write_enabled=true` 时送入写入管线；它不能包含密钥、令牌、密码、原始 Tool 参数或原始 Tool 输出。

```json
{
  "schema_version": "skilltree/v1",
  "profile_fields": [
    {
      "namespace": "identity",
      "key": "explanation_style",
      "value": "最多 256 字符",
      "confidence": 0.0,
      "reason": "最多 300 字符"
    }
  ],
  "procedural_candidates": [
    {
      "task_type": "repository_analysis",
      "rule": "先扫描候选 Skill，再由模型只对 Top-K 重排。",
      "why": "避免未信任技能进入模型上下文。",
      "applies_to": "skill_routing",
      "strength": "weak",
      "importance_prior": 0.5,
      "when": "最多 300 字符",
      "recommended_skill_names": ["analyze"],
      "ordering_constraints": [["analyze", "lsp"]],
      "avoid_when": "最多 300 字符",
      "evidence_event_ids": ["uuid"]
    }
  ]
}
```

`namespace` 枚举为 `identity|preference`；`key` 与 `task_type` 使用 `[a-z][a-z0-9_]{0,63}`；`confidence`、`importance_prior` 在 `[0,1]`，缺省 `importance_prior=0.5`。LLM 只能填写初始 `importance_prior`；`reinforcement_count`、`seen_count`、`usage_score`、`recency_score`、`score`、`low_score_sweeps` 和最终 `strength` 均由 Core 构造或重算，禁止信任模型回传值。`rule` 最长 500 字符，`why` 最长 200 字符，`applies_to` 最长 80 字符，`strength` 枚举为 `weak|strong`。每轮最多 8 个 Profile 字段与 3 个程序化候选；每个候选至多 3 个推荐 Skill、3 个顺序对和 10 个证据事件。技能名和事件 ID 分别必须属于 RunContext 快照和当前 run 的已持久化事件。Schema 通过不等于写入通过：`sanitize`、授权、作用域、同值检查或断路器仍可拒绝，并返回逐项 `{status, handle, reason_code}`。

L1 实际写入结果为 `{layer:"L1", handle:"namespace.key", action:"write|update", value:"sanitized value"}`；同值为 `{action:"no_op"}`。L2 实际写入或强化结果为：

```json
{
  "layer": "L2",
  "handle": "RFC4122 UUID",
  "action": "created|reinforced|hidden|rejected",
  "strength": "weak|strong",
  "seen_count": 1,
  "reinforcement_count": 0,
  "importance_prior": 0.5,
  "usage_score": 0.0,
  "recency_score": 1.0,
  "score": 45.0,
  "low_score_sweeps": 0,
  "last_reinforced_at": "RFC3339 UTC",
  "expires_at": "RFC3339 UTC",
  "reason_code": "optional"
}
```

`rejected` 仅保存 reason code 和审计引用，绝不保存被拒绝的原文。

#### MemoryRecallResult 与冻结读取

读取由 `UserPromptSubmit` Hook 在模型路由/执行上下文建立前启动，输入为 `{run_id, workspace_id, query_summary, budget_ms}`；`query_summary` 必须先经过 `sanitize`，最长 500 字符。`budget_ms` 首期固定为 `600`，调用方不得以模型输入覆盖；L1 的 SQLite 读取独立预算 `50ms`，L2 在总预算内执行。输出固定为：

```json
{
  "schema_version": "skilltree/v1",
  "status": "complete|degraded|timeout|disabled",
  "degrade_reason": "optional error code",
  "profile_fields": [
    {"namespace": "identity|preference", "key": "...", "value": "..."}
  ],
  "procedures": [
    {"rule": "...", "applies_to": "...", "strength": "weak|strong"}
  ],
  "retrieved_at": "RFC3339 UTC"
}
```

当 `memory_read_enabled=false` 时 Core 不读取 Profile 或 procedure，返回 `status=disabled` 的空数组；授权在读取开始前和冻结块提交前各检查一次，任一检查失败即丢弃已读结果并返回 `disabled` 空数组。L1 只读取同一 `user_id` 的 `identity|preference` 字段，按 `namespace ASC, field_key ASC` 排序。L2 召回是首期零外部依赖的确定性算法，借鉴参考 `query.py` 的“归一化 → 主 token → 细粒度 token → 查询 token 覆盖率”流程，但不引入其同义词、词权重、向量或词典依赖：对 `query_summary`、`applies_to` 和 `rule` 分别执行 Unicode NFKC、ASCII 小写、空白/标点归一化；将连续 `[a-z0-9][a-z0-9_+.#-]*` 保留为一个主 token，将连续汉字片段保留为一个主 token 且补充相邻二元汉字 token。token 去重但保持首次出现顺序，每个输入最多保留前 32 个 token；空、单个 ASCII 字符和纯标点不计 token。该算法版本固定为 `l2-token-recall/v1`，不得因模型输出或本地词典变化而改变。

L2 只扫描同一 `workspace_id,user_id`、`status=active AND expires_at>now_utc` 的最多 100 条 procedure，查询顺序固定为 `procedure_id ASC`；对每条候选令 `applies_to_score = |query_tokens ∩ applies_to_tokens| / |query_tokens|`、`rule_score = |query_tokens ∩ rule_tokens| / |query_tokens|`，空 `query_tokens` 时不召回 L2。`relevance_score = 0.65 * applies_to_score + 0.35 * rule_score`；仅当 `relevance_score >= 0.15`，或至少一个 query 主 token 与 `applies_to` 主 token 精确相同，候选才有资格注入。候选最终分数为 `final_score = 0.70 * relevance_score + 0.30 * (score / 100)`，按 `final_score DESC, updated_at DESC, procedure_id ASC` 排序，最多取 5 条；`score` 只能增强已相关规则，绝不能使零相关规则入选。L1 超过 50ms 或 SQLite 出错时，L1 置空并返回 `degraded`；L2 只等待 600ms 总预算，超时不取消后台读取且本轮跳过 L2 注入。`timeout`/`degraded` 表示“本轮未获得完整检索证据”，不能等价为“没有记忆”；已完成的结构化结果可作为 TraceEvent 证据写入，但不得改变已提交的冻结块。冻结块只能由 `status=complete` 的 `MemoryRecallResult` 投影而成，内容仅含已脱敏的 L1 key/value 与 L2 `rule/applies_to/strength`，无内部 ID、路径、分数、候选、hidden 记录或查询摘要；总长度最多 2000 UTF-8 bytes，并包裹在 `<skilltree_memory_frozen>` 中，固定注明“背景参考；当前用户请求优先”。

#### TraceEvent 与 Episode

事件经 `skilltree trace begin|bind|event|finish|outcome|flush --input <json-file>` 接收。`begin` 仅由 Core 创建 RunContext；`bind` 仅能把已经存在的 `run_id` 关联到已经存在的 `turn_token`。普通 Hook 仅可用 `event`、`finish` 和 `flush`，不得创建或绑定 run；唯一例外是 P3.1 的 `UserPromptSubmit` handler 通过不可公开调用的 Core `trace-reserve` 原子创建 provisional RunContext、TurnTrace 与 binding，不能由 stdin 中的模型/用户字段指定 `run_id`。`outcome` 仅接受用户、只读验证器或已注册的 Tool 适配器。Hook 入站对象不带 `ingest_sequence`，Core 赋值后形成下列持久化 TraceEvent：

```json
{
  "schema_version": "skilltree/v1",
  "event_id": "RFC4122 UUID",
  "turn_trace_id": "RFC4122 UUID",
  "run_id": "RFC4122 UUID, optional only when bound",
  "ingest_sequence": 1,
  "event_type": "turn_started|route_started|route_decided|skill_loaded|tool_started|tool_finished|tool_failed|claimed_outcome|run_closed|user_feedback|verifier_outcome|trace_flush_failed",
  "source": "core|hook_user_prompt|hook_pre_tool|hook_post_tool|hook_stop|router_adapter|tool_adapter|user|read_only_verifier",
  "coverage_state": "observed|partial|unobserved|unattributed",
  "observed_at": "RFC3339 UTC",
  "payload_hash": "sha256:<64 hex>",
  "payload_summary": "sanitized, at most 2048 characters",
  "evidence_ref": "optional local handle",
  "hook_bundle_hash": "sha256:<64 hex>, required only for hook_* source",
  "hook": {
    "session_id": "opaque string, at most 128 characters",
    "turn_id": "opaque string, at most 128 characters",
    "tool_use_id": "opaque string, optional",
    "tool_name": "string, optional, at most 128 characters"
  }
}
```

`ingest_sequence` 是大于零的整数，`payload_summary` 不得为空；`event_id`、`(turn_trace_id, ingest_sequence)` 与 Hook 的 `(session_id, turn_id, tool_use_id, event_type)` 均唯一。`tool_started` 只来自 `hook_pre_tool`，`tool_finished|tool_failed` 只来自 `hook_post_tool`；`claimed_outcome` 只来自模型/路由器语义，不能改变 verdict。`run_closed` 只来自 `hook_stop`，每个 TurnTrace 恰好一条且重复投递幂等。`finish` 输入固定为 `{schema_version, turn_trace_id, event_id, closure_source:"hook_stop", outcome_summary, coverage_state}`，其响应只能创建 `run_closed` 和初始 `unknown` verdict。

每个 `hook_*` 入站事件必须携带 `hook_bundle_hash`。它是对 `hooks/hooks.json` 与该配置引用的 `$PLUGIN_ROOT` 内 handler 文件按相对路径排序、逐文件 SHA-256 后形成的规范化总 SHA-256；其计算规则、被包含路径与 hash 必须写入 `runtime/bundle-manifest.json`。Core flush 时必须重新读取当前 Plugin 文件并计算该 hash；事件所带值、重新计算值与 manifest 声明值三者不一致时拒绝事件、记录不含原文的 `hook_bundle_mismatch` 审计，且不得更新观测状态。事件成功持久化时，在同一事务 upsert 对应 `hook_observations`；它只记录版本、hash、时间、计数和最近 event ID，不记录用户数据。工作区轨迹清除不删除该全局运行证明；Plugin 数据目录整体删除或 retention 到期后才移除它。

`outcome` 输入固定为 `{schema_version, run_id, turn_trace_id, event_id, source:"user|read_only_verifier|tool_adapter", verdict:"success|failed|cancelled|unknown", outcome_summary, evidence_ref, supersedes_event_id?}`；仅其来源可升级或降级 verdict。写入前 Core 必须验证 `(run_id, turn_trace_id)` 存在于 `run_turn_bindings`；不存在或不匹配时返回 `correlation_missing`、不写 `outcome_assessments`，并仅记录不含 outcome 原文的审计。Core 先选取最高可信来源，再在同一来源内按 `observed_at` 选择最新、未被 supersede 的 assessment；不同来源的冲突返回 `conflict` 并保留审计，不静默覆盖。assembler 输出的 `EpisodeRecord` 为：

```json
{
  "schema_version": "skilltree/v1",
  "episode_id": "RFC4122 UUID",
  "run_id": "RFC4122 UUID",
  "workspace_id": "sha256:<64 hex>",
  "objective_hash": "sha256:<64 hex>",
  "objective_preview": "sanitized, at most 500 characters",
  "trusted_skill_snapshot": [],
  "snapshot_partial": false,
  "trace_state": "complete|incomplete|flush_failed",
  "coverage_state": "observed|partial|unobserved|unattributed",
  "verdict": "unknown",
  "event_count": 0,
  "outcome_ref": "optional local handle"
}
```

`trace_state=complete` 仅代表事件投递与关闭完整，不代表任务成功；`verdict` 只允许 `success|failed|cancelled|unknown`。只有 `trace_state=complete`、`coverage_state=observed`、已绑定 RunContext 且有完整 Skill 快照的 Episode 才可用于权重更新或反事实 replay。

#### ReplayCapsule

`ReplayCapsule` 仅在 `replay_capture_enabled=true` 且用户对该 `run_id` 给出一次性明确授权后创建。授权记录必须含 `consent_id`、run、时间、策略版本和到期时间；trace/memory 同意不得复用为 replay 同意。对象固定为：

```json
{
  "schema_version": "skilltree/v1",
  "replay_capsule_id": "RFC4122 UUID",
  "run_id": "RFC4122 UUID",
  "consent_id": "RFC4122 UUID",
  "mode": "fixture_only",
  "blob_handle": "local encrypted handle",
  "content_hash": "sha256:<64 hex>",
  "skill_snapshot_refs": ["immutable local encrypted handle"],
  "tool_fixture_refs": ["immutable local encrypted handle"],
  "captured_at": "RFC3339 UTC",
  "expires_at": "RFC3339 UTC",
  "status": "ready|rejected|deleted|expired"
}
```

加密 blob 包含经凭据检测后的 canonical user request、trusted Skill 的不可变内容副本、模型/Plugin/Core/Schema/策略版本和脱敏 Tool fixture；不得包含原始密钥、Token、密码、原始 transcript、Hosted Tool 交互或可产生真实外部副作用的请求。`mode` 永远为 `fixture_only`；仅 `ready` 可有非空 `consent_id`、blob handle、content hash 和 expires_at。任一字段拒绝、缺失或无法 fixture 化时 Capsule 必须为 `rejected`，其 consent/blob/hash/expiry 均为空，且 Episode 的 `snapshot_partial=true`。到期或用户删除必须先擦除 blob，再将记录置为 `expired|deleted` 并清空上述四字段；不允许保留可访问的孤儿 blob。默认 `expires_at` 为 `captured_at + 30 days`；用户可通过 `skilltree replay capture|list|delete` 授权、查看元数据或按 `replay_capsule_id` 删除。`clear-workspace-data` 必须先物理删除 blob 再删除 Capsule 元数据。

#### SQLite、迁移与可靠投递

SQLite 数据库唯一位置为 `$PLUGIN_DATA/skilltree.sqlite3`；开发模式可通过显式 `SKILLTREE_DATA_DIR` 覆盖，但不得写入工作区、Plugin 根目录或用户 Skill 根目录。启动时必须启用外键、WAL 和有限 `busy_timeout`。迁移文件名固定为 `NNNN_<scope>.sql`，版本为十进制正整数且严格连续；每个发行 Bundle 仅携带从 `0001` 到其 `bundle-manifest.schema.migration_version` 的完整不可变集合。`storage initialize` 在单一事务中按升序应用缺失迁移，并为每一份成功文件写入 `schema_migrations(version PRIMARY KEY, applied_at, content_hash)`；已记录的 version 与发行文件 content hash 不同、数据库版本高于 Bundle、缺号、重复号、非连续号或任一 DDL 失败均必须回滚本次事务、停止写入并返回 `internal_error`。迁移只允许前向追加，首期不支持自动降级或修改已发布 migration 文件。

#### 全局保留与清理矩阵

所有时间均为 UTC。除用户执行 `clear-profile` 或 `clear-workspace-data` 的立即删除外，首期采用最小本地保留策略 `retention/v1`；`retention_until` 是可物理删除的最早时间，不是读取授权，也不延长对象的业务有效期。各创建/更新路径必须在同一事务写入下表的默认值，禁止由调用方任意指定保留期：

| 数据 | 默认保留/失效 | 清理条件与例外 |
| --- | --- | --- |
| RouteOffer | `prepared_at + 5 minutes` | 到期立即删除；合法 Stop 无回执时立即删除。 |
| 未路由 provisional Run、其 TurnTrace/TraceEvent/binding | `closed_at + 7 days`，无 Stop 时上限为 `created_at + 7 days` | 只作诊断，不学习、不回放；按既有未路由清理顺序删除。 |
| 已路由 RunContext、RouteDecision、TurnTrace、TraceEvent、Episode、OutcomeAssessment | `created_at + 90 days`；同一 Run 的关联对象统一以 Run 的 `retention_until` 为上限 | 若仍被 `ready` ReplayCapsule 或 `draft|replay_passed` EvolutionCandidate 引用，常规 sweep 跳过整个 Run/Episode 图；`clear-workspace-data` 先解除这些引用后立即删除。 |
| HookObservation | 最后一次观测后 90 天 | 不随 workspace 清理；仅 Plugin 数据目录整体删除或到期 sweep 可删。 |
| ProfileField、SkillWeight | `retention_until = NULL`（无自动到期） | 仅用户逐 handle 删除或 `clear-profile` / `clear-workspace-data` 删除；权重 30 天向零衰减不是删除。 |
| ReplayCapsule blob | `captured_at + 30 days` | 到期先物理擦除 blob，并原子清空 blob/consent/hash 字段、将 Capsule 置为 `expired`；Capsule 元数据保留至 `created_at + 90 days`。 |
| EvolutionCandidate、ReplayReport | `created_at + 180 days` | Candidate 与其 Report 同寿命；到期前仍可用于审查，但不延长已失效 Capsule blob。 |
| AuditEvent | `created_at + 30 days` | 只保存脱敏原因码和 handle hash；不受普通对象删除影响。 |
| MemoryWriteBreaker | 最后更新后 30 天 | 仅 `closed` 且连续失败为零时可删；`open|half_open` 不得清理。 |
| pending MemoryCandidate、hidden L2 procedure | 分别为 7 天、hidden 后 30 天 | 继续遵守 F4 的审批、隐藏与物理删除语义。 |

`skilltree maintenance sweep` 由用户显式触发，按固定顺序处理：先处理 RouteOffer/未路由 provisional Run，再处理 Capsule blob 到期、pending 候选与 L2 TTL，随后处理已路由 Run 图、HookObservation、EvolutionCandidate/ReplayReport、AuditEvent 和 closed Breaker。对每个 workspace、每一类对象最多选择 100 条，并将每条对象的状态迁移或删除放在独立事务中；全局 HookObservation/AuditEvent 每类同样最多 100 条。删除 Run 图前必须再次检查上述 Capsule/Candidate 引用，检查失败或事务冲突时保留原数据并在下次 sweep 重试。任何常规物理删除只写不含原文的审计；审计自身写入失败不得阻止删除，也不得递归创建审计。`clear-workspace-data` 固定顺序为：擦除 replay blob → 删除 Capsule 元数据、EvolutionCandidate/Report 及其 Episode 引用 → 删除 Run 图、weights、procedures、workspace candidates；`clear-profile` 删除 Profile 和 Profile candidates；两者均不等待 sweep。

- 不可变 `RunContext` 与 Hook `TurnTrace` 由 `run_turn_bindings` 独立关联；不得把可选 `run_id` 写入 `turn_traces`。每个任务只能新增其对应版本的迁移文件，禁止在 P0 的 `0001` 中预建后续阶段的表。迁移映射固定为：`0001_p0_runtime.sql`（P0：schema_migrations、RuntimeConfig、AuditEvent）、`0002_p1_registry.sql`（P1：skills）、`0003_p2_routing.sql`（P2：RunContext、RouteOffer、RouteDecision）、`0004_p3_turn_binding.sql`（P3.1：TurnTrace、RunTurnBinding）、`0005_p3_trace.sql`（P3.2：TraceEvent、HookObservation、Episode、OutcomeAssessment）、`0006_p4_learning.sql`（P4：SkillWeight）、`0007_p5_memory.sql`（P5：MemoryWriteBreaker、MemoryCandidate、ProfileField、Procedure）及 `0008_p6_replay.sql`（P6：ReplayCapsule、EvolutionCandidate、其 Episode 引用和 ReplayReport）。迁移必须使用如下字段、约束与删除策略，禁止以“字段摘要”替代可执行 DDL：

```sql
-- 0001_p0_runtime.sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE runtime_config (
  config_id INTEGER PRIMARY KEY CHECK(config_id = 1),
  config_version INTEGER NOT NULL CHECK(config_version >= 1),
  skill_root TEXT, skill_root_hash TEXT,
  trace_capture_enabled INTEGER NOT NULL CHECK(trace_capture_enabled IN (0,1)),
  memory_read_enabled INTEGER NOT NULL CHECK(memory_read_enabled IN (0,1)),
  memory_write_enabled INTEGER NOT NULL CHECK(memory_write_enabled IN (0,1)),
  replay_capture_enabled INTEGER NOT NULL CHECK(replay_capture_enabled IN (0,1)),
  updated_at TEXT NOT NULL,
  CHECK((skill_root IS NULL AND skill_root_hash IS NULL) OR
        (skill_root IS NOT NULL AND skill_root_hash IS NOT NULL AND length(skill_root_hash) = 71))
);
INSERT INTO runtime_config(
  config_id, config_version, skill_root, skill_root_hash,
  trace_capture_enabled, memory_read_enabled, memory_write_enabled, replay_capture_enabled, updated_at
) VALUES (1, 1, NULL, NULL, 0, 0, 0, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
CREATE TABLE audit_events (
  audit_id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('user_global','workspace','plugin_global')),
  workspace_id TEXT, event_type TEXT NOT NULL, object_handle_hash TEXT,
  reason_code TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_audit_events_retention ON audit_events(retention_until);

-- 0002_p1_registry.sql
CREATE TABLE skills (
  name TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '' CHECK(length(description) <= 500),
  path TEXT NOT NULL UNIQUE, content_hash TEXT NOT NULL CHECK(length(content_hash) = 71),
  state TEXT NOT NULL CHECK(state IN ('pending','trusted','blocked','invalid','out_of_scope')),
  diagnostic TEXT, updated_at TEXT NOT NULL,
  CHECK((state != 'invalid' AND length(description) BETWEEN 1 AND 500) OR state = 'invalid')
);

-- 0003_p2_routing.sql
CREATE TABLE run_contexts (
  run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  snapshot_json TEXT NOT NULL, trace_capture_enabled INTEGER NOT NULL,
  memory_read_enabled INTEGER NOT NULL, memory_write_enabled INTEGER NOT NULL,
  replay_capture_enabled INTEGER NOT NULL, created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_run_contexts_retention ON run_contexts(retention_until);
CREATE TABLE route_offers (
  route_token_hash TEXT PRIMARY KEY CHECK(length(route_token_hash) = 71),
  workspace_id TEXT NOT NULL, session_id_hash TEXT NOT NULL,
  provisional_run_id TEXT UNIQUE REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  trusted_snapshot_json TEXT NOT NULL CHECK(length(trusted_snapshot_json) BETWEEN 2 AND 131072 AND json_valid(trusted_snapshot_json) AND json_type(trusted_snapshot_json) = 'array'),
  candidate_json TEXT NOT NULL CHECK(length(candidate_json) BETWEEN 2 AND 16384 AND json_valid(candidate_json) AND json_type(candidate_json) = 'array'),
  candidate_snapshot_hash TEXT NOT NULL CHECK(length(candidate_snapshot_hash) = 71),
  prepared_at TEXT NOT NULL, expires_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_route_offers_expiry ON route_offers(expires_at);
CREATE TABLE route_decisions (
  run_id TEXT PRIMARY KEY REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  route_token_hash TEXT NOT NULL UNIQUE CHECK(length(route_token_hash) = 71),
  candidate_snapshot_hash TEXT NOT NULL CHECK(length(candidate_snapshot_hash) = 71),
  decision_json TEXT NOT NULL CHECK(length(decision_json) BETWEEN 2 AND 4096 AND json_valid(decision_json) AND json_type(decision_json) = 'object'),
  committed_at TEXT NOT NULL, retention_until TEXT NOT NULL
);

-- 0004_p3_turn_binding.sql
CREATE TABLE turn_traces (
  turn_trace_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
  session_id_hash TEXT NOT NULL, workspace_id TEXT NOT NULL, turn_token_hash TEXT NOT NULL UNIQUE,
  soft_expires_at TEXT NOT NULL, hard_expires_at TEXT NOT NULL, consumed_at TEXT,
  prompt_hash TEXT NOT NULL, coverage_state TEXT NOT NULL, closed_at TEXT, retention_until TEXT NOT NULL,
  UNIQUE(session_id, turn_id), UNIQUE(session_id_hash, turn_id)
);
CREATE INDEX idx_turn_traces_retention ON turn_traces(retention_until);
CREATE TABLE run_turn_bindings (
  run_id TEXT PRIMARY KEY REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  turn_trace_id TEXT NOT NULL UNIQUE REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  bound_at TEXT NOT NULL, bind_state TEXT NOT NULL CHECK(bind_state IN ('normal','late')),
  UNIQUE(run_id, turn_trace_id)
);

-- 0005_p3_trace.sql
CREATE TABLE trace_events (
  event_id TEXT PRIMARY KEY, turn_trace_id TEXT NOT NULL REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  ingest_sequence INTEGER NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL,
  tool_use_id TEXT, payload_hash TEXT NOT NULL, payload_summary TEXT NOT NULL,
  evidence_ref TEXT, hook_bundle_hash TEXT, created_at TEXT NOT NULL, retention_until TEXT NOT NULL,
  UNIQUE(turn_trace_id, ingest_sequence), UNIQUE(turn_trace_id, tool_use_id, event_type)
);
CREATE TABLE hook_observations (
  hook_bundle_hash TEXT PRIMARY KEY CHECK(length(hook_bundle_hash) = 71),
  plugin_version TEXT NOT NULL, core_version TEXT NOT NULL, schema_version TEXT NOT NULL,
  first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
  last_observed_event_id TEXT REFERENCES trace_events(event_id) ON DELETE SET NULL,
  observed_event_count INTEGER NOT NULL CHECK(observed_event_count >= 1),
  retention_until TEXT NOT NULL
);
CREATE INDEX idx_hook_observations_retention ON hook_observations(retention_until);
CREATE TABLE episodes (
  episode_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  trace_state TEXT NOT NULL, coverage_state TEXT NOT NULL, verdict TEXT NOT NULL, outcome_ref TEXT,
  created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_episodes_retention ON episodes(retention_until);
CREATE TABLE outcome_assessments (
  event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  turn_trace_id TEXT NOT NULL, source TEXT NOT NULL, verdict TEXT NOT NULL, evidence_ref TEXT, observed_at TEXT NOT NULL,
  supersedes_event_id TEXT UNIQUE, retention_until TEXT NOT NULL,
  FOREIGN KEY(run_id, turn_trace_id) REFERENCES run_turn_bindings(run_id, turn_trace_id) ON DELETE CASCADE
);
CREATE INDEX idx_outcome_assessments_retention ON outcome_assessments(retention_until);

-- 0006_p4_learning.sql
CREATE TABLE skill_weights (
  workspace_id TEXT NOT NULL, skill_name TEXT NOT NULL REFERENCES skills(name) ON DELETE RESTRICT,
  weight INTEGER NOT NULL, evidence_count INTEGER NOT NULL, updated_at TEXT NOT NULL,
  retention_until TEXT NULL CHECK(retention_until IS NULL),
  PRIMARY KEY(workspace_id, skill_name)
);
```

- P5 迁移必须在上述基础上创建下列 SQLite 表和索引；字段值已全部经过 Schema 校验和 `sanitize`，但候选或审计均不得存放被拒绝的敏感原文。`memory_candidates` 是唯一的待审批载体，且数据库中只允许 `pending` 状态存在候选正文；`profile_fields` 和 `procedures` 只能引用批准时的 `source_candidate_id`，候选批准后可立即删除，其外键必须 `ON DELETE SET NULL`。SQLite 不支持跨行 Profile 总长度约束，因此写入事务必须在 upsert 前验证同一 `user_id` 的 active 字段值总长度不超过 `profile_max_chars=1500`。

```sql
-- 0007_p5_memory.sql
CREATE TABLE memory_write_breakers (
  workspace_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('closed','open','half_open')),
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures BETWEEN 0 AND 3),
  open_until TEXT, updated_at TEXT NOT NULL, retention_until TEXT NOT NULL,
  CHECK((state = 'open' AND open_until IS NOT NULL) OR (state IN ('closed','half_open') AND open_until IS NULL))
);
CREATE TABLE memory_candidates (
  candidate_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  layer TEXT NOT NULL CHECK(layer IN ('profile','procedure')),
  kind TEXT NOT NULL CHECK(kind IN ('identity','preference','procedure')),
  scope TEXT NOT NULL CHECK(scope IN ('user_global','workspace')),
  payload_json TEXT NOT NULL CHECK(length(payload_json) BETWEEN 2 AND 4096 AND json_valid(payload_json) AND json_type(payload_json) = 'object'),
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status = 'pending'),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, retention_until TEXT NOT NULL,
  CHECK((layer = 'profile' AND kind IN ('identity','preference') AND scope = 'user_global')
     OR (layer = 'procedure' AND kind = 'procedure' AND scope = 'workspace'))
);
CREATE INDEX idx_memory_candidates_pending
  ON memory_candidates(workspace_id, user_id, status, expires_at);

CREATE TABLE profile_fields (
  profile_field_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope = 'user_global'),
  namespace TEXT NOT NULL CHECK(namespace IN ('identity','preference')),
  field_key TEXT NOT NULL CHECK(field_key GLOB '[a-z][a-z0-9_]*' AND length(field_key) <= 64),
  value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 256), value_hash TEXT NOT NULL,
  source_candidate_id TEXT REFERENCES memory_candidates(candidate_id) ON DELETE SET NULL,
  source_run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  retention_until TEXT NULL CHECK(retention_until IS NULL),
  UNIQUE(user_id, namespace, field_key)
);
CREATE INDEX idx_profile_fields_user ON profile_fields(user_id, namespace, updated_at);

CREATE TABLE procedures (
  procedure_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope = 'workspace'),
  rule TEXT NOT NULL CHECK(length(rule) BETWEEN 1 AND 500), rule_hash TEXT NOT NULL,
  shingle_fingerprint TEXT NOT NULL CHECK(length(shingle_fingerprint) = 71),
  applies_to TEXT NOT NULL CHECK(length(applies_to) BETWEEN 1 AND 80),
  recommended_skill_names_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(recommended_skill_names_json) AND json_type(recommended_skill_names_json) = 'array'),
  ordering_constraints_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(ordering_constraints_json) AND json_type(ordering_constraints_json) = 'array'),
  avoid_when TEXT NOT NULL DEFAULT '' CHECK(length(avoid_when) <= 300),
  strength TEXT NOT NULL CHECK(strength IN ('weak','strong')),
  importance_prior REAL NOT NULL DEFAULT 0.5 CHECK(importance_prior >= 0.0 AND importance_prior <= 1.0),
  reinforcement_count INTEGER NOT NULL DEFAULT 0 CHECK(reinforcement_count >= 0),
  seen_count INTEGER NOT NULL CHECK(seen_count >= 1),
  usage_score REAL NOT NULL DEFAULT 0.0 CHECK(usage_score >= 0.0 AND usage_score <= 1.0),
  recency_score REAL NOT NULL DEFAULT 1.0 CHECK(recency_score >= 0.0 AND recency_score <= 1.0),
  score REAL NOT NULL DEFAULT 0.0 CHECK(score >= 0.0 AND score <= 100.0),
  low_score_sweeps INTEGER NOT NULL DEFAULT 0 CHECK(low_score_sweeps >= 0),
  last_reinforced_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','hidden')),
  source_candidate_id TEXT REFERENCES memory_candidates(candidate_id) ON DELETE SET NULL,
  source_run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  hidden_at TEXT, retention_until TEXT NOT NULL,
  CHECK((status = 'active' AND hidden_at IS NULL) OR (status = 'hidden' AND hidden_at IS NOT NULL)),
  UNIQUE(workspace_id, user_id, applies_to, rule_hash)
);
CREATE INDEX idx_procedures_recall
  ON procedures(workspace_id, user_id, applies_to, status, expires_at);
CREATE INDEX idx_procedures_fingerprint
  ON procedures(workspace_id, user_id, applies_to, shingle_fingerprint, status);
CREATE INDEX idx_procedures_sweep
  ON procedures(status, expires_at, low_score_sweeps, last_reinforced_at);
```

P5 的 `memory approve` 在单一事务中校验候选仍存在、同一用户/工作区、尚未到期和授权有效，再先创建或 upsert L1/L2，最后删除候选行；审批并发时仅一个事务可以成功，其他请求返回 `not_found|conflict` 且不产生第二次写入。`memory reject`、`clear-profile` 中的 Profile 候选清理和 `clear-workspace-data` 中的 workspace 候选清理均直接删除候选行，再写对应脱敏审计。批准 procedure 时必须由 Core 计算并持久化上述 TTL 分数列，不能采用模型给出的 `strength` 或 TTL。sanitize 拒绝和 no-op 均仅写原因码、handle hash 与状态审计。`clear-profile` 物理删除该用户所有 `profile_fields` 及 Profile 候选；`clear-workspace-data` 先删除 Capsule blob，再删除该 workspace 的轨迹、weights、Capsule 元数据、procedures 和 workspace 候选。相关 `source_candidate_id` 由外键置空；审计以对象 handle hash 保留至 retention。`skilltree maintenance sweep` 默认不由后台调度，必须由用户显式执行；一次运行按每 workspace 最多 100 个 procedure 与 100 个到期候选分批、每条独立事务处理：先删除 `expires_at <= now_utc` 的 pending 候选并写 `candidate_expired` 审计；再 (1) 对到期 active 行执行 `active → hidden`，写 `hidden_at=now_utc` 与 `retention_until=hidden_at+30 days`；(2) 对尚未到期 active 行重算 `usage_score`、`recency_score`、`score` 和 `low_score_sweeps`，低于 35 分连续两次才降级，达到 70 分且强化次数至少 2 次立即升级；(3) 对 `status=hidden AND retention_until <= now_utc` 行物理删除。物理删除前后不允许恢复已删除行；任何单条失败回滚该条事务、保留原行并在下一次显式 sweep 重试。每次状态转换或物理删除仅写不含 rule 原文的 `procedure_hidden|procedure_purged` 审计，包含 procedure handle hash、workspace hash、原因码、策略版本和时间；审计的 `retention_until=created_at+30 days`。用户逐 handle `delete` 和 `clear-workspace-data` 均绕过隐藏窗口、立即物理删除，并分别写 `procedure_deleted_by_user|workspace_data_cleared` 审计。
- 每个 Hook 事件先以随机文件名写入 `$PLUGIN_DATA/outbox/staging/<random>.tmp`，fsync 后原子重命名到 `ready/<random>.json`；flush 成功后才删除该文件。Core 以 `event_id + payload_hash` 幂等消费；无法消费的文件进入 `$PLUGIN_DATA/outbox/failed/`，ID 冲突文件进入 `quarantine/`，并以不含原始内容的 audit record 记录 `trace_flush_failed|event_collision`。不得以“写审计到同一故障数据库”作为失败恢复方案。

#### ReplayReport、run_arm 与演进调度

P6 只能在已应用 `0001`–`0007` 后通过 `0008_p6_replay.sql` 创建下列回放/演进表；任何外键指向的 Run 或 Episode 在此前阶段均不存在这类引用，P0–P5 也不得创建这些空表作为占位：

```sql
-- 0008_p6_replay.sql
CREATE TABLE replay_capsules (
  replay_capsule_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run_contexts(run_id) ON DELETE RESTRICT,
  workspace_id TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode = 'fixture_only'), consent_id TEXT,
  blob_handle TEXT, content_hash TEXT, status TEXT NOT NULL CHECK(status IN ('ready','rejected','expired','deleted')),
  expires_at TEXT, retention_until TEXT NOT NULL, created_at TEXT NOT NULL,
  CHECK(
    (status = 'ready' AND consent_id IS NOT NULL AND blob_handle IS NOT NULL AND content_hash IS NOT NULL AND expires_at IS NOT NULL)
    OR
    (status IN ('rejected','expired','deleted') AND consent_id IS NULL AND blob_handle IS NULL AND content_hash IS NULL AND expires_at IS NULL)
  )
);
CREATE TABLE evolution_candidates (
  candidate_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','replay_passed','rejected','rolled_back')),
  created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_evolution_candidates_retention ON evolution_candidates(workspace_id, retention_until);
CREATE TABLE evolution_candidate_episode_refs (
  candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
  PRIMARY KEY(candidate_id, episode_id)
);
CREATE TABLE replay_reports (
  report_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE REFERENCES evolution_candidates(candidate_id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, retention_until TEXT NOT NULL
);
CREATE INDEX idx_replay_reports_retention ON replay_reports(retention_until);
```

`EvolutionCandidate` 是补丁候选而不是可执行 Skill，Schema 为：

```json
{
  "schema_version": "skilltree/v1",
  "candidate_id": "RFC4122 UUID",
  "operation": "patch|deprecate",
  "target_skill_name": "analyze",
  "baseline_content_hash": "sha256:<64 hex>",
  "evidence_episode_ids": ["RFC4122 UUID"],
  "patch": "unified diff, at most 20000 characters",
  "risk_tier": "low|medium|high",
  "status": "draft|replay_passed|rejected|rolled_back",
  "rollback": {"restore_content_hash": "sha256:<64 hex>"}
}
```

`evidence_episode_ids` 至少 1 条、最多 20 条，且都来自同一 `workspace_id`；`target_skill_name` 必须是 trusted Skill。状态只可按合法迁移变更；首期拒绝 `new`、`shadow`、`canary` 与 `active`。

`run_arm(episode_id, is_candidate)` 是 P6 注入的受控运行器，完整输入和输出固定为：

```json
{
  "request": {
    "schema_version": "skilltree/v1",
    "episode_id": "RFC4122 UUID",
    "replay_capsule_id": "RFC4122 UUID with status ready",
    "arm": "baseline|candidate",
    "candidate_id": "RFC4122 UUID or omitted only for baseline",
    "timeout_ms": 60000,
    "network": "deny",
    "workspace_access": "read_only_snapshot",
    "artifact_dir": "local isolated handle"
  },
  "result": {
    "schema_version": "skilltree/v1",
    "episode_id": "RFC4122 UUID",
    "arm": "baseline|candidate",
    "verdict": "success|failed|cancelled|unknown",
    "quality_score": 0.0,
    "latency_ms": 0,
    "error_code": "optional fixed error code",
    "guardrail_breaches": ["optional fixed guardrail code"],
    "artifact_refs": []
  }
}
```

`timeout_ms` 范围为 `[1000, 300000]`，`quality_score` 范围为 `[0,1]`；`candidate_id` 必须对应同一 workspace 的 `draft` 候选，`replay_capsule_id` 必须属于该 Episode 且为 `ready`。运行器只允许 `fixture_only`：在 Capsule 冻结输入、Skill 内容副本和 Tool fixture 下运行，绝不重放真实网络、MCP、Hosted Tool 或外部副作用；超时/异常/隔离违规输出 `unknown`，不计作改进。

`run_arm` 的唯一生产隔离后端为本机已通过 Doctor 验证的 OCI 容器运行时（首期实现目标为 Docker Engine）。不得以普通 Windows 子进程、PowerShell、Job Object、Windows Sandbox 或远程容器作为降级后端；它们至多用于开发测试，不能产生可写入 `ReplayReport` 的结果。运行前 Doctor 必须验证：运行时二进制路径来自受控配置而非 `PATH`、`docker version` 的 Client/Server 均可用、固定的本地 `skilltree-replay-runner` 镜像存在且其 digest 等于 Bundle Manifest 固定值；任一检查失败时 `skilltree evolve scan` 必须 fail-closed，不启动 baseline 或 candidate，返回 `replay_runtime_unavailable`，且不得创建“neutral”“insufficient”或可用于迁移的 Report。

每个 arm 在新容器中运行一次，固定为 `--user 65532:65532`、`--cap-drop ALL`、`--security-opt no-new-privileges:true`、`--read-only`、`--network none`，禁止挂载 Docker socket、Named Pipe、宿主设备、用户 home、工作区、Plugin 根目录、`$PLUGIN_DATA`、环境文件和凭据目录。容器只允许以下三个挂载点：解密后生成的 Capsule fixture 目录只读挂载到 `/input`，baseline 或候选 Skill 的不可变内容快照只读挂载到 `/skill`，以及空的、每 arm 唯一的 artifact 目录可写挂载到 `/artifacts`；它们都必须由 Core 在 `$PLUGIN_DATA/replay-staging/<random>/` 创建并在结束后清理。容器环境固定为最小 allowlist（`LANG=C.UTF-8`、`LC_ALL=C.UTF-8`、`TZ=UTC`、`HOME=/nonexistent`、`PATH=/usr/bin:/bin`），不得继承宿主环境变量；任何名称含 `KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PROXY` 的变量也必须被拒绝。容器用户只能读取 `/input`、`/skill` 与运行器自身文件，唯一可写路径为 `/artifacts` 与有限大小 tmpfs `/tmp`。

首期资源限制固定为 `--cpus 1`、`--memory 512m`、`--pids-limit 64`、`--tmpfs /tmp:rw,noexec,nosuid,size=64m`，artifact 总量不超过 8 MiB，并使用 request 的 wall-clock `timeout_ms`；Core 到期先请求终止、5 秒后强制删除容器，并确认容器 ID 不再存活。运行器入口只能消费 `/input/request.json`、`/skill/SKILL.md` 与 `/input/tool-fixtures.json`，只能把一个不超过 64 KiB、符合 `skilltree-run-arm/v1` 的结果 JSON 写入 `/artifacts/result.json`；它不得执行 Shell、下载依赖、启动特权子进程、读取未挂载路径或请求网络。`guardrail_breaches` 只能为 `container_create_mismatch|network_denied|mount_denied|credential_access_denied|workspace_write_denied|resource_limit_exceeded|timeout|container_cleanup_failed|result_invalid`。Core 必须校验容器创建参数、退出状态、result schema、artifact allowlist/字节上限并再次 `sanitize` 后才读取结果；缺失/超量/多余 artifact、非零退出、超时、容器仍存活、网络/路径/权限违规或输出校验失败都映射为 `verdict=unknown`，并记录上述固定 breach，且该 arm 不计入有效比较。

Replay 容器镜像和其 digest 属于 P6 独立发行的 Replay Extension Bundle，而非 P0 基础 Plugin Bundle。用户在 P6 阶段显式安装该扩展时，安装器才允许从 Extension Bundle 提供的 OCI archive 以无网络方式加载镜像并把 `image_digest` 写入 replay runtime state；不得在 replay 时拉取、构建或更新镜像。`skilltree evolve scan` 在创建解密 staging 后必须重新检查用户对每个 Capsule 的授权仍有效、Capsule 为 `ready`、blob/hash 与当前基础 Bundle、Replay Extension Bundle 和 Skill snapshot 一致；检查或 cleanup 任一失败时写入不含原文的审计并拒绝本次 arm。无论成功、失败、超时或崩溃恢复，Core 都必须删除容器和 staging；删除失败标记 `replay_cleanup_failed`，后续 scan 前先尝试清理，且对应 Episode/Capsule 不再可用于新的 replay，直至清理成功并经用户再次授权。

`ReplayReport` 完整对象固定为：

```json
{
  "schema_version": "skilltree/v1",
  "report_id": "RFC4122 UUID",
  "candidate_id": "RFC4122 UUID",
  "dataset_snapshot": {"episode_ids": [], "content_hash": "sha256:<64 hex>"},
  "baseline_metrics": {"success_rate": 0.0, "mean_quality_score": 0.0, "p95_latency_ms": 0},
  "candidate_metrics": {"success_rate": 0.0, "mean_quality_score": 0.0, "p95_latency_ms": 0},
  "sample_size": 0,
  "effect_size": 0.0,
  "coverage": {"success": 0, "failure_recovery": 0, "negative": 0, "complete": false},
  "guardrail_breaches": [],
  "verdict": "improved|regressed|neutral|insufficient",
  "created_at": "RFC3339 UTC"
}
```

`dataset_snapshot.episode_ids` 至少 1 条、最多 200 条，所有 Episode 必须来自同一 workspace、`trace_state=complete`、`coverage_state=observed`、快照完整且有 `ReplayCapsule.status=ready`；`sample_size` 必须等于其长度。`effect_size` 是 `candidate.mean_quality_score - baseline.mean_quality_score`。只有每个 Episode 的 baseline/candidate 均完成无隔离违规、`effect_size > 0`、成功率不下降、coverage 三类均至少 1、无 guardrail breach 且 `verdict=improved` 才允许迁移到 `replay_passed`。隔离运行时不可用或任一 arm 被 guardrail 拒绝时，不创建 `ReplayReport`，候选保持 `draft`。

首期调度为两个入口：任务收尾只提交 Episode 组装与“是否达到阈值”的轻量检查；`skilltree evolve scan` 由用户显式运行，默认 `scheduler_enabled=false`，并按本节的 baseline → candidate → ReplayReport 顺序执行受控演进 Loop。未来周期调度必须有单实例锁、批量上限、可恢复 backfill，并先补齐缺失 Episode 再挖掘；调度器永远不得自动应用补丁或推进发布。

#### Codex Plugin Runtime Contract

首期插件名固定为 `skilltree`（小写 kebab-case）；Plugin 是安装边界，Python Core 是可独立测试的发行包，`skill-router` 是用户/模型入口，Hook 是实际调用观测器。Plugin 根目录必须包含：

```text
codex-plugin/
  .codex-plugin/plugin.json
  skills/skill-router/SKILL.md
  hooks/hooks.json
  runtime/skilltree_bootstrap.ps1
  runtime/skilltree_hook.py
  runtime/wheels/skilltree_core-<version>-py3-none-any.whl
  migrations/0001_p0_runtime.sql
  runtime/bundle-manifest.json
  scripts/setup.ps1
  requirements.lock
  README.md
```

`plugin.json` 首期只使用经当前本地 Plugin validator 验证的最小字段：`name:"skilltree"`、语义化 `version`、`description`、`author.name`、`skills:"./skills/"` 与 `interface.displayName:"SkillTree"`。不在 manifest 中声明 Hook 路径；Codex 从默认 `hooks/hooks.json` 发现它，避免依赖不兼容的 manifest Hook 扩展字段。P0 必须执行项目固定版本的 `validate_plugin.py`，并把 validator 版本和输出保存为发布 artifact。

`runtime/bundle-manifest.json` 是 P0 的离线完整性清单，固定为下列 Schema；所有 `path` 必须是 `/` 分隔、相对 Plugin 根目录、非空且不得包含 `..`，所有 hash 均为 `sha256:<64 lowercase hex>`。数组按 `path` 或 `filename` 严格升序，不允许重复项、未知顶级字段、缺失字段、sdist 或未列入 `wheels` 的运行时依赖。`bundle_hash` 为除自身外整个对象按 RFC 8785 JSON Canonicalization Scheme 序列化后的 SHA-256；它用于发现不一致或意外损坏，不构成对可同时改写 Plugin 文件和 Manifest 的攻击者的签名保证，该保证留给 P7。

```json
{
  "schema_version": "skilltree-bundle/v1",
  "plugin": {"name": "skilltree", "version": "0.1.0", "manifest_path": ".codex-plugin/plugin.json", "sha256": "sha256:<64 hex>"},
  "core": {"distribution": "skilltree-core", "version": "0.1.0", "wheel": "runtime/wheels/skilltree_core-0.1.0-py3-none-any.whl", "sha256": "sha256:<64 hex>"},
  "schema": {"version": "skilltree/v1", "migration_version": 1},
  "migrations": [
    {"version": 1, "path": "migrations/0001_p0_runtime.sql", "sha256": "sha256:<64 hex>"}
  ],
  "requirements_lock": {"path": "requirements.lock", "sha256": "sha256:<64 hex>"},
  "runtime_files": [
    {"path": "runtime/skilltree_bootstrap.ps1", "sha256": "sha256:<64 hex>"},
    {"path": "runtime/skilltree_hook.py", "sha256": "sha256:<64 hex>"},
    {"path": "scripts/setup.ps1", "sha256": "sha256:<64 hex>"},
    {"path": "skills/skill-router/SKILL.md", "sha256": "sha256:<64 hex>"}
  ],
  "wheels": [{"filename": "skilltree_core-0.1.0-py3-none-any.whl", "distribution": "skilltree-core", "version": "0.1.0", "sha256": "sha256:<64 hex>"}],
  "hook_bundle": {
    "algorithm": "sha256-sorted-path-file-hashes/v1",
    "files": ["hooks/hooks.json", "runtime/skilltree_bootstrap.ps1", "runtime/skilltree_hook.py"],
    "hash": "sha256:<64 hex>"
  },
  "bundle_hash": "sha256:<64 hex>"
}
```

`migrations` 是唯一的迁移内容清单，不得把 migration 文件混入 `runtime_files`、`wheels` 或 `hook_bundle`。它按 `version ASC` 严格升序；每项只含 `version`、`path`、`sha256`，`version` 必须从 1 连续到 `schema.migration_version`、不重复，`path` 必须精确为 `migrations/%04d_<scope>.sql` 且位于 Plugin 根目录内。数组长度必须等于 `migration_version`，每个列出的文件必须存在、为 UTF-8 SQL regular file、其 SHA-256 精确匹配，且 `migrations/` 目录中不存在未列出、额外、重复版本或非匹配命名的 `.sql` 文件。P0 Manifest 的数组只能有 version 1；后续 Bundle 每次只可在末尾追加连续版本，分别为 P1=`2`、P2=`3`、P3.1=`4`、P3.2=`5`、P4=`6`、P5=`7`、P6=`8`。`bundle_hash` 覆盖 `migrations` 数组。

`hook_bundle.files` 必须恰好覆盖 `hooks/hooks.json` 与其引用的所有 handler；`runtime_files` 与 `wheels` 必须共同覆盖安装、路由和 Hook 运行时所需的所有 Plugin 内文件。P0.1 builder 以临时目录生成 wheel/lock/migration/Manifest，再复制到发布目录；不得将开发工作区、`.venv`、测试 fixture、源代码树、密钥、OCI archive 或 sdist 放入基础 Bundle。它必须拒绝缺文件、重复路径、migration 不连续或 hash 不符、Plugin/Core/Schema 版本不一致、Core wheel 缺失或未锁定依赖。`setup.ps1` 在 venv 创建前先校验 Manifest、migration 清单/文件/hash、requirements.lock 和全部 wheel；只有全部通过才按该清单顺序执行迁移。`doctor` 每次运行复算当前 Plugin 文件、Manifest 的 `bundle_hash`、Core/Schema 版本、全部列出 migration 文件 hash，并核对 SQLite `schema_migrations` 的 `(version,content_hash)` 集合恰好等于该 Manifest 的连续版本范围。任何校验失败均返回 `runtime_ready=false`，不执行网络安装或系统 Python 降级。

P6 Replay Extension Bundle 是独立于 Plugin 的离线安装包，目录固定为 `replay-extension/`，并必须包含 `replay-bundle-manifest.json` 和 `skilltree-replay-runner-<version>.oci.tar`。其 manifest 固定为 `{schema_version:"skilltree-replay-bundle/v1", extension_version, requires:{plugin_version_range,core_version_range,schema_version}, oci_archive:{path,sha256}, image:{name,digest}, bundle_hash}`：所有字段必填、无未知字段；`path` 是相对 Extension Bundle 根目录的规范化路径，`sha256` 与 `digest` 均为 `sha256:<64 lowercase hex>`，`bundle_hash` 对除自身外的 RFC 8785 canonical JSON 计算。P6 的显式 `skilltree replay install-extension --input <absolute-json-file>` 才可接收用户已下载并确认的 Extension Bundle 绝对目录；Core 验证范围、基础 Plugin/Core/Schema 兼容性、archive hash 和本地 Docker 后，用无网络 `docker load` 加载并核验镜像 digest，再以同卷原子替换写 `$PLUGIN_DATA/replay-runtime-state.json`。该 state 固定为 `{schema_version:"skilltree-replay-runtime/v1", extension_bundle_hash, extension_version, runtime_path, image_name, image_digest, installed_at}`，不含用户输入或绝对 Extension Bundle 路径。更新失败保留旧 replay runtime；卸载只删除 replay runtime state 和本地加载镜像引用，不影响基础 Plugin、SQLite、Hook、路由或 Capsule 元数据。P6 测试必须覆盖：未安装扩展、范围不兼容、archive/hash/digest 篡改、Docker 不可用、load 失败保留旧 state、离线成功安装、幂等重装、更新回滚和卸载后 `evolve scan` fail-closed。

P0.2 面向用户的唯一生产安装动作是单独发送下列精确 `UserPromptSubmit` 控制消息：`$skilltree-bootstrap install --python "<absolute python.exe path>"`。`runtime/skilltree_bootstrap.ps1` 是不依赖 venv 的最小 PowerShell Hook handler；它只接受该行完全匹配的消息，`<absolute python.exe path>` 不得含换行、引号或额外参数。任何其他消息（包括带前后文本、自然语言近似请求、相对路径、重复参数或未知参数）不得安装、更新或写入运行时；以 `$skilltree-bootstrap` 开头但不合法的消息必须返回固定 `invalid_bootstrap_request` 并阻止该控制消息进入模型。合法消息是一次明确的用户安装/更新授权：handler 必须解析并验证 Codex 注入的 `$PLUGIN_ROOT`、`$PLUGIN_DATA`，且 `$PLUGIN_ROOT` 必须等于 handler 所在 Plugin 根目录、`$PLUGIN_DATA` 必须为位于该根目录和工作区之外的规范化可写绝对路径。随后 handler 仅以参数数组调用内部 `scripts/setup.ps1 -PluginData $env:PLUGIN_DATA -PythonPath <validated path>`，禁止经 shell 拼接或执行用户提供的其他文本。`-PluginData` 是安装器内部必填参数，不是用户公开接口；P0 测试可直接调用脚本，但只可使用测试进程创建的临时目录，不能据此宣称生产安装可用。

`PythonPath` 始终由用户在每次安装/更新动作中自行选择，必须是 Python 3.10+ 的规范化绝对 `python.exe` 路径、可导入 `venv`；它只用于创建专用 venv，绝不成为 SkillTree、Hook 或 Doctor 的回退解释器。Bootstrap handler 不得持久化、回显或写审计该原始路径；其失败输出只含固定 reason code。安装器不读取当前工作目录，也不使用 `py`、`PATH`、系统 site-packages 或网络。

安装器必须独立重复验证 `PluginData`：它规范化后不得等于、包含或位于 `$PLUGIN_ROOT`、工作区或用户 Skill 根目录中；测试直调时还必须是测试进程创建的临时目录。安装前先用 bootstrap Python 校验 `skilltree-bundle/v1` Manifest、`migrations` 的完整连续集合及其每个文件 hash、全部 wheel、`requirements.lock` 与 hook bundle。通过后在 `$PLUGIN_DATA/install-staging/<random>/venv` 创建默认不带 system site-packages 的 venv，并仅以该 venv Python 执行 `pip install --no-index --require-hashes --find-links <PLUGIN_ROOT>/runtime/wheels -r <PLUGIN_ROOT>/requirements.lock` 和 `python -I -c "import skilltree"` smoke check。不得在正式 `$PLUGIN_DATA/venv` 内就地安装。smoke check 通过后、切换 venv/state 前，安装器必须只用暂存 venv 以参数数组调用 `skilltree storage initialize --data-dir <PluginData> --target-schema-version <bundle-manifest.schema.migration_version> --json`：它创建 `$PLUGIN_DATA/skilltree.sqlite3`（若不存在）、启用 foreign keys/WAL/有限 `busy_timeout`，在单一事务内按 Manifest `migrations` 顺序应用完整连续集合并逐份写入相同 version/content_hash 的 `schema_migrations`。P0 Bundle 的 target 固定为 `1`，因此全新 P0 安装只应用 `migrations/0001_p0_runtime.sql`。已存在数据库只允许其已记录迁移是当前 Manifest 清单的未篡改连续前缀、并在此次事务成功补齐到 target 后才返回 `initialized`；数据库不可读、版本更高、缺少/篡改迁移记录、Schema hash 不符或迁移失败必须以 `database_initialize_failed` 终止，不得修复、降级或覆盖既有数据库。全新安装的初始化失败必须删除本次新建数据库及 `-wal`/`-shm`，更新安装则保留原数据库与旧 runtime；任何路径都不得写 runtime-state。

安装成功的唯一基础运行时状态文件为 `$PLUGIN_DATA/runtime-state.json`，固定为 `{schema_version:"skilltree-runtime/v1", plugin_root, plugin_version, core_version, skilltree_schema_version, bundle_hash, hook_bundle_hash, installed_at}`；其中 `plugin_root` 是 `setup.ps1` 从自身 `$PSScriptRoot` 推导出的 `$PLUGIN_ROOT`，必须为规范化绝对路径，且其下必须存在已通过 Manifest 校验的 `runtime/bundle-manifest.json`。它仅用于本地完整性校验，不得进入模型上下文、TraceEvent、导出或外部审计。用户显式运行安装命令即确认该 Plugin 位置；安装后 Plugin 被移动、路径失效、路径解析越界或该路径的 Manifest/Hook hash 不再与 state 一致时，Doctor 必须返回 `runtime_ready=false`，用户只能在新位置再次显式运行 `setup.ps1`，不得通过当前工作目录、`PATH` 或系统 Python 回退定位 Plugin。仅当初始迁移已成功、最终 venv 已切换且该文件以同卷临时文件原子替换写入后才认为运行时可用。运行时切换规则为：保留已验证的旧 `venv` 与旧 state，暂存检查及数据库初始化通过后先将旧 venv 移入 `$PLUGIN_DATA/rollback/<random>/`，再把暂存 venv 移为正式 venv，最后写入新 state；任一切换或 state 写入失败必须恢复旧 venv/state。切换窗口内 Hook 只 fail-open。成功后可删除旧 rollback；删除失败只留下不可执行备份，不影响新 runtime。基础安装器绝不读取、加载或要求 Replay Extension Bundle，也不创建 `replay-runtime-state.json`。

当现有 `runtime-state.json` 的 `bundle_hash` 与当前 Manifest 相同，且正式 venv 可通过隔离 import smoke check 时，脚本不得重建 venv，返回 `already_installed`。用户显式再次运行同一命令且 Bundle hash 不同时，脚本自动执行上述暂存更新；暂存、pip、smoke 或切换失败时保留原先已验证 runtime。若原运行时本就无效，失败后不得把它标为可用。

脚本 stdout 固定输出一个 JSON 对象：成功为 `{schema_version:"skilltree-setup/v1", status:"installed|already_installed", bundle_hash}`，失败为 `{schema_version:"skilltree-setup/v1", status:"failed|rolled_back", error:{code, message}}`；`error.code` 只能是 `invalid_argument|invalid_bootstrap_python|bundle_validation_failed|offline_install_failed|smoke_check_failed|database_initialize_failed|runtime_switch_failed`，`message` 不得包含用户数据、凭据或未规范化路径。退出码固定为 `0`（installed/already_installed）、`2`（参数或 bootstrap Python 无效）、`3`（Bundle 校验失败）、`4`（暂存/离线 pip 失败）、`5`（smoke check 失败）、`6`（数据库初始化失败）、`7`（切换失败，可能已 rollback）。Bootstrap 成功时只返回 `skilltree_bootstrap_installed|skilltree_bootstrap_already_installed`，失败时只返回 `skilltree_bootstrap_failed:<error.code>`。P0.2 测试必须覆盖：相同 Bundle 幂等、变更 Bundle 更新、干净目录创建并事务应用 migration 1、已有一致 migration 幂等、数据库不可读/高版本/DDL hash 不符/迁移失败时不得写 runtime-state、缺失/额外/跳号/路径越界/篡改 migration 文件或 Manifest migration hash、缺失/篡改 wheel、错误 PythonPath、PluginData 越界、非法/带额外文本的 bootstrap 消息不得安装、合法 bootstrap 消息阻止进入模型且不产生 TurnTrace、pip 失败保留旧 runtime、切换失败恢复旧 runtime、安装后 Plugin 移动或替换导致 Doctor 失败，以及断网/无 system site-packages 安装。

`hooks/hooks.json` 必须只配置 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 的 command handler；`PostToolUse` 的 matcher 覆盖 `Bash|apply_patch|mcp__.*` 和已验证的本地 function Tool 名，禁止把 Hosted Tool 声明为已覆盖。`UserPromptSubmit` 配置普通 `$PLUGIN_DATA/venv/Scripts/python.exe` handler 与独立的 `$PLUGIN_ROOT/runtime/skilltree_bootstrap.ps1` handler；所有 UserPrompt handler 先识别保留 bootstrap 语法，控制消息不得创建 TurnTrace、outbox、审计或 token。Bootstrap 对非控制消息必须在 1 秒内零输出退出；对合法控制消息的 timeout 为 300 秒，完成后以 JSON `{decision:"block", reason:"skilltree_bootstrap_installed|skilltree_bootstrap_already_installed|skilltree_bootstrap_failed:<error.code>"}` 阻止该控制消息进入模型，其中 `<error.code>` 只能取第 P0.2 节列出的六个安装器错误码；reason 不得带路径、用户文本或凭据。普通 UserPrompt、PreToolUse、PostToolUse 的 timeout 固定为 1 秒，Stop 为 3 秒。Windows handler 使用 `commandWindows`；不得依赖当前工作目录。Hook 只从 stdin 读取 Codex JSON，并仅向 `$PLUGIN_DATA` 写已脱敏 outbox/审计数据。超时、清洗失败、环境缺失或 outbox 故障均 fail-open，不得改写输入、审批、Tool 结果或模型上下文（除合法 bootstrap 阻止自身控制消息，以及 UserPromptSubmit 返回 turn_token 外）。

安装不允许隐式联网或隐式创建环境。发布构建必须将纯 Python `skilltree-core` wheel 与所有运行时依赖 wheel 放入 `runtime/wheels/`；`runtime/bundle-manifest.json` 固定 Plugin/Core/Schema 版本、wheel 文件名与 SHA-256，并固定 hook bundle 的包含文件清单、规范化规则与 `hook_bundle_hash`。只有合法 bootstrap 控制消息经用户明确授权后，安装器才在 Codex 注入的 `$PLUGIN_DATA/venv` 创建专用虚拟环境，并以绝对 Plugin 路径执行 `pip install --no-index --require-hashes --find-links <PLUGIN_ROOT>/runtime/wheels -r <PLUGIN_ROOT>/requirements.lock`。`requirements.lock` 必须列出 Core wheel 与每个依赖的精确版本和 hash；不得访问网络、构建 sdist、依赖系统 site-packages 或从工作区安装。失败时保持 Hook 不可用且不降级为系统 Python。

`skill-router` 不直接执行 `doctor`；Plugin `UserPromptSubmit` Hook 在向模型注入路由上下文前，以专用 venv 执行 `skilltree doctor --json`。Doctor 纯只读：不得创建目录、迁移数据库、修复 state、创建 venv、写审计、调用网络或使用 bootstrap/system Python。它先解析 `runtime-state.json` 的 `plugin_root`，再按固定顺序校验 `runtime_state`、`venv_python`、`bundle_manifest`、`versions`、`schema_migrations`、`hook_bundle`、`hook_observation`；其中 `bundle_manifest` 还必须验证 `migrations` 项的名称、连续性、路径边界和文件 hash，`schema_migrations` 再验证数据库的 `(version,content_hash)` 与该已验证清单完全相等。前六项任一失败仍须返回已完成检查的结果，但不得尝试后续依赖该失败项的文件读取。`hook_observation` 的最低可用 migration 为 `5`：当已验证 Manifest 的 `migration_version < 5` 时，它固定返回 `{name:"hook_observation",state:"unknown",code:"hook_unconfirmed"}`，不得尝试查询尚不存在的 `hook_observations` 表，也不得使 `runtime_ready=false`；从 migration `5` 起才查询该表并按实际当前 hash 判断 `pass/observed` 或 `unknown/hook_unconfirmed`。P6 的 `skilltree doctor --replay-json` 在上述常规只读检查通过后，额外按顺序检查受控 OCI runtime 绝对路径、Client/Server version、本地 `replay-runtime-state.json`、镜像名称和 digest；它不得加载/拉取镜像、运行容器、创建 staging 或调用网络。stdout 只输出下列对象，stderr 只供人工诊断，且不得含凭据、用户输入、prompt、token 或未规范化绝对路径：

```json
{
  "schema_version": "skilltree-doctor/v1",
  "runtime_ready": true,
  "diagnostic_state": "ready",
  "checks": [
    {"name": "runtime_state", "state": "pass", "code": "ok"},
    {"name": "venv_python", "state": "pass", "code": "ok"},
    {"name": "bundle_manifest", "state": "pass", "code": "ok"},
    {"name": "versions", "state": "pass", "code": "ok"},
    {"name": "schema_migrations", "state": "pass", "code": "ok"},
    {"name": "hook_bundle", "state": "pass", "code": "ok"},
    {"name": "hook_observation", "state": "pass", "code": "observed"}
  ],
  "hook_observation_state": "observed",
  "current_hook_bundle_hash": "sha256:<64 hex>",
  "last_observed_at": "RFC3339 UTC"
}
```

顶层字段与七个 `checks` 均必填，`checks` 必须保持上述顺序且每项只含 `name`、`state`、`code`。`state` 只能为 `pass|fail|unknown`；仅 `hook_observation` 允许 `unknown`，其 `code` 必为 `hook_unconfirmed`。其余检查失败时使用确定 code：`runtime_state_missing|runtime_state_invalid|plugin_root_invalid`、`venv_python_missing|venv_python_mismatch`、`manifest_missing|manifest_invalid|bundle_hash_mismatch|file_hash_mismatch|migration_manifest_invalid|migration_file_missing|migration_file_hash_mismatch`、`core_import_failed|version_mismatch`、`database_missing|database_unreadable|migration_mismatch`、`hook_bundle_missing|hook_bundle_mismatch`。`current_hook_bundle_hash` 为可重算时的当前值，否则为 `null`；当 migration `< 5` 时 `last_observed_at` 必为 `null`；从 migration `5` 起它只在当前 hash 存在未过期 `hook_observations` 记录时为 RFC3339 UTC，否则为 `null`。当且仅当前六项均为 `pass` 时 `runtime_ready=true`；其后 `hook_observation=pass` 时 `diagnostic_state="ready"`、退出码 `0`，仅该项为 `unknown` 时 `diagnostic_state="degraded"`、退出码 `1`；任何其余失败均为 `diagnostic_state="failed"`、退出码 `2`。`runtime_ready=false` 不代表可修复操作已执行。`--replay-json` 的 replay checks 必须全部 `pass` 才返回 `replay_ready=true`；固定失败 code 为 `replay_runtime_path_invalid|replay_runtime_unavailable|replay_runtime_state_missing|replay_image_missing|replay_image_digest_mismatch`，任一失败不得被普通 Doctor 的 `runtime_ready` 掩盖。

Codex 没有供 Plugin/Core 查询 `/hooks` 信任状态的接口，因此 `doctor` 不得声称 Hook 已信任；只有当前 hash 命中未过期 `hook_observations` 才报告 `observed`，否则为 `unknown`。依赖 Hook 关联的 trace/learning/replay 操作遇到 `unknown` 返回 `hook_unconfirmed`；本地 Top-K 路由及用户主动的画像管理仍可用。

首次安装、每次 Hook 内容哈希变化和每次 Plugin 更新后，用户必须在 Codex `/hooks` 中审阅并信任该 Hook；更新时通过缓存破坏版本后缀重新安装 Plugin，并在新线程执行验证。`trace_capture_enabled=false` 为默认值；只有用户明确执行管理命令启用后，Hook 才创建 TurnTrace。该开关独立于 `memory_read_enabled` 和 `memory_write_enabled`，关闭时 Hook 必须成功快速退出且不写数据。

G0.5 是 TurnToken Compatibility 的人工 Gate，不是 `auto-coder` 可执行任务，必须在 P3.1 后、P3.2 前通过。用户在真实 Codex 新线程中用最小 Plugin fixture 分别验证显式 `$skill-router`、自然语言触发、连续多轮、上下文 compact、子代理五个场景；每个场景连续运行至少 3 次。证据写入 `docs/verification/G0.5-turn-token-compatibility.md`，只记录场景编号、Plugin/Core/Schema 与 Hook content hash、session/turn/token 的 hash、run_id、bind 结果、时间和脱敏诊断，不得记录 prompt 或 token 原文。全部样本正确关联且无跨 turn 关联才通过；用户人工审阅证据后将 Gate 标为 `[x]`，并把 P3.2 从 `[-]` 解锁为 `[ ]`。任一样本失败、证据缺失或 Hook 不可用时 Gate 保持 `[!]`、P3.2 及其依赖任务保持 `[-]`，项目锁定为仅路由模式。

#### Open-source Release Contract

首次公开发布前，仓库根目录必须包含 `LICENSE`、`README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`PRIVACY.md`、`SUPPORT.md`、`CHANGELOG.md` 与发布时生成的依赖 SBOM。未选择 `LICENSE` 前不得声称项目可复用开源。

- README 必须说明 Hook 覆盖与盲区、默认关闭的 trace/memory/replay、安装/更新/卸载与 `/hooks` 信任步骤。
- SECURITY 必须给出漏洞报告渠道、支持版本、Hook 信任风险和 ReplayCapsule 风险；PRIVACY 必须列出 SQLite、outbox、ReplayCapsule、审计的内容、位置、默认状态、TTL、导出与删除语义。
- SUPPORT 必须提供 Codex 客户端、Windows、Python、Hook 可用性和支持/不支持 Tool 的兼容矩阵；CONTRIBUTING 必须禁止提交真实 prompt、凭据、Replay blob、SQLite 数据、outbox 或含敏感输出的 Hook fixture。
- 发布 CI 必须验证 lock 文件、生成 SBOM、运行 Plugin validator 与 Hook fixture 测试；真实 Codex 验收记录必须独立保存，不得作为 CI 通过的替代证据。
- Plugin、Core、Schema 和数据库迁移各自版本化；不兼容迁移发布前必须提供备份、升级、回滚和数据清除说明。
