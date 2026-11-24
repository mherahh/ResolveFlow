# ResolveFlow: Autonomous AI Customer Support Platform

ResolveFlow is a production-grade multi-agent AI customer support system designed for modern e-commerce and retail. It autonomously resolves routine customer requests—order status tracking, shipment delivery lookups, return and refund processing, order cancellations, and shipping address modifications—grounded in live database state, verified code-level policies, and safe human-in-the-loop escalation paths.

Unlike basic conversational chatbots that hallucinate or claim actions have succeeded without executing them, ResolveFlow decouples language understanding from execution: **the LLM understands intent, plans resolution, and generates natural customer replies, while deterministic Python policy engines enforce constraints, execute atomic database changes, and verify state before responding.**

---

## Key Features

- **Multi-Agent Orchestration with LangGraph**: StateGraph pipeline with specialized roles (`Intake`, `Retrieve`, `Resolve`, `Policy Guard`, `Execute`, `Verify`, `Approval`, `Escalate`, `Respond`).
- **Multi-Turn Persistent Conversations**: Preserves conversation history across follow-ups, resolving order references and context without requiring customers to re-state order IDs.
- **Order Verification & Privacy Guard**: Requires both Order ID and matching customer email address (`lookup_order(order_id, email)`). Blocks unverified orders at the graph routing level without revealing whether an order ID exists in the system.
- **Customer Confirmation Safeguards**: Destructive actions (refunds, cancellations) require explicit customer confirmation (`yes` / `proceed`) before execution, ensuring business safety and realistic SaaS customer interaction.
- **Database Status Memory**: Accurately recognizes and remembers if an order was already refunded or cancelled, providing clear policy explanations instead of re-processing or crashing.
- **Deterministic Policy Guardrails**: Automatic refunds under $100 within a 30-day window; high-value refunds (> $100) automatically routed to human managers for approval; cancellations and address changes restricted to 'processing' orders.
- **Idempotent Write Tools**: Cryptographic idempotency keys prevent duplicate refunds or double cancellations under network retries or concurrent requests.
- **Human-in-the-Loop Triage Hub**: Dedicated manager interface to review high-value pending approvals, examine structured escalation summaries, and approve/reject with audit notes.
- **Modern SaaS Dashboard & Inbox**: Clean, professional UI inspired by Intercom and Linear, featuring live step-by-step agent telemetry, customer order drawer, quick confirmation response pills, search and status filters, and one-click demo data reset.
- **Comprehensive Evaluation & Test Suite**: 41 automated pytest unit and integration tests (passing with 0 errors), plus a 28-scenario evaluation harness comparing the controlled pipeline against a standard ReAct baseline.

---

## Technology Stack

- **Frontend**: Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS.
- **Backend**: Python 3.12 / 3.13, FastAPI, SQLAlchemy 2, Pydantic v2.
- **Agent Orchestration**: LangChain, LangGraph StateGraph.
- **LLM Providers**: OpenRouter (e.g. `openai/gpt-4o-mini`, `anthropic/claude-3.5-sonnet`) or native OpenAI.
- **Database**: SQLite (local development) / PostgreSQL (production).

---

## Multi-Agent Architecture

```
Customer Message
       │
       ▼
 ┌───────────┐
 │  Intake   │  Extracts intent, sentiment, order ID, address; utilizes past turns for context.
 └─────┬─────┘
       ▼
 ┌───────────┐
 │ Retrieve  │  Loads customer record, order data, shipment tracking, refund history, and policy.
 └─────┬─────┘
       ▼
 ┌───────────┐
 │  Resolve  │  Proposes an ActionPlan based strictly on retrieved facts (read-only; no write access).
 └─────┬─────┘
       ▼
 ┌──────────────┐
 │ Policy Guard │  Pure Python validation (returns allow, needs_approval, or deny).
 └─────┬────────┘
       │
       ├─► [Needs Approval] ─► Approval Node (Holds ticket for human manager review)
       │
       ├─► [Policy Denied]  ─► 1 correction attempt with feedback ─► Else Escalation
       │
       ▼ [Allowed]
 ┌───────────┐
 │  Execute  │  Atomic idempotent database write (refund, cancel, or change_address).
 └─────┬─────┘
       ▼
 ┌───────────┐
 │  Verify   │  Re-reads database to ensure the mutation was actually applied.
 └─────┬─────┘
       ▼
 ┌───────────┐
 │  Respond  │  Composes empathetic, professional reply strictly using verified facts.
 └───────────┘
```

*Note: Any step can trigger safe human escalation (`Escalate`) with a structured handoff package if sentiment is hostile, uncertainty is high, or verification fails.*

---

## Database Schema

Seven core relational tables:
1. `customers`: Customer identity, name, email, and loyalty tier (`standard`, `premium`).
2. `orders`: Order ID, item description, amount, status (`processing`, `shipped`, `delivered`, `cancelled`), timestamps, and address.
3. `shipments`: Carrier (`TCS`, `Leopards`), tracking number, delivery status, and live ETA.
4. `refunds`: Processed refunds with amount, reason, timestamp, and unique idempotency keys.
5. `tickets`: Support cases with customer ID, issue type, status, proposed action, and trace logs.
6. `ticket_messages`: Multi-turn chronological message thread (`customer`, `agent`, `system`).
7. `actions_log`: Immutable audit log of every state change with status and idempotency keys.

---

## Project Structure

```
resolveflow/
├── frontend/                     # Next.js 14 App Router Frontend
│   ├── app/                      # Dashboard (/), Inbox (/chat), Tickets (/tickets, /tickets/[id]),
│   │                             # Approvals (/approvals), Activity (/activity)
│   ├── components/               # Sidebar, Card, StatusBadge, Timeline
│   ├── lib/api.ts                # Typed API client, models, and utility helpers
│   └── package.json
├── backend/                      # FastAPI & LangGraph Backend
│   ├── agents/                   # Intake, Resolution, Responder, Verifier, Escalation, ReAct
│   ├── database/                 # SQLAlchemy models, db session engine, seed script
│   ├── graph/                    # LangGraph StateGraph workflow and state schema
│   ├── models/                   # Pydantic validation schemas
│   ├── policies/                 # Policy rules and deterministic PolicyGuard engine
│   ├── prompts/                  # Centralized system and few-shot prompts
│   ├── tools/                    # Read tools (queries) and Write tools (idempotent mutations)
│   ├── evaluation/               # 28 evaluation scenarios, metrics, and report generator
│   ├── tests/                    # Pytest test suite (API, core rules, graph, eval)
│   ├── config.py                 # Pydantic Settings and LLM model factory
│   ├── demo_stub_server.py       # Standalone keyword stub server for zero-key demo
│   ├── main.py                   # FastAPI REST and SSE streaming endpoints
│   ├── services.py               # Database glue, ticket generator, and session helpers
│   └── requirements.txt
├── docs/                         # SYSTEM_ARCHITECTURE.md & TECHNICAL_DOCUMENTATION.md
├── .env.example
├── README.md
└── render.yaml
```

---

## Quickstart & Installation

### Prerequisites
- Python 3.12+ (tested through 3.13)
- Node.js 18+ (tested with Node 20)

### 1. Backend Setup
```bash
cd backend
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On Linux / macOS:
source .venv/bin/activate


cp ../.env.example .env    # Configure your LLM provider and keys in .env
python -m database.seed    # Initialize SQLite database with sample customers & orders
```

### 2. Frontend Setup
```bash
cd frontend
npm install
cp .env.local.example .env.local    # Default points to http://localhost:8000
```

---

## Environment Configuration (`backend/.env`)

```ini
# Provider: "openrouter" or "openai"
LLM_PROVIDER=openrouter

# Model Name
LLM_MODEL=openai/gpt-4o-mini

# API Keys (leave the unused one blank)
OPENROUTER_API_KEY=your-openrouter-api-key
OPENAI_API_KEY=your-openai-api-key

# Database (defaults to SQLite)
DATABASE_URL=sqlite:///./resolveflow.db

# CORS Origin
FRONTEND_ORIGIN=http://localhost:3000
```

---

## Running the Application

### Option A: Standard Autonomous Mode (with real LLM)
In terminal 1 (Backend):
```bash
cd backend
uvicorn main:app --reload --port 8000
```

In terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```
Open **http://localhost:3000** in your browser.

### Option B: Zero-Credential Demo Mode (No API Key Required)
If you do not have an API key and want to click through the full interface, run the mock stub server:
```bash
cd backend
uvicorn demo_stub_server:app --port 8000
```
This serves rule-based keyword responses (`[stub reply: resolved]`) while exercising the complete frontend, database, and approval workflow.

---

## Running Automated Tests

Run the full pytest suite (35 unit and integration tests):
```bash
cd backend
pytest tests -v
```
All 35 tests run against injected stubs and mocks without consuming paid tokens or requiring internet access.

---

## Running the Evaluation Benchmark

To run the automated 28-scenario evaluation benchmark:

**Dry-Run / Wiring Smoke Test:**
```bash
cd backend
python -m evaluation.run_eval --system workflow --trials 1 --stub
```

**Full Benchmark with Live LLM Judge (Requires API Key):**
```bash
cd backend
python -m evaluation.run_eval --system both --trials 5 --judge
```
Results and metrics (Grounding score, Policy compliance, Escalation accuracy) will be generated in `backend/evaluation/results/report.md`.

---

## REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | `GET` | Health check and current LLM provider/model metadata |
| `/api/customers` | `GET` | List sample customer accounts |
| `/api/customers/{id}/orders` | `GET` | Retrieve order history and carrier tracking for a customer |
| `/api/chat` | `POST` | Synchronous chat endpoint (returns ticket ID, outcome, response, trace) |
| `/api/chat/stream` | `POST` | Server-Sent NDJSON streaming endpoint for live step telemetry |
| `/api/dashboard` | `GET` | Real-time support metrics, resolution rate, and intent breakdown |
| `/api/tickets` | `GET` | Query tickets with filters (`status`, `search`, `customer_id`) |
| `/api/tickets/{id}` | `GET` | Complete ticket detail including full multi-turn conversation messages |
| `/api/approvals` | `GET` | List tickets pending manager approval or human escalation |
| `/api/approvals/{id}` | `POST` | Approve or reject a pending high-value financial action with optional note |
| `/api/tickets/{id}/resolve` | `POST` | Mark an escalated or open ticket as resolved with optional note |
| `/api/activity` | `GET` | Multi-agent execution telemetry and tool logs across recent runs |
| `/api/admin/reset-demo` | `POST` | Reset database state back to initial seed data |

---

## Deploying ResolveFlow on Vercel & Production Cloud

ResolveFlow is architected with a decoupled frontend (Next.js) and backend (FastAPI / LangGraph). The recommended production setup is:
- **Frontend**: Hosted on **Vercel** (global edge CDN, automatic HTTPS, optimized Next.js builds).
- **Backend API**: Hosted on **Render**, **Railway**, **Fly.io**, or **AWS ECS** (long-running Python processes for SSE streaming).
- **Database**: Managed **PostgreSQL** (e.g. Supabase, Neon, or AWS RDS).

---

### Step-by-Step Vercel Deployment (Frontend)

#### 1. Push Code to Git
Ensure your repository is pushed to GitHub or GitLab:
```bash
git add .
git commit -m "feat: resolveflow production upgrade"
git push origin main
```

#### 2. Import into Vercel
1. Log in to your [Vercel Dashboard](https://vercel.com).
2. Click **Add New...** → **Project**.
3. Select your repository from GitHub / GitLab and click **Import**.

#### 3. Configure Project Settings on Vercel
- **Root Directory**: Click **Edit** and choose the `frontend` folder (`resolveflow/frontend` or `frontend` depending on your repo root).
- **Framework Preset**: Vercel will automatically detect **Next.js**.
- **Build Command**: `next build` (default).
- **Output Directory**: `.next` (default).
- **Install Command**: `npm install` (default).

#### 4. Configure Environment Variables on Vercel
In the **Environment Variables** section, add:
| Key | Value | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://your-backend-api.onrender.com` | The public HTTPS URL of your deployed FastAPI backend. |

*(For local testing with Vercel CLI, this defaults to `http://127.0.0.1:8000`).*

#### 5. Click Deploy
Click **Deploy**. In under 2 minutes, Vercel will build and publish your Next.js frontend with a public `https://<your-project>.vercel.app` URL.

---

### Step-by-Step Backend Deployment (Render / Railway / Fly.io)

Your Python backend must be running on a cloud service accessible by Vercel:

#### Example: Deploying Backend to Render
1. Create a **New Web Service** on [Render.com](https://render.com).
2. Connect your repository.
3. Configure settings:
   - **Root Directory**: `backend`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt && python -m database.seed`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Set Environment Variables in Render:
   - `LLM_PROVIDER`: `openrouter`
   - `LLM_MODEL`: `openai/gpt-4o-mini`
   - `OPENROUTER_API_KEY`: `your-openrouter-key`
   - `FRONTEND_ORIGIN`: `https://your-project.vercel.app`
   - `DATABASE_URL`: `sqlite:///./resolveflow.db` (or your managed PostgreSQL connection string `postgresql://user:pass@host:5432/dbname`)
5. Deploy. Render will give you a public URL (e.g., `https://resolveflow-api.onrender.com`).
6. Copy this URL and set it as `NEXT_PUBLIC_API_URL` in your Vercel project settings.

---

### Verifying Production Deployment

After deployment:
1. Open your Vercel URL (`https://<your-app>.vercel.app`).
2. Verify that the **KPI Dashboard** displays active stats from the backend.
3. Open the **Conversation Inbox (`/chat`)**, select a sample scenario or type:
   > *"I want a refund for ORD-1001, email ayesha@example.com"*
4. Verify that:
   - The system verifies the order with matching email.
   - The chatbot asks for customer confirmation before processing.
   - Responding *"Yes, please proceed"* immediately executes the atomic refund.
   - Follow-up attempts to refund `ORD-1001` recognize the order is already refunded.
