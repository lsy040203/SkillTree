# P0.1 存储与迁移

状态：`ready_for_user_acceptance`

本阶段新增 migration `migrations/0001_p0_runtime.sql`。它创建 `schema_migrations`、`runtime_config` 和 `audit_events`，以及仅用于审计保留期扫描的 `idx_audit_events_retention`；安装器和 SQLite 初始化属于 P0.2，P0.1 不在开发数据目录创建运行数据库。

| 表 | 主键/约束 | P0 语义 |
| --- | --- | --- |
| `schema_migrations` | `version` 主键 | P0.2 记录已成功应用的 migration content hash。 |
| `runtime_config` | `config_id=1` 单行约束 | 初始四个授权开关均为 0；skill root 初始为空。 |
| `audit_events` | `audit_id` 主键 | 仅存脱敏事件元数据与保留期。 |

`runtime/bundle-manifest.json`、`requirements.lock` 与 `runtime/wheels/*.whl` 是只读发布 artifact，不是 `$PLUGIN_DATA` 中的运行时可变数据。
