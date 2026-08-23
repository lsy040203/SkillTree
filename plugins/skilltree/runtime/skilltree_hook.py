"""Fail-open Plugin process boundary for the host-neutral Core Hook bridge."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

try:
    from skilltree.hook_bridge import handle_hook_event
    from skilltree.storage import Database
except ImportError:  # The wrapper must remain harmless before setup succeeds.
    handle_hook_event = None
    Database = None


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin_bytes: bytes | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Invoke Core once and forward only its approved stdout string."""
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        environment = os.environ if environ is None else environ
        event_name = arguments[0] if len(arguments) == 1 else ""
        plugin_data = environment.get("PLUGIN_DATA")
        plugin_root = environment.get("PLUGIN_ROOT")
        if event_name not in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}:
            return 0
        if not plugin_data or not plugin_root or handle_hook_event is None or Database is None:
            return 0
        raw = sys.stdin.buffer.read(32 * 1024 + 1) if stdin_bytes is None else stdin_bytes
        code, output = handle_hook_event(
            event_name,
            raw,
            data_dir=Path(plugin_data),
            plugin_root=Path(plugin_root),
            database=Database(Path(plugin_data) / "skilltree.sqlite3"),
        )
        if output:
            sys.stdout.write(output)
        return 0 if code is None else int(code)
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
