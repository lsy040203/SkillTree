# P0.1 里程碑

状态：`ready_for_user_acceptance`，将与 `DEV_SPEC.md` 的 `[x] [P0.1]` 一致；尚未创建提交。

| 子任务 | 状态 | 验收标准 |
| --- | --- | --- |
| 留痕骨架 | 完成 | 六份必需文件存在且未把计划写成完成事实。 |
| Bundle 失败测试 | 完成 | 测试先因 `skilltree.bundle` 缺失失败。 |
| 最小构建器与 artifact | 完成 | 只生成 P0 所需 wheel、lock、migration 与 Manifest。 |
| Bundle 校验与全量测试 | 完成 | hash、版本、迁移、无 sdist/OCI、重复构建稳定性和无 setuptools 的 uv Python 构建自动验证；16 tests passed。 |
| 规格同步与用户验收包 | 进行中 | 同步后的任务状态和留痕与事实一致；不自动提交。 |

阻塞项：无。下一人工动作：用户验收 P0.1 并决定是否提交；P0.2 的端到端离线安装在后续阶段执行。
