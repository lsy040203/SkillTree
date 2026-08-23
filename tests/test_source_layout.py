from __future__ import annotations


def test_responsibility_packages_import_without_side_effects() -> None:
    import skilltree
    import skilltree.application.cli
    import skilltree.compat.memory_candidates
    import skilltree.core.bundle
    import skilltree.core.storage
    import skilltree.hooks.hook_bridge
    import skilltree.interfaces.registry_io
    import skilltree.registry_service.registry

    assert skilltree.__version__ == "0.4.1"


def test_root_module_facades_preserve_existing_public_imports() -> None:
    from skilltree.bundle import build_bundle as facade_build_bundle
    from skilltree.core.bundle import build_bundle as implementation_build_bundle
    from skilltree.hook_bridge import handle_hook_event as facade_handle_hook_event
    from skilltree.hooks.hook_bridge import handle_hook_event as implementation_handle_hook_event
    from skilltree.storage import Database as facade_database
    from skilltree.core.storage import Database as implementation_database

    assert facade_build_bundle is implementation_build_bundle
    assert facade_handle_hook_event is implementation_handle_hook_event
    assert facade_database is implementation_database


def test_compatibility_facades_are_centralized() -> None:
    from skilltree.compat.memory_candidates import normalize_memory_extraction_candidate as compat_candidate
    from skilltree.core.memory_candidates import normalize_memory_extraction_candidate as implementation_candidate

    assert compat_candidate is implementation_candidate
