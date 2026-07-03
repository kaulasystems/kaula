"""Pause ledger + wrapper pause integration — framework-free."""

from pathlib import Path

import pytest
from kaula.runtime import SqlitePauseLedger, ToolHealingPausedError, UnknownPauseError
from test_wrapper import ScriptedRepairAgent, make_loop, make_wrapper


def test_record_and_resolve_lifecycle() -> None:
    ledger = SqlitePauseLedger()
    record = ledger.record_pause(
        tool_name="parse", failure_id="fail-1", reason="budget exhausted", run_id="run-1"
    )

    assert record.pending
    assert [r.record_id for r in ledger.pending()] == [record.record_id]

    resolved = ledger.resolve(record.record_id, resolution="rolled back to v1")
    assert not resolved.pending
    assert resolved.resolution == "rolled back to v1"
    assert ledger.pending() == []


def test_resolve_is_single_shot() -> None:
    ledger = SqlitePauseLedger()
    record = ledger.record_pause(tool_name="t", failure_id="f", reason="r")
    ledger.resolve(record.record_id, resolution="fixed")
    with pytest.raises(UnknownPauseError):
        ledger.resolve(record.record_id, resolution="fixed again")
    with pytest.raises(UnknownPauseError):
        ledger.resolve("pause-nope", resolution="x")


def test_pauses_persist_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "pauses.db"
    SqlitePauseLedger(db).record_pause(tool_name="t", failure_id="f", reason="r")
    reopened = SqlitePauseLedger(db)
    assert len(reopened.pending()) == 1


def test_pauses_are_mirrored_to_audit() -> None:
    from test_wrapper import NullAudit

    audit = NullAudit()
    ledger = SqlitePauseLedger(audit=audit)
    record = ledger.record_pause(tool_name="t", failure_id="f", reason="r")
    ledger.resolve(record.record_id, resolution="fixed")
    assert audit.types == ["run_paused", "pause_resolved"]


def test_wrapper_records_pause_when_healing_fails() -> None:
    ledger = SqlitePauseLedger()
    wrapper = make_wrapper(
        loop=make_loop(ScriptedRepairAgent(source=None)),
        pause_ledger=ledger,
        run_id="run-42",
    )

    with pytest.raises(ToolHealingPausedError) as excinfo:
        wrapper("1,234.5")

    record = excinfo.value.pause_record
    assert record is not None
    assert record.pending
    assert record.tool_name == "parse_price"
    assert record.run_id == "run-42"
    assert ledger.pending()[0].record_id == record.record_id
