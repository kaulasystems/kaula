from pathlib import Path

import pytest
from kaula.core import PermissivePolicyEngine, PolicyEngine, Registry, ResolutionError, Sandbox


class FakeSandbox:
    def run(self, candidate, tests, *, timeout_s=30.0):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_explicit_injection_wins_over_everything() -> None:
    registry = Registry()
    explicit = FakeSandbox()
    registry.register_default(Sandbox, FakeSandbox)
    registry.configure({"sandbox": "kaula.core.policy:PermissivePolicyEngine"})
    registry.register(Sandbox, explicit)
    assert registry.resolve(Sandbox) is explicit


def test_config_declared_impl_resolved_by_import_path() -> None:
    registry = Registry()
    registry.register_default(PolicyEngine, FakeSandbox)
    registry.configure({"PolicyEngine": "kaula.core.policy:PermissivePolicyEngine"})
    assert isinstance(registry.resolve(PolicyEngine), PermissivePolicyEngine)


def test_default_used_when_nothing_configured() -> None:
    registry = Registry()
    registry.register_default(Sandbox, FakeSandbox)
    assert isinstance(registry.resolve(Sandbox), FakeSandbox)


def test_unresolvable_interface_raises_with_guidance() -> None:
    registry = Registry()
    with pytest.raises(ResolutionError, match="Sandbox"):
        registry.resolve(Sandbox)


def test_load_config_from_toml(tmp_path: Path) -> None:
    config = tmp_path / "kaula.toml"
    config.write_text(
        '[implementations]\npolicyengine = "kaula.core.policy:PermissivePolicyEngine"\n'
    )
    registry = Registry()
    registry.load_config(config)
    assert isinstance(registry.resolve(PolicyEngine), PermissivePolicyEngine)


def test_bad_import_path_rejected() -> None:
    registry = Registry()
    registry.configure({"sandbox": "kaula.core.policy"})  # missing ':Attr'
    with pytest.raises(ValueError, match="module:Attr"):
        registry.resolve(Sandbox)


class FakeEntryPoint:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


def test_entry_point_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kaula.core.registry.entry_points",
        lambda group: [FakeEntryPoint("policyengine", "kaula.core.policy:PermissivePolicyEngine")],
    )
    registry = Registry(discover_installed=True)
    assert isinstance(registry.resolve(PolicyEngine), PermissivePolicyEngine)


def test_config_outranks_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kaula.core.registry.entry_points",
        lambda group: [FakeEntryPoint("sandbox", "kaula.core.policy:PermissivePolicyEngine")],
    )
    registry = Registry(discover_installed=True)
    registry.configure({"sandbox": "test_registry:FakeSandbox"})
    assert isinstance(registry.resolve(Sandbox), FakeSandbox)


def test_conflicting_entry_points_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kaula.core.registry.entry_points",
        lambda group: [
            FakeEntryPoint("sandbox", "kaula.sandbox_local:DockerSandbox"),
            FakeEntryPoint("sandbox", "kaula.sandbox_hardened:HardenedSandbox"),
        ],
    )
    registry = Registry(discover_installed=True)
    with pytest.raises(ResolutionError, match="multiple installed implementations"):
        registry.resolve(Sandbox)
