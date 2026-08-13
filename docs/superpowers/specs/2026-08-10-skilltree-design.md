# SkillTree 技术设计

**状态：** 已完成设计评审，待书面规格审阅
**日期：** 2026-08-10
**范围：** 本地单用户 Codex Plugin；以 `skill-router` Skill 为入口，管理用户已安装技能并进行受控学习与演进。

## 1. 目标与非目标

SkillTree 是一个本地优先的开源 Codex Plugin。它扫描用户可写的 Codex 技能目录，建立技能注册表；由当前 Codex 会话模型理解自然语言、重排受限候选；记录经过路由器编排的多 Skill/Tool 轨迹；在用户授权下提取用户画像和程序化记忆；依据可审计证据生成技能文档改进候选。

首期目标：

- 只扫描 `C:\Users\Lenovo\.codex\skills` 的 `SKILL.md`；保存名称、描述、真实路径、内容哈希、更新时间和状态。
- 支持普通自然语言自动触发和 `$skill-router <请求>` 显式触发。显式指定其他 Skill 时，路由器不拦截。
- 当前 Codex 模型输出结构化意图，并只对 Core 给出的 Top-K 可用候选重排。
- 记录推荐、实际 Skill/Tool 调用、成功/失败/取消/未知结果及用户反馈。
- 用户显式授权后，提取可查看、可编辑、可删除的 L1 用户画像与经批准的程序化记忆。
- 对跨 Episode 的重复成功、稳定顺序和重复失败生成带证据的 `SKILL.md` 补丁候选；绝不自动写入。

首期非目标：跨用户或云端数据、自动执行推荐 Skill、自动收集绕过路由器的全局 Codex 事件、自动应用补丁、MCP 服务、canary 自动放量。

## 2. 产品形态与目录

Plugin 是发布和安装边界，Skill 是 Codex 内的工作流入口，Python Core 是可离线测试的业务内核。后续可在不改变 Core 数据模型的前提下增加 MCP 或 hooks。

```text
skill_tree/
├─ DEV_SPEC.md
├─ docs/superpowers/specs/2026-08-10-skilltree-design.md
├─ packages/
│  ├─ skilltree-core/              # 注册、候选、审计、记忆、演进
│  └─ skilltree-cli/               # scan / route / trace / profile / replay 命令
├─ codex-plugin/
│  ├─ .codex-plugin/plugin.json
│  └─ skills/skill-router/SKILL.md
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
└─ docs/
```

`skill-router` 只负责模型侧的结构化推理、读取候选、重排、解释和调用观测命令；它不承载持久化业务规则。Core 不调用外部模型 API。

## 3. 架构与运行时流程

```text
用户请求
  → 已批准 L1 Profile / 程序化记忆的冻结快照
  → skill-router
      → 当前 Codex 模型：结构化意图
      → Core：可用技能 Top-K 预筛
      → 当前 Codex 模型：候选重排、理由和多 Skill 计划
  → 受控执行 / 观测适配器
      → TraceEvent：实际 Skill/Tool 事件
  → 用户结果

任务收尾
  → Episode：目标、意图、实际序列、结果、反馈
  → 授权记忆提取 / 权重更新 / Pattern 与顺序约束
  → MemoryCandidate / EvolutionCandidate / 审计事件
```

检索使用“先启动、后有限等待”的原则：Profile 与已批准程序化记忆只在预算内读入；超时或错误时本轮不注入，但路由继续。注入内容在本轮开始时冻结；本轮产生的新画像或记忆只在下一轮可见。

独立 Skill 不能天然监听所有绕过它发生的 Codex 调用。因此首期的完整轨迹保证范围是：经过 `skill-router` 编排、并调用观测适配器的实际事件。模型计划调用不能记作已执行。全局采集仅作为未来 Plugin/MCP/hook 的研究项。

## 4. 数据模型

全部持久化在项目数据目录的 SQLite 中。`workspace_id` 是项目根路径哈希；单用户首期使用本地匿名 `user_id`，但保留字段以支持扩展。

| 实体 | 关键字段 | 责任 |
|---|---|---|
| `SkillRecord` | name, description, path, content_hash, status, scanned_at | 注册表和变更检测 |
| `RouteRun` | request 摘要, intent JSON, top_k, selected, reason, status | 路由审计 |
| `TraceEvent` | run_id, sequence, type, skill/tool, 参数与结果摘要, duration | 实际轨迹 |
| `Episode` | objective, intent, skill_sequence, tool_sequence, verdict, trace_state | 任务复盘 |
| `FeedbackEvent` | selected/rejected/overridden/outcome | 可观察反馈 |
| `SkillWeight` | workspace_id, intent_type, skill_name, score, updated_at | 可解释偏好 |
| `UserProfileField` | namespace/key, value, source, confidence, state | L1 用户画像 |
| `MemoryCandidate` | kind, payload, evidence_refs, status | 未激活记忆 |
| `EvolutionCandidate` | patch, baseline_hash, evidence_refs, risk_tier, status | 技能文档改进候选 |
| `Release` | stage, traffic_percent, metrics, actor | 未来发布治理 |

`Episode.verdict` 只能是 `success`、`failed`、`cancelled` 或 `unknown`。失败必须记录发生阶段、错误类别、已脱敏摘要和相关能力；`unknown` 绝不能计入成功率。`trace_incomplete` 的 Episode 不得进入演进样本。

## 5. 路由与多 Skill 轨迹

1. `registry` 仅解析 frontmatter 和有限元数据，生成可用 `SkillRecord`。
2. `candidate` 使用本地字段索引、别名和历史权重，筛出 Top-K；只允许 `status=available` 的候选。
3. `skill-router` 要求当前 Codex 模型生成 Schema 合法的意图 JSON；Core 验证后把候选交给模型重排。
4. 模型返回的 `selected_skill_name` 必须属于 Top-K。否则降级为本地排序并标记 `model_output_invalid`。
5. 每次推荐写入 `RouteRun`。执行期间观测适配器按序写入 `skill_loaded`、`tool_started`、`tool_finished`、`tool_failed`、`outcome` 等事件。
6. 任务结束把实际序列和结果组装为 `Episode`。明确选择、拒绝、改选和可信结果才更新权重；一次推荐本身不加成功分。

建议的模型输出契约：

```json
{
  "intent": "repository_analysis",
  "constraints": ["read_only", "chinese"],
  "selected_skill_name": "analyze",
  "ordered_skill_names": ["analyze", "lsp"],
  "confidence": 0.86,
  "reason": "跨文件只读原因定位需要分析工作流，再以语言服务补充符号证据。"
}
```

## 6. 授权记忆与用户画像

`memory_write_enabled` 默认 `false`。关闭时不得抽取、写入或注入画像和程序化记忆；轨迹审计的开关独立于记忆授权。

启用后，模型只产生候选，程序执行字段级清洗与 upsert。L1 Profile 只存稳定且可复用的字段，例如：

- `identity.language`
- `preference.explanation_style`
- `preference.review_depth`
- `routing.preferred_skills`

每个字段有来源、时间、置信度和删除入口。用户可查看、编辑、删除或清空；敏感信息过滤器必须拒绝 API key、令牌、密码和不适合持久化的内容。当前请求始终优先于冻结记忆。

程序化记忆描述“任务类型—能力组合/顺序—适用条件—禁忌/失败条件”，初始为 `MemoryCandidate(draft)`，必须经用户批准后才激活。

## 7. 演进治理

演进从 Episode 证据出发，分别处理成功模式与失败归因：

- 完整 Skill/Tool 序列重复成功：可形成 SOP 或技能文档改进候选。
- 不同成功序列中稳定的局部先后关系：可形成带任务类型边界的顺序约束。
- 重复失败：用于规避同类错误，并生成可能属于 Skill、工具、环境或编排的改进候选；不得固化为成功 SOP。

候选必须包含目标技能、基线文件哈希、有限且已脱敏的 Episode 证据、补丁、风险等级、回放集和回滚方案。状态机定义为：

```text
draft → replay_passed → shadow → canary → active
                       ↘ rejected / rolled_back
```

首期实现 `draft → replay_passed`，即用户审阅补丁且通过历史 Episode 回放比较；`shadow/canary/active` 只定义数据结构和迁移接口。任何模型输出均不可直接写入或覆盖 `SKILL.md`。

## 8. 安全模型

- 扫描内容、技能描述、技能正文、工具输出和用户输入均是不可信数据；扫描绝不执行脚本、命令或链接。
- 模型接收的注册表是有边界的数据；只可在 Core 给出的 Top-K 中选择，不能被描述中的提示注入改写规则。
- 路由器首期不自动执行推荐；实际执行仍受 Codex 沙箱、权限和用户审批约束。
- 解析真实路径并限制在允许扫描根目录，拒绝目录逃逸、符号链接逃逸和异常大文件。
- 补丁只可指向已登记 `SKILL.md`，应用时校验哈希、差异范围、用户批准和回放结果。
- 路由、记忆和演进状态变更写入追加式审计记录及关联哈希。

SkillTree 不能替代 Codex 的沙箱或人工审批；对恶意第三方 Skill 的最后执行授权仍属于 Codex 和用户。

## 9. 错误处理与测试

损坏 frontmatter 只让该技能进入 `invalid` 状态；模型 JSON 无效时安全降级；数据库和记忆错误时以无记忆路由继续；轨迹写入失败时标记 `trace_incomplete` 并禁止演进；补丁基线冲突时拒绝应用。

测试采用单元、集成和固定回放三层：

- 单元：frontmatter、路径边界、索引、Schema、权重、授权门、失败分类、状态机。
- 集成：扫描 → 路由 → Trace/Episode → 画像候选 → 回放 → 演进候选。
- 安全 fixture：恶意 frontmatter、提示注入描述、越界路径、伪造 JSON、敏感信息、哈希冲突和未授权补丁。
- 模型测试使用 Fake/fixture JSON；真实 Codex 手动验证单独记录，不与 CI 的确定性结果混称。

## 10. 首期验收

1. Plugin 安装后，`$skill-router` 可运行并可由自然语言描述自动选择；显式其他 Skill 不被拦截。
2. 仅扫描指定全局技能根目录，完整显示技能元数据与状态。
3. 模型只能从本地 Top-K 选择；无效输出安全降级。
4. 经路由器的成功、失败、取消和未知任务均形成可审计 Episode；不完整轨迹不参与演进。
5. 记忆默认关闭；启用后用户可管理画像，敏感信息不持久化。
6. 可生成具有证据、基线哈希和回放结果的 `SKILL.md` 补丁候选，且永不自动写入。
