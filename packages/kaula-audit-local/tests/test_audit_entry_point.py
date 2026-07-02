"""This distribution registers SqliteAuditSink for the AuditSink seam at
install time; the registry must discover it (the install-time half of the
swap mechanism)."""

from kaula.audit_local import SqliteAuditSink
from kaula.core import AuditSink, Registry


def test_installed_package_provides_the_audit_sink() -> None:
    registry = Registry(discover_installed=True)
    sink = registry.resolve(AuditSink)
    assert isinstance(sink, SqliteAuditSink)
    assert sink.verify()
