"""SelfHealingLoop.from_registry — the swap mechanism, exercised with fakes."""

import pytest
from kaula.core import (
    AuditSink,
    MemoryStore,
    PermissivePolicyEngine,
    PolicyEngine,
    Registry,
    RepairAgent,
    ResolutionError,
    Sandbox,
    Scanner,
    ToolFailure,
    ToolVersion,
)
from kaula.self_healing import SelfHealingLoop
from test_healing_loop import (
    GOOD_SOURCE,
    TESTS,
    FakeRepairAgent,
    FakeSandbox,
    FakeScanner,
    RecordingAudit,
    RecordingMemory,
)


def make_registry() -> Registry:
    registry = Registry()
    registry.register(RepairAgent, FakeRepairAgent([GOOD_SOURCE]))
    registry.register(Sandbox, FakeSandbox())
    registry.register(Scanner, FakeScanner())
    registry.register(AuditSink, RecordingAudit())
    return registry


def heal_once(loop: SelfHealingLoop) -> bool:
    try:
        float("1,234.56")
    except ValueError as exc:
        failure = ToolFailure.from_exception("parse", exc, args=("1,234.56",))
    current = ToolVersion.initial("parse", "parse", "def parse(x):\n    return float(x)\n")
    return loop.heal(failure, current, TESTS, apply_swap=lambda v: None).healed


def test_assembles_from_explicit_registrations() -> None:
    loop = SelfHealingLoop.from_registry(make_registry())
    assert heal_once(loop)


def test_policy_and_memory_are_optional_with_safe_fallbacks() -> None:
    # heals under the permissive default policy without a memory store
    assert heal_once(SelfHealingLoop.from_registry(make_registry()))

    registry = make_registry()  # fresh: the fake agent is single-use
    memory = RecordingMemory()
    registry.register(MemoryStore, memory)
    registry.register(PolicyEngine, PermissivePolicyEngine())
    assert heal_once(SelfHealingLoop.from_registry(registry))
    assert len(memory.records) == 1  # resolved memory is actually wired in


def test_missing_required_interface_fails_loudly() -> None:
    registry = make_registry()
    registry._explicit.pop(Sandbox)
    with pytest.raises(ResolutionError, match="Sandbox"):
        SelfHealingLoop.from_registry(registry)


def test_config_can_select_the_scanner(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = make_registry()
    registry._explicit.pop(Scanner)
    config = tmp_path / "kaula.toml"
    config.write_text('[implementations]\nscanner = "kaula.self_healing:BasicStaticScanner"\n')
    registry.load_config(config)
    assert heal_once(SelfHealingLoop.from_registry(registry))
