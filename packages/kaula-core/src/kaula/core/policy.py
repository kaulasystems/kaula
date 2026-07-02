"""The open default PolicyEngine.

Permissive, single-user, all-autonomous-if-green: a candidate that passed the
sandbox tests and the security scan may go live without human approval.
RBAC, approval workflows and autonomy tiers are the commercial
``kaula-governance`` implementation of the same Protocol.
"""

from __future__ import annotations

from kaula.core.types import PolicyDecision, RepairCandidate, SandboxResult, ScanResult

__all__ = ["PermissivePolicyEngine"]


class PermissivePolicyEngine:
    def authorize_swap(
        self,
        candidate: RepairCandidate,
        sandbox_result: SandboxResult,
        scan_result: ScanResult,
    ) -> PolicyDecision:
        if sandbox_result.infra_error is not None:
            return PolicyDecision(
                allowed=False,
                reason=f"sandbox infrastructure error: {sandbox_result.infra_error}",
            )
        if not sandbox_result.passed:
            return PolicyDecision(allowed=False, reason="sandbox verification did not pass")
        if not scan_result.passed:
            return PolicyDecision(allowed=False, reason="security scan did not pass")
        return PolicyDecision(allowed=True, reason="all verification gates green")
