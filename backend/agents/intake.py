"""Intake agent: LLM call with structured output + deterministic speech-act and entity safety nets."""
import re
from models.schemas import IntakeResult
from prompts.prompts import INTAKE_PROMPT

ORDER_RE = re.compile(r"(?:ORD-?\s*|order\s*#?\s*|#)(\d{3,6})\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Strict explicit confirmations (vague "ok"/"okay"/"alright"/"maybe" deliberately excluded!)
EXPLICIT_CONFIRM_RE = re.compile(
    r"^\s*(?:"
    r"yes(?:,?\s*(?:please|proceed|confirm|go ahead|do it|refund it|cancel it|update it|change it|submit it))?|"
    r"confirm(?:\s+(?:refund|cancellation|cancel|address change|order|request|it))?|"
    r"confirmed|"
    r"i confirm(?:\s+(?:the\s+)?(?:refund|cancellation|address change|this|it))?|"
    r"please proceed(?:\s+with.*)?|"
    r"proceed(?:\s+with.*)?|"
    r"go ahead(?:\s+and\s+(?:refund|cancel|update|change|proceed).*)?"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Explicit withdrawal / cancellation of a pending request
CANCEL_PENDING_RE = re.compile(
    r"(?:"
    r"\b(?:never\s*mind|nevermind|forget\s+it|changed\s+my\s+mind|don'?t\s+(?:do\s+it|proceed|refund|cancel|change|update)|"
    r"do\s+not\s+(?:proceed|refund|cancel|change|update)|no\s+thanks|abort|stop\s+(?:that|it|the\s+request)|"
    r"cancel\s+(?:that|this|the)\s+(?:request|action|refund|cancellation|update)|"
    r"actually,?\s+no)\b|"
    r"^\s*(?:no|nope|cancel)\s*[.!]?\s*$"
    r")",
    re.IGNORECASE,
)

# Ambiguous / vague replies that must NEVER authorize a financial or destructive action
AMBIGUOUS_RE = re.compile(
    r"^\s*(?:ok|okay|k|kk|alright|fine|sure|hmm+|uh+|um+|maybe|not\s+sure|i\s+guess|whatever|sounds\s+good|wait|hold\s+on)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# Pure eligibility / policy questions (must NOT initiate or execute an action)
ELIGIBILITY_INQUIRY_RE = re.compile(
    r"(?:"
    r"\b(?:am\s+i\s+eligible|is\s+(?:my\s+order|ord-\d+|it)\s+eligible|"
    r"want\s+to\s+know\s+if\s+i\s+can\s+(?:get\s+a\s+refund|refund|cancel|change)|"
    r"wondering\s+if\s+i\s+can\s+(?:get\s+a\s+refund|refund|cancel|change)|"
    r"would\s+(?:i|my\s+order|ord-\d+)\s+(?:be\s+eligible|qualify)|"
    r"what\s+is\s+your\s+(?:return|refund|cancellation|shipping)\s+policy|"
    r"how\s+(?:do\s+refunds|does\s+your\s+return\s+policy|long\s+does\s+a\s+refund)|"
    r"can\s+orders\s+be\s+(?:refunded|cancelled)|"
    r"check\s+(?:refund\s+)?eligibility)\b"
    r")",
    re.IGNORECASE,
)

LATEST_ORDER_RE = re.compile(
    r"\b(?:last|latest|most\s+recent|newest|recent)\s+order\b",
    re.IGNORECASE,
)

CORRECTION_RE = re.compile(
    r"\b(?:actually\s+i\s+meant|sorry,?\s*i\s+meant|meant\s+ord-|instead\s+of|wrong\s+order|not\s+ord-)\b",
    re.IGNORECASE,
)

ADDRESS_EXTRACT_RE = re.compile(
    r"(?:(?:deliver|ship|send|change\s+(?:the\s+|my\s+)?(?:shipping\s+|delivery\s+)?address(?:\s+(?:of|for)\s+ORD-\d+)?|update\s+(?:the\s+|my\s+)?(?:shipping\s+|delivery\s+)?address(?:\s+(?:of|for)\s+ORD-\d+)?)\s+to|"
    r"(?:new\s+)?(?:shipping\s+|delivery\s+)?address\s*(?:is|:|to))\s+([A-Za-z0-9#][^.?]{7,120}?)(?:\s+instead)?[.?]?$",
    re.IGNORECASE,
)


def normalize_order_id(text: str | None) -> str | None:
    if not text:
        return None
    m = ORDER_RE.search(text)
    return f"ORD-{m.group(1)}" if m else None


def normalize_email(text: str | None) -> str | None:
    if not text:
        return None
    m = EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


def classify_pending_reply(message: str) -> str:
    """Deterministically classify a customer's reply when a pending confirmation is active.

    Returns one of: 'confirm_action', 'cancel_action', 'ambiguous', or 'other'.
    """
    msg = message.strip()
    if not msg:
        return "ambiguous"
    if CANCEL_PENDING_RE.search(msg):
        return "cancel_action"
    if AMBIGUOUS_RE.match(msg):
        return "ambiguous"
    if EXPLICIT_CONFIRM_RE.match(msg):
        return "confirm_action"
    return "other"


def extract_address_fallback(message: str) -> str | None:
    m = ADDRESS_EXTRACT_RE.search(message.strip())
    if m:
        candidate = m.group(1).strip().rstrip(".,")
        if (
            len(candidate) >= 8
            and not candidate.lower().startswith(("ord-", "change ", "update ", "my "))
            and any(ch.isdigit() for ch in candidate)
        ):
            return candidate
    # If the entire message looks like a street address (has digits + street/block/road/house/apt/karachi/lahore/etc.)
    stripped = message.strip().rstrip(".")
    if (
        len(stripped) >= 10
        and any(ch.isdigit() for ch in stripped)
        and re.search(r"\b(house|street|st|road|rd|block|sector|ave|avenue|lane|apt|flat|floor|phase|town|city|karachi|lahore|islamabad|clifton|gulshan|dha)\b", stripped, re.IGNORECASE)
        and not re.search(r"\b(refund|cancel|where|track|status|policy|eligible)\b", stripped, re.IGNORECASE)
    ):
        return stripped
    return None


def run_intake(
    llm,
    message: str,
    conversation_history: list[dict] | None = None,
    has_pending_confirmation: bool = False,
) -> IntakeResult:
    context_prefix = ""
    prior_order = None
    prior_email = None
    prior_intent = None

    if conversation_history and len(conversation_history) > 1:
        for past_msg in reversed(conversation_history[:-1]):
            t = past_msg.get("text", "")
            if not prior_order:
                prior_order = normalize_order_id(t)
            if not prior_email:
                prior_email = normalize_email(t)
            if not prior_intent and past_msg.get("role") == "agent":
                tl = t.lower()
                if "new shipping address" in tl or "delivery address" in tl or "address change" in tl:
                    prior_intent = "address_change"
                elif "refund" in tl:
                    prior_intent = "refund_request"
                elif "cancel" in tl:
                    prior_intent = "cancellation"

        recent_turns = conversation_history[-5:-1]
        history_lines = [f"{m.get('role', 'user').capitalize()}: {m.get('text', '')}" for m in recent_turns]
        context_prefix = "Recent conversation context:\n" + "\n".join(history_lines) + "\n\n"

    input_text = f"{context_prefix}Customer latest message:\n{message}" if context_prefix else message

    chain = llm.with_structured_output(IntakeResult)
    result = chain.invoke(INTAKE_PROMPT.format_messages(message=input_text))

    msg_strip = message.strip()
    msg_low = msg_strip.lower()

    # Safety nets for order ID, email, address, latest-order reference
    explicit_oid = normalize_order_id(message)
    asks_all_orders = bool(re.search(r"\b(any\s+order|all\s+(?:of\s+)?my\s+orders|my\s+orders|which\s+orders?)\b", msg_low))
    result.order_id = explicit_oid or normalize_order_id(result.order_id)
    if asks_all_orders and not explicit_oid:
        result.order_id = None
    elif not result.order_id and not LATEST_ORDER_RE.search(msg_low):
        # Only inherit prior_order when continuing the same topic/follow-up
        if prior_order and not any(w in msg_low for w in ("new order", "another order", "different order")):
            result.order_id = prior_order

    result.email = normalize_email(message) or normalize_email(result.email) or prior_email
    if not result.new_address:
        result.new_address = extract_address_fallback(message)
    if LATEST_ORDER_RE.search(msg_low):
        result.reference_latest_order = True

    # Deterministic speech-act guardrails (LLM cannot accidentally confirm on vague text or execute on inquiry)
    if CANCEL_PENDING_RE.search(msg_strip):
        result.message_type = "cancel_action"
        result.confirmed = False
    elif AMBIGUOUS_RE.match(msg_strip):
        result.message_type = "ambiguous"
        result.confirmed = False
    elif EXPLICIT_CONFIRM_RE.match(msg_strip):
        result.message_type = "confirm_action"
        result.confirmed = True
        if prior_intent and result.intent in ("general_inquiry", "order_inquiry"):
            result.intent = prior_intent
    elif ELIGIBILITY_INQUIRY_RE.search(msg_strip):
        result.message_type = "inquiry"
        result.confirmed = False
    elif CORRECTION_RE.search(msg_strip):
        result.message_type = "correction"
        result.confirmed = False
        if prior_intent and result.intent in ("general_inquiry", "order_inquiry"):
            result.intent = prior_intent
    else:
        result.confirmed = False
        # Distinguish pure supply of missing info (e.g. just "ORD-1001" or just an address)
        if explicit_oid and len(msg_strip.split()) <= 4 and prior_intent in ("refund_request", "cancellation", "address_change"):
            result.message_type = "provide_info"
            result.intent = prior_intent
        elif result.new_address and prior_intent == "address_change" and not re.search(r"\b(refund|cancel)\b", msg_low):
            result.message_type = "provide_info"
            result.intent = "address_change"
        elif result.intent in ("refund_request", "cancellation", "address_change"):
            if result.message_type not in ("inquiry", "correction", "provide_info"):
                result.message_type = "action_request"
        else:
            result.message_type = "inquiry"

    # Never allow confirmed=True if message_type != confirm_action
    if result.message_type != "confirm_action":
        result.confirmed = False

    # Preserve pending cancellation context when customer clearly cancels pending action
    if has_pending_confirmation and result.message_type == "cancel_action":
        result.confirmed = False

    return result
