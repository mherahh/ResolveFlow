"""Pydantic models: validated structured outputs from the LLM and API request/response shapes."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

Intent = Literal["order_inquiry", "shipment_tracking", "refund_request", "cancellation",
                 "address_change", "general_inquiry"]
Sentiment = Literal["calm", "frustrated", "angry"]
ActionName = Literal["none", "refund", "cancel", "change_address", "escalate"]
MessageType = Literal[
    "inquiry",          # Asking about status, tracking, policy, or eligibility
    "action_request",   # Asking to initiate a refund, cancellation, or address change
    "confirm_action",   # Explicitly authorizing a pending action
    "cancel_action",    # Cancelling/aborting a pending action ("never mind", "cancel that")
    "provide_info",     # Providing requested order ID, address, or details
    "correction",       # Correcting previously provided order ID or address
    "ambiguous",        # Vague/unclear message ("okay", "maybe", "hmm")
]


class IntakeResult(BaseModel):
    """What the Intake step understood from the customer's message."""
    intent: Intent
    sentiment: Sentiment
    message_type: MessageType = Field(
        "inquiry",
        description="Whether this is an inquiry, action_request, confirm_action, cancel_action, provide_info, correction, or ambiguous",
    )
    order_id: Optional[str] = Field(None, description="Order ID like ORD-1001 if the customer gave one")
    email: Optional[str] = Field(None, description="Customer email address if provided in message")
    confirmed: bool = Field(False, description="True ONLY if customer explicitly authorizes a pending action")
    new_address: Optional[str] = Field(None, description="New delivery address if the customer gave one")
    reference_latest_order: bool = Field(False, description="True if customer refers to their last/latest/recent order")
    reason: Optional[str] = Field(None, description="Short customer reason for refund/cancellation if mentioned")


class ActionPlan(BaseModel):
    """The Resolution step's proposed plan. It is only a suggestion; the Policy Guard decides."""
    action: ActionName
    order_id: Optional[str] = None
    amount: Optional[float] = None
    new_address: Optional[str] = None
    reason: str = Field(description="One short sentence explaining the plan, based only on retrieved facts")
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are, from 0 to 1")


class PolicyDecision(BaseModel):
    decision: Literal["allow", "needs_approval", "deny"]
    reasons: list[str] = []


class VerificationResult(BaseModel):
    ok: bool
    detail: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    customer_id: Optional[str] = None  # Ignored/validated against authenticated session on backend
    ticket_id: Optional[str] = None
    order_id: Optional[str] = None     # Optional explicit order context from order sidebar click
    require_confirmation: Optional[bool] = None


class ApprovalRequest(BaseModel):
    approve: bool
    note: Optional[str] = None


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=1, max_length=128)


class DemoSwitchRequest(BaseModel):
    account_id: str = Field(min_length=2, max_length=32)


class ConfirmationActionRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)
