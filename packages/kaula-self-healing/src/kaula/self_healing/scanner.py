"""Reference Scanner: AST-based static checks on candidate source.

Deliberately conservative deny-list scanning — repaired tools have no
business spawning processes, opening sockets, or executing dynamic code. A
finding of severity ``high`` or ``critical`` fails the scan; the loop never
weakens this gate to make a candidate pass.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from kaula.core import RepairCandidate, ScanFinding, ScanResult
from kaula.core.types import Severity

__all__ = ["BasicStaticScanner"]

_BLOCKING_SEVERITIES = {"high", "critical"}

_DEFAULT_BANNED_IMPORTS: Mapping[str, Severity] = {
    "subprocess": "high",
    "socket": "high",
    "ctypes": "critical",
    "pty": "high",
    "pickle": "high",
    "marshal": "high",
}

_DEFAULT_BANNED_CALLS: Mapping[str, Severity] = {
    "eval": "critical",
    "exec": "critical",
    "compile": "critical",
    "__import__": "critical",
    "os.system": "high",
    "os.popen": "high",
    "os.execv": "high",
    "os.execve": "high",
    "os.spawnl": "high",
}


class BasicStaticScanner:
    def __init__(
        self,
        *,
        banned_imports: Mapping[str, Severity] | None = None,
        banned_calls: Mapping[str, Severity] | None = None,
    ) -> None:
        self._banned_imports = dict(
            banned_imports if banned_imports is not None else _DEFAULT_BANNED_IMPORTS
        )
        self._banned_calls = dict(
            banned_calls if banned_calls is not None else _DEFAULT_BANNED_CALLS
        )

    def scan(self, candidate: RepairCandidate) -> ScanResult:
        try:
            tree = ast.parse(candidate.source)
        except SyntaxError as exc:
            finding = ScanFinding(
                rule="syntax-error",
                severity="critical",
                message=f"candidate source does not parse: {exc.msg}",
                line=exc.lineno,
            )
            return ScanResult(passed=False, findings=(finding,))

        findings: list[ScanFinding] = []
        for node in ast.walk(tree):
            findings.extend(self._check_node(node))

        passed = not any(f.severity in _BLOCKING_SEVERITIES for f in findings)
        return ScanResult(passed=passed, findings=tuple(findings))

    def _check_node(self, node: ast.AST) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                findings.extend(self._check_import(alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            findings.extend(self._check_import(node.module.split(".")[0], node.lineno))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name is not None and name in self._banned_calls:
                findings.append(
                    ScanFinding(
                        rule="banned-call",
                        severity=self._banned_calls[name],
                        message=f"call to {name!r} is not allowed in repaired tools",
                        line=node.lineno,
                    )
                )
        return findings

    def _check_import(self, root_module: str, lineno: int) -> list[ScanFinding]:
        if root_module not in self._banned_imports:
            return []
        return [
            ScanFinding(
                rule="banned-import",
                severity=self._banned_imports[root_module],
                message=f"import of {root_module!r} is not allowed in repaired tools",
                line=lineno,
            )
        ]


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None
