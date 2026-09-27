"""Sandbox Engineering (W5): quien puede hacer que.

Hoy solo permisos. El aislamiento real -- Docker, limites de recursos, red --
todavia no existe, y esa distincion esta en el docstring de permissions.py.
"""

from localforge.sandbox.approvers import Approver, AutoApprover, DenyingApprover
from localforge.sandbox.permissions import (
    Decision,
    PermissionPolicy,
    Rule,
    SENSITIVE_GLOBS,
    Verdict,
    default_policy,
    read_only_policy,
)

__all__ = [
    "Approver", "AutoApprover", "DenyingApprover",
    "Decision", "PermissionPolicy", "Rule", "Verdict",
    "SENSITIVE_GLOBS", "default_policy", "read_only_policy",
]
