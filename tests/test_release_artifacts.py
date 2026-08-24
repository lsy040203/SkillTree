from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCUMENTS = (
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "PRIVACY.md",
    "SUPPORT.md",
    "CHANGELOG.md",
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_required_release_documents_exist_and_are_non_empty() -> None:
    for name in REQUIRED_DOCUMENTS:
        path = ROOT / name
        assert path.is_file(), name
        assert path.read_text(encoding="utf-8").strip(), name


def test_license_and_governance_boundaries_are_explicit() -> None:
    license_text = _read("LICENSE")
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text

    readme = _read("README.md")
    assert "Codex Plugin" in readme
    assert "does not" in readme

    contributing = _read("CONTRIBUTING.md")
    for forbidden in ("prompts", "credentials", "SQLite", "outbox", "Replay", "Hook"):
        assert forbidden.lower() in contributing.lower()

    privacy = _read("PRIVACY.md")
    for term in ("SQLite", "outbox", "ReplayCapsule", "credentials"):
        assert term in privacy

    security = _read("SECURITY.md")
    assert "Security Advisory" in security
    assert "Hook" in security


def test_support_matrix_and_human_gate_are_present() -> None:
    support = _read("SUPPORT.md")
    for term in ("Codex", "Windows", "Python", "Hook", "Tool", "Evidence"):
        assert term in support
    assert "human" in support.lower()

    changelog = _read("CHANGELOG.md")
    assert "Unreleased" in changelog
    assert "unsigned" in changelog.lower()


def test_p7_architecture_declares_release_and_evidence_boundaries() -> None:
    architecture = _read("docs/implementation/p7/ARCHITECTURE.md")
    for term in ("build_bundle.py", "SBOM", "compatibility", "SQLite", "OCI", "human Gate"):
        assert term in architecture


def test_p7_signing_and_codex_acceptance_contracts_are_present() -> None:
    signing = _read("docs/implementation/p7/SIGNING.md")
    acceptance = _read("docs/verification/P7.4-CODEX-ACCEPTANCE.md")
    for term in ("cosign", "private keys", "key-rotation", "signature metadata"):
        assert term.lower() in signing.lower()
    for term in ("doctor --json", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "human gate"):
        assert term.lower() in acceptance.lower()


def test_release_workflow_runs_required_checks_and_redacted_artifacts() -> None:
    workflow = _read(".github/workflows/release-validation.yml")
    for term in ("pull_request:", "push:", "actions/checkout@", "actions/setup-python@", "python-version", "pytest"):
        assert term in workflow
    for command in (
        "tools/validate_plugin.py",
        "tools/generate_sbom.py",
        "tools/check_compatibility.py",
        "tests/test_release_signing.py",
        "tests/test_hook_bridge.py",
    ):
        assert command in workflow
    assert "p7-reports/*.json" in workflow
    assert "p7-reports/*-junit.xml" in workflow
    assert "if-no-files-found: error" in workflow
    assert ".sqlite" not in workflow
    assert "prompt" not in workflow.lower()
    assert "credential" not in workflow.lower()
    assert ".oci" not in workflow.lower()
