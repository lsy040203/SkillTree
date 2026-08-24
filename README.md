# Lumos My Codex

<p align="center">
  <img src="docs/assets/lumos-skilltree-poster.png" alt="Lumos My Codex：SkillTree 项目海报" width="720" />
</p>

<p align="center"><strong>面向 AI Agent 的本地化技能智能与持续进化系统</strong></p>
<p align="center">Local-first · Auditable · Evolve</p>

Lumos My Codex（项目代号：SkillTree）是一个隐私优先、本地运行、可审计的 Codex Plugin。它连接技能管理、任务路由、执行轨迹、反馈学习、授权记忆和受控演进，为 Agent 提供一层属于用户自己的 **Agent Intelligence Layer**。

图标中的金色光核象征被汇聚、被照亮的技能能力：技能不再是散落在目录里的静态文档，而是在用户授权下可发现、可路由、可观察、可复盘，并逐步形成更可靠工作流的能力单元。

## 为什么需要 SkillTree？

传统 Agent 往往只展示最终答案。用户很难知道 Agent 选择了哪些 Skill、实际调用了哪些 Tool、哪些步骤真正执行，以及一次成功经验能否在下一次任务中安全复用。

SkillTree 通过 Skill Registry、Skill Router 和 Codex Lifecycle Hook，把一次任务转化为可分析的 Execution Trace 与 Episode，再将经过授权和验证的经验沉淀为权重、Profile、Procedure 以及受控演进候选。

## 核心闭环

```text
自然语言请求 → Skill 自动匹配 → Tool Chain 执行 → 轨迹记录
      → 反馈学习 → 工作流沉淀 → Skill 优化候选 → 下一轮更优执行
```

每次执行都是能力进化的证据，但不是每次执行都自动改变系统。只有满足覆盖完整、来源可信、用户授权和回放验证等条件的数据，才允许进入学习或演进流程。

## 六阶段能力路线

| 阶段 | 能力 | 目标 |
| --- | --- | --- |
| **P1** | Skill Registry · 技能注册 | 扫描用户可写的 `SKILL.md`，建立 `pending → trusted / blocked` 生命周期。 |
| **P2** | Skill Router · 技能路由 | 理解任务意图，从可信 Top-K 候选中选择并排序 Skill。 |
| **P3** | Execution Trace · 执行追踪 | 通过 Codex Lifecycle Hook 记录实际 Tool 链路与 Episode。 |
| **P4** | Feedback Learning · 反馈学习 | 根据显式选择、拒绝、改选和可信结果更新 Skill 权重。 |
| **P5** | Memory System · 授权记忆 | 将稳定偏好沉淀为 Profile，将可复用做法沉淀为 Procedure。 |
| **P6** | Skill Evolution · 技能演进 | 通过隔离回放比较基线与候选，生成带证据、风险和回滚方案的补丁候选。 |

## 设计原则

- **本地优先**：运行数据保存在本地 Plugin data 目录，不上传 prompt、凭据或 SQLite 文件。
- **可审计**：区分模型推荐、计划语义和实际 Tool 调用；没有可信结果时保持 `unknown`。
- **授权优先**：`pending` 或 `blocked` Skill 不进入候选集；记忆写入必须经过用户批准。
- **受控演进**：只生成候选，不自动修改 `SKILL.md` 或发布；首期只允许 `draft → replay_passed`。

## Privacy and safety

Raw prompts, credentials, tokens, SQLite contents, outbox payloads, and replay artifacts are not release inputs and must not be committed. Memory candidates require explicit approval before authoritative storage. See [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md), and [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

## Install from GitHub

The repository currently provides a validated pre-release branch rather than a numbered GitHub Release. To install it in Codex, add the repository as a marketplace and install the plugin:

```powershell
codex plugin marketplace add lsy040203/SkillTree --ref codex/release-foundation
codex plugin add skilltree@skilltree
```

To use a local checkout instead:

```powershell
codex plugin marketplace add "D:\path\to\SkillTree"
codex plugin add skilltree@skilltree
```

Restart Codex after installation, then verify with `codex plugin list`.

On the first Codex turn after installation, initialize the local runtime by sending this exact request (replace the Python path with an installed Python 3.11+ executable):

```text
$skilltree-bootstrap install --python "C:\\Users\\Lenovo\\AppData\\Local\\Programs\\Python\\Python311\\python.exe"
```

After the bootstrap reports success, run `skilltree doctor --json` in the same environment. Runtime data stays in the local Plugin data directory; prompts, credentials, and SQLite files are not uploaded to GitHub.

## 当前状态

当前仓库提供经过验证的预发布分支，而不是编号 GitHub Release。Plugin 运行时和发布边界已建立，版本变化见 [`CHANGELOG.md`](CHANGELOG.md)。

项目不替代 Codex agent loop，不执行任意命令，不自动编辑 `SKILL.md`，也不自动发布 Skill。

## 文档入口

- [`plugins/skilltree/README.md`](plugins/skilltree/README.md)：Plugin 运行时与使用说明
- [`CHANGELOG.md`](CHANGELOG.md)：版本变化记录
- [`PRIVACY.md`](PRIVACY.md)：数据范围、位置和治理
- [`SECURITY.md`](SECURITY.md)：安全边界与运行时约束
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：贡献与发布要求
- [`docs/assets/lumos-skilltree-poster.png`](docs/assets/lumos-skilltree-poster.png)：Lumos 项目海报

## 技术栈与边界

- Python 3.11+、SQLite、本地 Plugin data 目录；
- 标准库优先，`pytest` 用于测试；
- 首期不依赖外部 LLM API、云端数据库、向量数据库或 Mem0；
- Hook 未信任、未启用或覆盖不足时，事件标记为 `unobserved`、`partial` 或 `unattributed`，不得产生因果学习结论；
- 回放默认禁网、只读输入并设置资源限制；异常统一返回 `unknown` 并记录 guardrail breach。

---

**Observe. Learn. Evolve.**
让每一次执行，都成为下一次能力进化的基石。
