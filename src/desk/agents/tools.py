"""Tool permissions for the three research roles (RC09, OWASP agentic S50).

Rules:
- Only the orchestrating code may request a tool ("system" origin). A request that
  originates from source content, model output or a user's chat text is always denied,
  whatever it says — outside text is data, never authority.
- Each role has a fixed, narrow allowlist. No role can send messages, read secrets or
  approve anything; sending is done only by the outbox service, which is not an agent.
- Every tool call is bound to one tenant; arguments naming another tenant are denied.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Origin(StrEnum):
    SYSTEM = "system"
    MODEL_OUTPUT = "model_output"
    SOURCE_CONTENT = "source_content"
    USER_MESSAGE = "user_message"


ROLE_TOOLS: dict[str, frozenset[str]] = {
    "research": frozenset({"fetch_dataset"}),
    "chief": frozenset({"draft_scenarios"}),
    "reviewer": frozenset({"review_report"}),
}


class ToolDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class ToolRequest:
    role: str
    tool: str
    origin: Origin
    tenant_id: uuid.UUID
    args: dict[str, Any] = field(default_factory=dict)


def authorize(req: ToolRequest, scope_tenant: uuid.UUID) -> None:
    if req.origin is not Origin.SYSTEM:
        raise ToolDeniedError(f"tool requests from {req.origin} are never executed")
    if req.tool not in ROLE_TOOLS.get(req.role, frozenset()):
        raise ToolDeniedError(f"role {req.role!r} may not use {req.tool!r}")
    if req.tenant_id != scope_tenant:
        raise ToolDeniedError("cross-tenant tool request")
    for value in req.args.values():
        if isinstance(value, uuid.UUID) and value != scope_tenant:
            raise ToolDeniedError("argument names another tenant")
