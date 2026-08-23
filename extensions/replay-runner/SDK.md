# SkillTree Replay Adapter SDK

An adapter receives one `skilltree-replay-task/v1` JSON request and writes one
`skilltree/v1` `result.json` to `/artifacts`. Extensions must declare a
namespaced task type (for example `com.example.python.repository_verification`)
in a v2 manifest. The Core pins the extension image digest before running both
baseline and candidate arms.

Adapters are fixture-only: no network, credentials, arbitrary commands, or
host workspace mounts. New task types belong in an independently installed OCI
Extension Bundle and must not modify `SKILL.md` automatically.
