import pytest
from kaula.audit_local import (
    NoPreviousVersionError,
    SqliteAuditSink,
    ToolVersionStore,
    UnknownToolError,
)
from kaula.core import ToolVersion

V1_SOURCE = "def run(x):\n    return float(x)\n"
V2_SOURCE = "def run(x):\n    return float(x.replace(',', ''))\n"


@pytest.fixture
def sink() -> SqliteAuditSink:
    return SqliteAuditSink()


@pytest.fixture
def store(sink: SqliteAuditSink) -> ToolVersionStore:
    return ToolVersionStore(audit=sink)


def test_register_and_current(store: ToolVersionStore) -> None:
    v1 = ToolVersion.initial("parse", "run", V1_SOURCE)
    store.register(v1)
    current = store.current("parse")
    assert current.version == 1
    assert current.source == V1_SOURCE
    assert current.entrypoint == "run"


def test_new_version_becomes_current_history_kept(store: ToolVersionStore) -> None:
    v1 = ToolVersion.initial("parse", "run", V1_SOURCE)
    v2 = v1.child(V2_SOURCE)
    store.register(v1)
    store.register(v2)

    assert store.current("parse").version == 2
    assert [v.version for v in store.history("parse")] == [1, 2]
    assert store.get("parse", 1).source == V1_SOURCE


def test_rollback_is_single_logged_action(store: ToolVersionStore, sink: SqliteAuditSink) -> None:
    v1 = ToolVersion.initial("parse", "run", V1_SOURCE)
    v2 = v1.child(V2_SOURCE)
    store.register(v1)
    store.register(v2)

    reverted = store.rollback("parse")

    assert reverted.version == 1
    assert store.current("parse").version == 1
    rollback_events = [e for e in sink.events() if e.event_type == "rollback"]
    assert len(rollback_events) == 1
    assert rollback_events[0].payload == {
        "tool_name": "parse",
        "from_version": 2,
        "to_version": 1,
        "to_source_hash": v1.source_hash,
    }
    assert sink.verify()


def test_rollback_from_initial_version_refused(store: ToolVersionStore) -> None:
    store.register(ToolVersion.initial("parse", "run", V1_SOURCE))
    with pytest.raises(NoPreviousVersionError):
        store.rollback("parse")


def test_unknown_tool_raises(store: ToolVersionStore) -> None:
    with pytest.raises(UnknownToolError):
        store.current("nope")


def test_audit_references_source_by_hash_only(
    store: ToolVersionStore, sink: SqliteAuditSink
) -> None:
    v1 = ToolVersion.initial("parse", "run", V1_SOURCE)
    store.register(v1)
    registered = [e for e in sink.events() if e.event_type == "tool_version_registered"]
    assert registered[0].payload["source_hash"] == v1.source_hash
    assert "source" not in registered[0].payload
