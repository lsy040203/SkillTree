## 1. 项目概述

SkillTree 是本地优先、单用户的开源 Codex Plugin。它以 Plugin-bundled Codex lifecycle hooks 采集受支持的实际本地 Tool 调用，以 `skill-router` Skill 管理并路由用户可写的 Codex 技能；当前 Codex 会话模型完成结构化意图识别与候选重排，Python Core 负责确定性约束、SQLite 持久化、轨迹、用户授权记忆和受控演进。

项目目标是形成可审计闭环：自然语言请求 → 受支持 Tool 的实际轨迹 → 成功、失败、取消或未知 Episode → 权重、程序化记忆和 `SKILL.md` 改进候选。项目不自动执行推荐 Skill、不自动写入技能文档，也不替代 Codex 沙箱、Hook 信任审阅与用户审批。
