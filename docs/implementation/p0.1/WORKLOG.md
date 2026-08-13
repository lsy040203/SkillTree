# P0.1 工作日志

状态：`ready_for_user_acceptance`

## 目标

交付可离线校验的 SkillTree 基础 Plugin Bundle：P0 wheel、迁移 `0001`、带 SHA-256 的 lock 与 Manifest。

## 实际完成

- 已确认任务状态为 `P0.1 [~]`，且不存在前置 `BLOCKED_BY`。
- 新增 `src/skilltree/bundle.py`：构建纯 Python Core wheel，并对 Bundle 的版本、hash、migration、lock、wheel 与 Hook bundle 执行纯静态校验。
- 新增 `tools/build_bundle.py`，实际构建了 `runtime/wheels/skilltree_core-0.1.0-py3-none-any.whl`、`requirements.lock` 和 `runtime/bundle-manifest.json`。
- 新增 `migrations/0001_p0_runtime.sql`，只定义 P0 的 `schema_migrations`、`runtime_config`、`audit_events` 及 `idx_audit_events_retention`。
- 新增 `tests/test_bundle_contract.py`；先因不存在 `skilltree.bundle` 失败，后在实现后通过。

## 实际命令与结果

| 命令 | 结果 |
| --- | --- |
| `py -m pytest -q tests/test_bundle_contract.py`（首次） | 预期失败：`ModuleNotFoundError: No module named 'skilltree.bundle'`。 |
| `C:\Users\Lenovo\AppData\Roaming\uv\python\cpython-3.14.6-windows-x86_64-none\python.exe tools\build_bundle.py` | 成功；最终 Bundle hash 为 `sha256:4986fc1141f7931dfe531354eb603fb2b68eaca97e74d064fcbb1f53c390010a`。 |
| `$env:PYTHONPATH=(Join-Path $PWD 'src'); py -m pytest -q` | 16 passed。 |
| `py C:\Users\Lenovo\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py plugins\skilltree` | `Plugin validation passed`。 |
| `git diff --check` | 通过。 |

## Artifact 清单

| Artifact | SHA-256 |
| --- | --- |
| `runtime/wheels/skilltree_core-0.1.0-py3-none-any.whl` | `sha256:6d3e3d4a5b6c2498862b1ffc0f973a06834941f8ecfb4add518febb6adc29edb` |
| `migrations/0001_p0_runtime.sql` | `sha256:91118602d167e6f6d3e3281d301344b1f76947758b4ce3bd92fa073b99a9defc` |
| `requirements.lock` | `sha256:792b058c3002daeef10bb4b545f80cd17b6cc7a4ad3e043fe606bd762440093b` |
| `runtime/bundle-manifest.json` | 由其内部 `bundle_hash` 保护，见上。 |

## 验证边界与风险

- 已验证：Bundle 仅声明一个 Core wheel，未出现 sdist 或 OCI archive；每个声明文件和 aggregate Bundle hash 可复算；篡改 migration 会在任何安装步骤前被拒绝。
- 已验证：同一源目录连续执行构建产生相同 Core wheel hash 与 Bundle hash；构建器使用固定 ZIP 元数据，避免时间戳改变发布 hash。
- 已验证：用户提供的 uv CPython 3.14.6 环境没有 `setuptools`/`wheel`，仍可成功构建；Core wheel 由标准库直接生成，不依赖 pip 或 PEP 517 backend。
- 已验证：使用该 uv Python 创建的临时 venv 能以 `pip install --no-index --require-hashes --find-links ... -r requirements.lock` 安装，并通过 `python -I -c "import skilltree"` 输出 `0.1.0`。该临时目录保留在 `C:\Users\Lenovo\AppData\Local\Temp\skilltree-p01-uv-verify-dbe28595a7ae4b788d39035d351edd62` 供人工检查。
- 已知开发环境差异：工作区 `.venv` 缺少 `pytest`，所以使用可用的 `py -m pytest` 并设置 `PYTHONPATH=src` 运行源码测试；发布 Bundle 不包含 pytest 或任何开发依赖。
