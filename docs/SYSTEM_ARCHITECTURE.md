# System Architecture

ResolveFlow has three parts: a Next.js frontend, a FastAPI backend that runs one LangGraph workflow, and a SQL database.

```mermaid
flowchart LR
    U[Browser: Next.js + Tailwind] -- "HTTP / NDJSON stream" --> API[FastAPI main.py]
    API --> SVC[services.py: tickets, save results]
    API --> G[LangGraph workflow graph/workflow.py]
    G --> LLM[(LLM via LangChain: OpenAI or OpenRouter)]
    G --> RT[tools/read_tools.py]
    G --> PG[policies/policy_guard.py]
    G --> WT[tools/write_tools.py]
    RT --> DB[(SQLite / PostgreSQL)]
    WT --> DB
    SVC --> DB
```

## Who is allowed to do what

| Component | Uses LLM? | Can read DB? | Can write DB? |
|---|---|---|---|
| Intake | yes (structured output) | no | no |
| Context retrieval | no | yes (read tools) | no |
| Resolution | yes (structured output) | no (gets retrieved data in its prompt) | no |
| Policy Guard | no | no (gets order data) | no |
| Action Executor | no | no | **yes, the only one** |
| Verifier | no | yes | no |
| Escalation | no | no | no |
| Responder | yes (text) | no (gets verified facts only) | no |

## Workflow

```mermaid
flowchart TD
    A[Customer message] --> I[intake]
    I -->|angry or error| E[escalate]
    I --> R[retrieve]
    R -->|order missing or not on this account| N[needs_info]
    R --> S[resolve]
    S -->|escalate or low confidence| E
    S -->|action none| P[respond]
    S --> G[policy]
    G -->|allow| X[execute]
    G -->|needs approval| AP[approval]
    G -->|deny, first time| S
    G -->|deny again| E
    X --> V[verify]
    V -->|ok| P
    V -->|failed, first time| G
    V -->|failed again| E
    AP --> P
    N --> P
    E --> P
    P --> Z[Customer reply]
```

## Database

```mermaid
erDiagram
    customers ||--o{ orders : places
    customers ||--o{ refunds : receives
    orders ||--o{ shipments : has
    orders ||--o{ refunds : has
    tickets }o--|| customers : "opened by"
    tickets ||--o{ actions_log : "caused"
```
