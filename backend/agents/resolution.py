"""Resolution agent: reads retrieved data and proposes an ActionPlan. It has no write access."""
import json
from models.schemas import ActionPlan
from prompts.prompts import RESOLUTION_PROMPT


def run_resolution(
    llm,
    message: str,
    intent: str,
    context: dict,
    new_address: str | None,
    feedback: str | None,
    message_type: str = "action_request",
) -> ActionPlan:
    chain = llm.with_structured_output(ActionPlan)
    msgs = RESOLUTION_PROMPT.format_messages(
        message=message,
        intent=intent,
        message_type=message_type,
        order_json=json.dumps(context.get("order")),
        shipment_json=json.dumps(context.get("shipment")),
        refunds_json=json.dumps(context.get("refunds")),
        policy_text=(context.get("policy") or {}).get("policy"),
        new_address=new_address,
        feedback=feedback or "none",
    )
    return chain.invoke(msgs)
