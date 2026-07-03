"""kaula-core: the contracts every Kaula package builds against.

Domain types, seam Protocols, the loop state machine, and the implementation
registry. Framework-agnostic by rule — no CrewAI, no implementation imports.
"""

from kaula.core.loop import (
    PHASE_ORDER,
    RETRY_PHASES,
    TERMINAL_PHASES,
    HealingPhase,
    InvalidTransition,
    LoopStateMachine,
)
from kaula.core.policy import PermissivePolicyEngine
from kaula.core.protocols import (
    AuditSink,
    MCPGateway,
    MemoryStore,
    PolicyEngine,
    RepairAgent,
    Sandbox,
    Scanner,
)
from kaula.core.registry import ENTRY_POINT_GROUP, Registry, ResolutionError, load_symbol
from kaula.core.types import (
    AuditEvent,
    HealingRecord,
    Plan,
    PlanStep,
    PolicyDecision,
    RepairCandidate,
    SandboxResult,
    ScanFinding,
    ScanResult,
    ToolFailure,
    ToolTest,
    ToolVersion,
    fingerprint_args,
    new_id,
    sha256_hex,
    utcnow,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "PHASE_ORDER",
    "RETRY_PHASES",
    "TERMINAL_PHASES",
    "AuditEvent",
    "AuditSink",
    "HealingPhase",
    "HealingRecord",
    "InvalidTransition",
    "LoopStateMachine",
    "MCPGateway",
    "MemoryStore",
    "PermissivePolicyEngine",
    "Plan",
    "PlanStep",
    "PolicyDecision",
    "PolicyEngine",
    "Registry",
    "RepairAgent",
    "RepairCandidate",
    "ResolutionError",
    "Sandbox",
    "SandboxResult",
    "ScanFinding",
    "ScanResult",
    "Scanner",
    "ToolFailure",
    "ToolTest",
    "ToolVersion",
    "fingerprint_args",
    "load_symbol",
    "new_id",
    "sha256_hex",
    "utcnow",
]
