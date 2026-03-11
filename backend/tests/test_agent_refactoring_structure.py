"""Tests for agent_manager re-exports and refactoring structure.

# Feature: agent-code-refactoring, Property 7: All required symbols re-exported from agent_manager

Validates: Requirements 11.1, 11.2

Verifies that all required symbols are importable from core.agent_manager
and that the PermissionManager singleton is the same instance across imports.

# Feature: agent-code-refactoring, Property 6: All functions in new modules have type-annotated signatures

Validates: Requirements 10.2

Verifies that all functions in the extracted modules have type annotations
on all parameters (except ``self``) and on return types.
"""

import inspect
import sys
import types
from typing import get_type_hints

import pytest


# ---------------------------------------------------------------------------
# Property 7: All required symbols re-exported from agent_manager
# ---------------------------------------------------------------------------

REQUIRED_SYMBOLS = [
    "ensure_default_agent",
    "get_default_agent",
    "approve_command",
    "is_command_approved",
    "set_permission_decision",
    "wait_for_permission_decision",
    "DEFAULT_AGENT_ID",
    "SWARM_AGENT_NAME",
    "AgentManager",
    "DANGEROUS_PATTERNS",
    "check_dangerous_command",
    "expand_allowed_skills_with_plugins",
    "ContentBlockAccumulator",
]


def _get_agent_manager_module():
    """Get the actual agent_manager module object.

    The module defines `agent_manager = AgentManager()` at module level,
    which shadows the module name on attribute access. We use sys.modules
    to get the real module.
    """
    import core.agent_manager  # noqa: F401 — triggers module load
    return sys.modules["core.agent_manager"]


@pytest.mark.parametrize("symbol_name", REQUIRED_SYMBOLS)
def test_symbol_importable_from_agent_manager(symbol_name: str):
    """**Validates: Requirements 11.1, 11.2**

    Each required symbol must be importable from core.agent_manager.
    """
    am = _get_agent_manager_module()

    assert hasattr(am, symbol_name), (
        f"Symbol '{symbol_name}' is not importable from core.agent_manager"
    )
    assert getattr(am, symbol_name) is not None, (
        f"Symbol '{symbol_name}' is None in core.agent_manager"
    )


def test_permission_manager_singleton_identity():
    """**Validates: Requirements 11.1, 11.2**

    The PermissionManager singleton should be the same instance whether
    imported directly from permission_manager.py or accessed via the
    agent_manager.py re-exports.

    Bound methods are not identity-equal across accesses, so we verify
    that the re-exported callables reference the same underlying
    PermissionManager instance (via __self__).
    """
    from core.permission_manager import permission_manager as direct_pm

    am = _get_agent_manager_module()

    # Re-exported bound methods should reference the same PermissionManager instance
    assert am.approve_command.__self__ is direct_pm
    assert am.is_command_approved.__self__ is direct_pm
    assert am.set_permission_decision.__self__ is direct_pm
    assert am.wait_for_permission_decision.__self__ is direct_pm


# ---------------------------------------------------------------------------
# Property 5: All refactored modules and public methods have docstrings
# ---------------------------------------------------------------------------

EXTRACTED_MODULES = [
    "core.security_hooks",
    "core.permission_manager",
    "core.agent_defaults",
    "core.claude_environment",
    "core.content_accumulator",
]


@pytest.mark.parametrize("module_name", EXTRACTED_MODULES)
def test_extracted_module_has_docstring(module_name: str):
    """**Validates: Requirements 8.1, 10.1**

    Each extracted module must have a non-empty __doc__ attribute.
    """
    __import__(module_name)
    mod = sys.modules[module_name]

    assert mod.__doc__ is not None, (
        f"Module '{module_name}' has no __doc__ attribute"
    )
    assert mod.__doc__.strip(), (
        f"Module '{module_name}' has an empty docstring"
    )



# ---------------------------------------------------------------------------
# Property 6: All functions in new modules have type-annotated signatures
# ---------------------------------------------------------------------------


def _collect_functions_and_methods(module_name: str) -> list[tuple[str, str, callable]]:
    """Collect all top-level functions and class methods from a module.

    Returns a list of (qualified_name, param_context, callable) tuples.
    Only collects directly-defined callables (not imported ones) and
    skips inner closures / factory-returned functions.
    """
    __import__(module_name)
    mod = sys.modules[module_name]
    results: list[tuple[str, str, callable]] = []

    for name, obj in inspect.getmembers(mod):
        # Skip imported objects — only check things defined in this module
        if getattr(obj, "__module__", None) != module_name:
            continue

        if inspect.isfunction(obj):
            results.append((f"{module_name}.{name}", module_name, obj))
        elif inspect.isclass(obj):
            for method_name, method_obj in inspect.getmembers(obj, predicate=inspect.isfunction):
                # Only include methods defined in this class (not inherited)
                if method_name in obj.__dict__:
                    results.append(
                        (f"{module_name}.{name}.{method_name}", module_name, method_obj)
                    )

    return results


def _build_type_annotation_cases() -> list[tuple[str, callable]]:
    """Build the full list of (qualified_name, callable) for parameterization."""
    modules = [
        "core.security_hooks",
        "core.permission_manager",
        "core.agent_defaults",
        "core.claude_environment",
        "core.content_accumulator",
    ]
    cases: list[tuple[str, callable]] = []
    for mod_name in modules:
        for qualified_name, _, fn in _collect_functions_and_methods(mod_name):
            cases.append((qualified_name, fn))
    return cases


_TYPE_ANNOTATION_CASES = _build_type_annotation_cases()


@pytest.mark.parametrize(
    "qualified_name,fn",
    _TYPE_ANNOTATION_CASES,
    ids=[c[0] for c in _TYPE_ANNOTATION_CASES],
)
def test_function_parameters_have_type_annotations(qualified_name: str, fn: callable):
    """**Validates: Requirements 10.2**

    Every parameter (except ``self``) on every function/method in the
    extracted modules must have a type annotation.
    """
    sig = inspect.signature(fn)
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        assert param.annotation is not inspect.Parameter.empty, (
            f"{qualified_name}: parameter '{param_name}' has no type annotation"
        )


@pytest.mark.parametrize(
    "qualified_name,fn",
    _TYPE_ANNOTATION_CASES,
    ids=[c[0] for c in _TYPE_ANNOTATION_CASES],
)
def test_function_return_type_annotated(qualified_name: str, fn: callable):
    """**Validates: Requirements 10.2**

    Every function/method in the extracted modules must have a return
    type annotation.
    """
    sig = inspect.signature(fn)
    assert sig.return_annotation is not inspect.Signature.empty, (
        f"{qualified_name}: missing return type annotation"
    )
