## 3. 技术栈与约束

- Python 3.10+，以 `py` 作为 Windows 解释器入口。
- SQLite：本地注册表、事件、画像、候选与发布状态。
- 标准库优先；frontmatter 使用安全 YAML 解析器，禁止自定义对象构造。
- `pytest` 作为测试框架；模型行为在 CI 中用 Fake/fixture JSON。
- 首期没有外部 LLM API、云端数据库、向量数据库或 Mem0 依赖。
- Plugin 是分发边界；首期使用 Plugin-bundled command Hook 采集受支持的本地 Tool 事件，MCP 仅预留适配器接口。
- 运行时只使用 `$PLUGIN_ROOT`（只读发行内容）与 `$PLUGIN_DATA`（专用可写数据）；不得把数据库、outbox、venv 或 Hook 生成文件写入工作区、用户 Skill 目录或 Plugin 根目录。
- P0 必须在项目内安装或复制受版本锁定的 `.github/skills/auto-coder/`，包含 `scripts/sync_spec.py`；首次执行 `py .github/skills/auto-coder/scripts/sync_spec.py --force` 并验证生成 7 个章节参考文件后，才允许开始 P1。
- P6 仅支持 Windows 上由 Doctor 确认的本地 Docker Engine OCI 隔离；Docker Desktop 的 Linux containers 模式、离线 `docker load`、已安装 Replay Extension Bundle 与固定 runner image digest 是 P6 前提。缺失任一前提时 replay/evolution 功能不可用而其余 Plugin 功能保持可用；不得将 Docker socket、远程 Docker context 或 Windows containers 视为等价实现。
