"""This distribution registers DockerSandbox for the Sandbox seam at install
time; the registry must discover it (the install-time half of the swap
mechanism)."""

from kaula.core import Registry, Sandbox
from kaula.sandbox_local import DockerSandbox


def test_installed_package_provides_the_sandbox() -> None:
    registry = Registry(discover_installed=True)
    assert isinstance(registry.resolve(Sandbox), DockerSandbox)
