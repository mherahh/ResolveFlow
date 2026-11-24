"""Shared state passed between graph nodes. Each node returns only the keys it changes."""
import operator
from typing import Annotated, Optional, TypedDict


class SupportState(TypedDict, total=False):
    # inputs
    message: str
    customer_id: Optional[str]
    ticket_id: str
    conversation_history: Optional[list[dict]]  # [{"role": "customer"|"agent", "text": str}]
    approved: bool                 # True when a human manager approved a pending action
    confirmed: bool                # True when customer explicitly confirmed a proposed write action
    require_confirmation: bool     # True when consequential actions require customer confirmation before execution
    confirmation_id: Optional[str]
    actor_id: Optional[str]
    actor_role: Optional[str]
    # results of each step
    intake: dict
    context: dict
    plan: dict
    policy: dict
    exec_result: dict
    verification: dict
    # control
    attempt: int                   # policy-correction attempts used
    verify_attempts: int           # verification failures so far
    policy_feedback: Optional[str]
    error: Optional[str]
    address_needed: bool
    order_selection_needed: bool
    # outputs
    outcome: str                   # resolved | escalated | pending_approval | needs_info | needs_confirmation | failed
    escalation_reason: Optional[str]
    handoff: Optional[dict]
    final_response: str
    trace: Annotated[list, operator.add]   # list of events; new events are appended
