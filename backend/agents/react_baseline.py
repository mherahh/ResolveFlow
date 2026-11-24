"""Baseline A: ONE basic ReAct agent with direct access to every tool, including the write tools.
No policy guard, no verifier, no idempotency discipline (each write gets a fresh random key)."""
import uuid
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from prompts.prompts import BASELINE_SYSTEM_PROMPT
from tools import write_tools
from tools.read_tools import READ_TOOLS


class RunFlags:
    escalated = False


def build_baseline(llm, flags: RunFlags):
    @tool
    def issue_refund(order_id: str, amount: float, reason: str) -> dict:
        """Refund an order."""
        return write_tools.issue_refund(order_id, amount, reason, uuid.uuid4().hex)

    @tool
    def cancel_order(order_id: str) -> dict:
        """Cancel an order."""
        return write_tools.cancel_order(order_id, uuid.uuid4().hex)

    @tool
    def change_address(order_id: str, new_address: str) -> dict:
        """Change an order's delivery address."""
        return write_tools.change_address(order_id, new_address, uuid.uuid4().hex)

    @tool
    def escalate_to_human(reason: str) -> str:
        """Hand the case to a human support agent."""
        flags.escalated = True
        return "A human agent has been notified."

    return create_react_agent(llm, READ_TOOLS + [issue_refund, cancel_order, change_address, escalate_to_human])


def run_baseline(llm, message: str, customer_id: str | None):
    flags = RunFlags()
    agent = build_baseline(llm, flags)
    out = agent.invoke(
        {"messages": [("system", BASELINE_SYSTEM_PROMPT.format(customer_id=customer_id)), ("human", message)]},
        {"recursion_limit": 14},
    )
    text = out["messages"][-1].content
    return {"response": text if isinstance(text, str) else str(text), "human_routed": flags.escalated, "error": None}
