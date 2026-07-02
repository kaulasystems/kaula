from kaula.core import RepairCandidate
from kaula.self_healing import BasicStaticScanner


def _candidate(source: str) -> RepairCandidate:
    return RepairCandidate(
        failure_id="fail-1",
        tool_name="t",
        entrypoint="run",
        source=source,
        diagnosis="d",
        attempt=1,
    )


def test_clean_source_passes() -> None:
    result = BasicStaticScanner().scan(
        _candidate("import json\n\ndef run(x):\n    return json.loads(x)\n")
    )
    assert result.passed
    assert result.findings == ()


def test_banned_import_fails() -> None:
    result = BasicStaticScanner().scan(_candidate("import subprocess\n\ndef run():\n    pass\n"))
    assert not result.passed
    assert result.findings[0].rule == "banned-import"
    assert "subprocess" in result.findings[0].message


def test_banned_from_import_and_submodule_fail() -> None:
    scanner = BasicStaticScanner()
    assert not scanner.scan(_candidate("from socket import socket\n")).passed
    assert not scanner.scan(_candidate("import ctypes.util\n")).passed


def test_banned_calls_fail() -> None:
    scanner = BasicStaticScanner()
    assert not scanner.scan(_candidate("def run(x):\n    return eval(x)\n")).passed
    assert not scanner.scan(_candidate("import os\n\ndef run(c):\n    os.system(c)\n")).passed


def test_syntax_error_is_a_critical_finding() -> None:
    result = BasicStaticScanner().scan(_candidate("def run(:\n"))
    assert not result.passed
    assert result.findings[0].rule == "syntax-error"
    assert result.findings[0].severity == "critical"


def test_findings_carry_line_numbers() -> None:
    result = BasicStaticScanner().scan(_candidate("x = 1\nimport pickle\n"))
    assert result.findings[0].line == 2


def test_custom_deny_list_overrides_defaults() -> None:
    scanner = BasicStaticScanner(banned_imports={"json": "high"}, banned_calls={})
    assert not scanner.scan(_candidate("import json\n")).passed
    assert scanner.scan(_candidate("import subprocess\n")).passed
