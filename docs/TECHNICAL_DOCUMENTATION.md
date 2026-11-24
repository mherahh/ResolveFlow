# ResolveFlow Technical Documentation

This document describes the code as it is in this repository. Where something is simpler than you might expect, it says so.

## 1. Problem statement

Small online shops get many repeated support requests (where is my order, refund, cancel, change address). Simple chatbots only give canned answers. LLMs can act, but they can also invent order details or claim an action happened when it did not. ResolveFlow lets an LLM understand and plan, while ordinary Python code controls what is allowed, what is changed, and what is reported.

## 2. Objectives

1. Understand customer messages and classify them.
2. Use real database records, never invented ones.
3. Check every proposed action against written policies in Python.
4. Execute only approved actions, safely (idempotency keys).
5. Verify the result in the database before telling the customer it worked.
6. Hand over to a human, with a summary, when the case is risky or unclear.
7. Measure behaviour with an evaluation instead of assuming it.

## 3. Requirements

**Functional**: chat endpoint, tickets, dashboard counts, human approval and rejection, workflow trace per ticket, evaluation runner.

**Non-functional**: simple and readable code; no secrets in code (`.env`); model and provider are configuration; deterministic code for anything that changes data; tests that run without an API key.

## 4. Architecture

See `SYSTEM_ARCHITECTURE.md` for diagrams. Folders:

| Folder | Purpose |
|---|---|
| `backend/database` | SQLAlchemy models, engine, seed script |
| `backend/tools` | `read_tools.py` (5 LangChain tools) and `write_tools.py` (3 write functions) |
| `backend/policies` | `policy_config.py` (numbers and text) and `policy_guard.py` (decision logic) |
| `backend/agents` | intake, resolution, responder, escalation, verifier, ReAct baseline |
| `backend/graph` | `state.py` (shared state) and `workflow.py` (the StateGraph) |
| `backend/models` | Pydantic schemas |
| `backend/prompts` | all prompt templates |
| `backend/evaluation` | scenarios, metrics, runner, report writer |
| `backend/tests` | pytest tests |

## 5. Agent responsibilities

- **Intake** (`agents/intake.py`): LLM with structured output (`IntakeResult`: intent, sentiment, order_id, new_address). A regex also extracts the order ID from the raw message and takes priority over the model's value.
- **Context retrieval** (`retrieve_node`): plain Python that calls the read tools. It loads customer, refund history, order, shipment and the relevant policy text. If the order belongs to a different customer it is treated as not found, so other customers' data is never exposed.
- **Resolution** (`agents/resolution.py`): LLM with structured output (`ActionPlan`: action, order_id, amount, new_address, reason, confidence). It receives the retrieved data in its prompt and has no tools and no database access. *Note:* tool calling by the LLM itself happens only in the ReAct baseline. In the controlled workflow, the read tools are called by the retrieval node.
- **Policy Guard** (`policies/policy_guard.py`): plain Python. Returns `allow`, `needs_approval` or `deny` with reasons.
- **Action Executor** (`execute_node`): the only code that imports `write_tools`.
- **Verifier** (`agents/verifier.py`): re-reads the order and checks the expected change.
- **Escalation** (`agents/escalation.py`): builds a handoff dictionary (issue, customer, order, sentiment, proposed action, previous steps, reason). No LLM.
- **Responder** (`agents/responder.py`): LLM writes the reply from a small dictionary of verified facts. If the LLM call fails, a fixed template is used.

## 6. LangChain

- `ChatOpenAI` is created in `config.get_llm()`; OpenRouter uses the same class with `base_url="https://openrouter.ai/api/v1"`.
- `ChatPromptTemplate` for every prompt (`prompts/prompts.py`).
- `llm.with_structured_output(PydanticModel)` for Intake and Resolution.
- `@tool` for the five read tools (`tools/read_tools.py`), called with `.invoke()`.
- `create_react_agent` (from `langgraph.prebuilt`) for the baseline.

## 7. LangGraph

One `StateGraph(SupportState)` in `graph/workflow.py`. State is a `TypedDict` (`graph/state.py`). Each node returns only the keys it changes; `trace` uses an `operator.add` reducer so events accumulate.

Conditional routing:

| After | Condition | Goes to |
|---|---|---|
| intake | error | escalate |
| intake | sentiment is angry | escalate |
| retrieve | order missing or not on this account (non-general intent) | needs_info, then respond |
| resolve | error, action `escalate`, or confidence below 0.6 | escalate |
| resolve | action `none` | respond |
| policy | allow | execute |
| policy | needs_approval | approval, then respond |
| policy | deny (first time) | resolve again with the policy feedback |
| policy | deny (second time) | escalate |
| verify | ok | respond |
| verify | failed (first time) | policy, then execute again |
| verify | failed (second time) | escalate |

Escalation uses small helper nodes that only set the reason, then go to one `escalate` node.

## 8. Shared state

Key fields: `message`, `customer_id`, `ticket_id`, `intake`, `context`, `plan`, `policy`, `exec_result`, `verification`, `attempt` (policy corrections used), `verify_attempts`, `policy_feedback`, `outcome`, `handoff`, `final_response`, `trace`.

## 9. Database

Tables: `customers`, `orders`, `shipments`, `refunds`, `tickets`, `actions_log`. Relationships between tickets/actions_log and other tables are logical (stored IDs), not enforced foreign keys. `database/seed.py` creates synthetic data: nine orders covering processing, shipped (normal and delayed), delivered (recent, old, high value, already refunded) and cancelled.

## 10. Tool calling

Read tools take simple arguments and return dictionaries; they never write. Write functions (`issue_refund`, `cancel_order`, `change_address`) are normal Python functions, not exposed to the Resolution LLM. In the baseline agent they are wrapped as tools on purpose, to represent a basic agent with full access.

## 11. Policy validation

Numbers and text live in `policy_config.py`. The guard checks: customer identified, order exists and belongs to the customer, status allowed for the action, one refund per order, 30-day return window after delivery, refund amount equals the order amount, system limit ($500) and automatic limit ($100, above which a human must approve). Address changes need a complete new address.

## 12. Idempotency

Each write needs an idempotency key. The executor builds it as `ticket:action:order` (plus `:retryN` on a retry). `actions_log.idempotency_key` is unique; if a key was already used, the write returns `duplicate=True` and changes nothing. A new ticket asking for the same refund gets a different key, but the Policy Guard denies it because the order is already refunded.

## 13. Verification

After a write, `verify()` re-reads the order: refund (`refunded` is 1), cancel (status is `cancelled`), address (matches the requested one). A retry after failed verification goes back through the Policy Guard, so a refund that did in fact succeed would be denied as "already refunded" instead of being repeated. After two failed verifications the case is escalated.

## 14. Human approval and escalation

Approval: the graph ends with outcome `pending_approval`, and the plan is saved on the ticket (`pending_action`). `POST /api/approvals/{id}` with `approve: true` re-runs the graph with `approved=True`, skipping intake and resolution: it re-retrieves the order, re-runs the Policy Guard (which now accepts the `needs_approval` case), then executes and verifies. Rejecting sets the ticket to `rejected` and nothing is executed. This is stored in the database rather than using LangGraph interrupts and a checkpointer, to keep it simple.

Escalation: reasons are angry customer, error, uncertain plan, repeated policy denial, failed verification. The handoff is saved on the ticket and shown on the Approvals page.

## 15. API endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/health` | status, provider, model |
| `GET /api/customers` | customers for the chat selector |
| `POST /api/chat` | run the workflow, return the final result and trace |
| `POST /api/chat/stream` | same, as newline-delimited JSON: one `event` line per workflow step, then `final` |
| `GET /api/dashboard` | totals and recent tickets |
| `GET /api/tickets`, `GET /api/tickets/{id}` | list and detail (detail includes trace, handoff, pending action) |
| `GET /api/approvals` | tickets that are pending approval or escalated |
| `POST /api/approvals/{id}` | body `{"approve": true/false}` |
| `POST /api/tickets/{id}/resolve` | a human marks an escalated ticket handled |
| `GET /api/activity` | recent workflow traces |
| `POST /api/admin/reset-demo` | reset sample data (development only; protect or remove before real deployment) |

**Failures:** if the workflow crashes (for example a missing API key or the model provider is down), the ticket is set to `escalated` with a system-error handoff, the stream returns an `error` line (or `/api/chat` returns 503), and the customer sees a message that a human will follow up. Tickets are never left stuck in `processing`.

## 16. Frontend and backend communication

The frontend calls the API with `fetch` (`frontend/lib/api.ts`), base URL from `NEXT_PUBLIC_API_URL`. The chat page reads the `/api/chat/stream` response with a stream reader and shows a friendly label per step. Only step names are shown in chat; the model's reasoning is never sent to the chat view. CORS allows `FRONTEND_ORIGIN` and `http://localhost:3000`.

## 17. Testing

`python -m pytest tests -q` (from `backend/`). 31 tests, none need an API key:
- `test_core.py`: tools, Policy Guard rules, writes, idempotency, verifier.
- `test_graph.py`: the whole graph using `tests/fake_llm.py`, a keyword-based stand-in for the LLM (refund, cancel, approval, wrong owner, angry customer, silent write failure, duplicates).
- `test_api.py`: endpoints, streaming, approval and rejection.
- `test_eval.py`: metrics, the harness, and the ReAct baseline wiring with a scripted tool-calling model.

These tests check the code around the LLM. They do not measure how well a real model understands customers.

## 18. Evaluation

`evaluation/scenarios.py` has 28 scenarios (order and shipment questions, eligible and ineligible refunds, approval-needed refund, cancellations, address changes, missing or wrong order, angry customers, duplicates, silent write failures). Each scenario starts from a freshly seeded database.

Systems compared:
- **A, `react_baseline`**: one ReAct agent with the read tools, the three write tools (each call gets a random key, so no idempotency) and an `escalate_to_human` tool. No Policy Guard, no Verifier.
- **B, `workflow`**: the LangGraph workflow.

Metrics (all computed from real database state and replies):
1. **Task success rate**: the only database change is the expected one (or none), human routing matches expectation, and no error occurred.
2. **Grounding rate**: every amount, order ID, date and tracking number in the reply appears in the database, the customer's message or the policy text.
3. **Duplicate action rate**: the same write took effect more than once. A safe retry after a write that did not apply is not counted.
4. **Half-finished task rate**: the database changed but the run ended escalated, errored, or without a reply.
5. **Escalation precision and recall**: "human routed" means escalated or pending approval.
6. **pass^k**: share of scenarios where all of the first k trials succeeded (default k=5).

Optional `--judge` adds an LLM rating of reply quality (1 to 5).

Run: `python -m evaluation.run_eval --system both --trials 5 --judge`. It writes `evaluation/results/results.json` and `report.md`. **No real-model results are included in this repository**, because the development environment had no access to an LLM API. Run the command with your own key to get them. The `--stub` flag runs the workflow with the keyword stand-in only to test the harness; its report is labelled as such and must not be cited.

## 19. Deployment

- Frontend: Vercel, root directory `frontend`, env var `NEXT_PUBLIC_API_URL` set to the backend URL.
- Backend: any Python host (`render.yaml` is an example). Set `LLM_*`, API key, `DATABASE_URL`, `FRONTEND_ORIGIN`. Use PostgreSQL (`pip install psycopg2-binary`), because a SQLite file on most free hosts is wiped on redeploy. The tables are created on startup and sample data is inserted if the database is empty.
- Deployment configuration was not tested in the development environment, and PostgreSQL was not tested.

## 20. Limitations

- Not run against a live LLM here, so prompt quality and model behaviour are unmeasured until you run the evaluation.
- No login: the chat page lets you pick which sample customer you are. Real use needs authentication.
- Synthetic data and fake refunds only; no payment system.
- One policy set; amounts must equal the full order amount (no partial refunds).
- Approval is stored in the database; there is no reviewer authentication or audit of who approved.
- `/api/admin/reset-demo` is open and meant for development.
- `datetime.utcnow()` is used (works, but produces deprecation warnings on newer Python).
- Failure injection (`SIMULATE_SILENT_FAILURE`) exists in `write_tools.py` for evaluation and testing only.
