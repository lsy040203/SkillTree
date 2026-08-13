# P0.1 实现顺序与接口

状态：`ready_for_user_acceptance`

1. 用 `tests/test_bundle_contract.py` 冻结 Manifest、wheel、lock 和 migration 的 P0 不变量。
2. `build_bundle(repository_root)` 使用标准库直接生成固定 ZIP 元数据的纯 Python wheel；不依赖 pip、setuptools、wheel 或 PEP 517 build backend。
3. 构建器写入精确单行 hash lock，生成带 `schema.migration_version=1` 的 `skilltree-bundle/v1` Manifest。
4. `validate_bundle(plugin_root)` 不执行 artifact：拒绝未知字段、路径越界、hash/版本不匹配、非连续 migration、非 wheel runtime artifact 和错误 Hook file set。
5. `tools/build_bundle.py` 作为实际构建命令调用该接口；构建后再次运行 `validate_bundle`。

输入为仓库根目录或 Plugin 根目录；输出为发布目录中的 wheel、lock、migration 和 Manifest。失败以 `BundleValidationError` 终止；P0.2 再将其映射到安装器的机器可读错误码。
