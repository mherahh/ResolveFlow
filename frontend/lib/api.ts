export type AuthUser = {
  id: string;
  name: string;
  email: string;
  tier?: string;
  role: "customer" | "manager" | "admin";
};

export type PendingConfirmation = {
  id: string;
  ticket_id: string;
  customer_id: string;
  order_id: string;
  item: string;
  action: "refund" | "cancel" | "change_address";
  amount?: number | null;
  current_address?: string | null;
  new_address?: string | null;
  order_status?: string | null;
  reason?: string | null;
  policy_decision: "allow" | "needs_approval";
  eligibility_label: string;
  processing_info: string;
  status: "pending" | "processing" | "confirmed" | "cancelled" | "expired" | "superseded" | "failed";
  created_at: string;
  expires_at: string;
  resolved_at?: string | null;
};

export type MessageMetadata = {
  card_type?:
    | "confirmation"
    | "confirmation_reminder"
    | "confirmation_expired"
    | "action_cancelled"
    | "receipt"
    | "approval_pending"
    | "approval_rejected"
    | "execution_failed"
    | "verification_failed"
    | "order_selection";
  confirmation?: PendingConfirmation;
  confirmation_id?: string;
  order?: {
    id: string;
    item: string;
    amount: number;
    status: string;
    refunded: boolean;
  };
  order_id?: string;
  item?: string;
  amount?: number;
  order_status?: string;
  refunded?: boolean;
  shipping_address?: string;
  reference_id?: string | null;
  verified?: boolean;
  action?: string;
  detail?: string;
  reason?: string;
  reasons?: string[];
  available_orders?: Array<{
    id: string;
    item: string;
    amount: number;
    status: string;
    already_refunded: boolean;
    eligibility_note?: string;
  }>;
  requested_action_label?: string;
  state_changed?: boolean;
};

export type TicketMessage = {
  id: number;
  role: "customer" | "agent" | "system";
  text: string;
  metadata?: MessageMetadata | null;
  created_at: string;
};

export type AuditLogEntry = {
  id: number;
  action: string;
  order_id?: string | null;
  actor_id?: string | null;
  actor_role?: string | null;
  confirmation_id?: string | null;
  status: string;
  detail?: string | null;
  created_at: string;
};

export type TraceEvent = {
  node: string;
  summary: string;
  stage?: string;
  tool?: string;
  detail?: string;
  timestamp?: string;
};

export type Ticket = {
  id: string;
  customer_id: string | null;
  order_id: string | null;
  issue_type: string;
  status: string;
  resolution_mode?: string | null;
  message: string;
  created_at: string;
  updated_at?: string;
  final_response?: string | null;
  messages?: TicketMessage[];
  confirmations?: PendingConfirmation[];
  active_confirmation?: PendingConfirmation | null;
  audit_logs?: AuditLogEntry[];
  pending_action?: {
    plan: { action: string; order_id?: string; amount?: number; new_address?: string; reason: string; confidence?: number };
    policy?: { decision: string; reasons: string[] };
    confirmation_id?: string;
  } | null;
  handoff?: {
    issue?: string;
    customer?: { id?: string; name?: string };
    order?: { id?: string; status?: string; amount?: number } | null;
    sentiment?: string;
    proposed_action?: string;
    previous_actions?: string[];
    reason_for_escalation?: string;
  } | null;
  trace?: TraceEvent[];
};

export type OrderAction = {
  action: "refund" | "cancel" | "change_address";
  label: string;
  available: boolean;
  requires_approval: boolean;
  reason: string;
};

export type CustomerOrder = {
  id: string;
  item: string;
  amount: number;
  status: string;
  placed_at: string;
  delivered_at: string | null;
  shipping_address: string;
  refunded: boolean;
  refund?: {
    amount: number;
    reason: string;
    reference_id?: string | null;
    created_at: string;
  } | null;
  shipment?: {
    carrier: string;
    tracking_number: string;
    status: string;
    last_update: string;
    eta: string | null;
  } | null;
  actions?: OrderAction[];
};

export type DashboardStats = {
  scope: "all" | "customer";
  total: number;
  resolved: number;
  autonomous_resolved: number;
  autonomous_resolution_rate: number;
  resolution_rate: number;
  escalated: number;
  pending_approvals: number;
  awaiting_customer: number;
  failed: number;
  open: number;
  processing: number;
  intents: Record<string, number>;
  recent: Ticket[];
};

export type ActivityRun = {
  ticket_id: string;
  customer_id: string | null;
  order_id: string | null;
  issue_type: string;
  status: string;
  resolution_mode?: string | null;
  created_at: string;
  trace: TraceEvent[];
  confirmations: PendingConfirmation[];
  audit_logs: AuditLogEntry[];
};

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

const TOKEN_STORAGE_KEY = "rf_session_token";
let unauthorizedListener: (() => void) | null = null;

export function setUnauthorizedListener(fn: (() => void) | null) {
  unauthorizedListener = fn;
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // ignore storage errors
  }
}

let currentApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function getApiUrl(): string {
  return currentApiUrl;
}

export const API = currentApiUrl;

export async function fetchWithFallback(path: string, init?: RequestInit): Promise<Response> {
  const candidates: string[] = [];
  if (currentApiUrl) candidates.push(`${currentApiUrl}${path}`);
  if (!candidates.includes(`http://127.0.0.1:8000${path}`)) candidates.push(`http://127.0.0.1:8000${path}`);
  if (!candidates.includes(`http://localhost:8000${path}`)) candidates.push(`http://localhost:8000${path}`);
  if (!candidates.includes(path)) candidates.push(path);

  const token = getStoredToken();
  const headers = new Headers(init?.headers || {});
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const reqInit: RequestInit = {
    ...init,
    headers,
    credentials: "include",
  };

  let lastError: unknown = null;
  for (const url of candidates) {
    try {
      const res = await fetch(url, reqInit);
      if (res.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/register")) {
        if (unauthorizedListener) unauthorizedListener();
      }
      if (res.ok || res.status < 500) {
        if (url.startsWith("http://127.0.0.1:8000")) currentApiUrl = "http://127.0.0.1:8000";
        else if (url.startsWith("http://localhost:8000")) currentApiUrl = "http://localhost:8000";
        else if (url === path) currentApiUrl = "";
        return res;
      }
      // Return 5xx JSON responses from backend directly (e.g. failed DB execution) instead of retrying other URLs
      const ct = res.headers.get("content-type") || "";
      if (ct.includes("application/json")) {
        return res;
      }
      lastError = new ApiError(res.status, res.statusText);
    } catch (e) {
      lastError = e;
    }
  }

  throw lastError || new Error(`Failed to fetch ${path}`);
}

async function parseError(r: Response, fallbackPath: string): Promise<ApiError> {
  try {
    const data = await r.json();
    const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail || data);
    return new ApiError(r.status, detail);
  } catch {
    return new ApiError(r.status, `${r.status} ${fallbackPath}`);
  }
}

export async function get<T>(path: string): Promise<T> {
  const r = await fetchWithFallback(path, { cache: "no-store" });
  if (!r.ok) throw await parseError(r, path);
  return r.json();
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetchWithFallback(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw await parseError(r, path);
  return r.json();
}

// Auth & Demo helpers
export async function getAuthConfig(): Promise<{ demo_mode_enabled: boolean }> {
  return get("/api/auth/config");
}

export async function authMe(): Promise<{
  user: AuthUser;
  is_demo: boolean;
  expires_at: string;
  demo_mode_enabled: boolean;
}> {
  return get("/api/auth/me");
}

export async function authLogin(email: string, password: string): Promise<{
  token: string;
  user: AuthUser;
  is_demo: boolean;
  expires_at: string;
}> {
  return post("/api/auth/login", { email, password });
}

export async function authRegister(name: string, email: string, password: string): Promise<{
  token: string;
  user: AuthUser;
  is_demo: boolean;
  expires_at: string;
}> {
  return post("/api/auth/register", { name, email, password });
}

export async function authLogout(): Promise<{ status: string }> {
  return post("/api/auth/logout");
}

export async function getDemoAccounts(): Promise<AuthUser[]> {
  return get("/api/demo/accounts");
}

export async function demoSwitchAccount(account_id: string): Promise<{
  token: string;
  user: AuthUser;
  is_demo: boolean;
  expires_at: string;
}> {
  return post("/api/demo/switch", { account_id });
}

export async function getMyOrders(): Promise<CustomerOrder[]> {
  return get<CustomerOrder[]>("/api/customers/me/orders");
}

export async function getCustomerOrders(customerId: string): Promise<CustomerOrder[]> {
  return get<CustomerOrder[]>(`/api/customers/${customerId}/orders`);
}

export async function confirmPendingAction(confirmationId: string): Promise<{
  ok: boolean;
  ticket_id: string;
  order_id?: string;
  status: string;
  outcome: string;
  verified?: boolean;
  reference_id?: string | null;
  response: string;
  confirmation: PendingConfirmation;
  metadata?: MessageMetadata;
  reconfirmation_required?: boolean;
}> {
  return post(`/api/confirmations/${confirmationId}/confirm`, {});
}

export async function cancelPendingAction(confirmationId: string, note?: string): Promise<{
  ok: boolean;
  ticket_id: string;
  status: string;
  outcome: string;
  response: string;
  confirmation: PendingConfirmation;
  metadata?: MessageMetadata;
}> {
  return post(`/api/confirmations/${confirmationId}/cancel`, note ? { note } : {});
}

export async function resolveTicket(ticketId: string, note?: string): Promise<{ ticket_id: string; status: string }> {
  return post(`/api/tickets/${ticketId}/resolve`, note ? { note } : {});
}

export async function resetDemo(): Promise<{ status: string }> {
  return post<{ status: string }>("/api/admin/reset-demo");
}

/** Friendly labels for workflow nodes. Internal reasoning is never exposed. */
export const STEP_LABEL: Record<string, string> = {
  intake: "Understanding request & intent",
  retrieve: "Retrieving order & policy data",
  verify_order: "Verifying order selection",
  resolve: "Planning resolution strategy",
  policy: "Validating against company policy",
  confirmation: "Customer confirmation gate",
  execute: "Executing database transaction",
  verify: "Verifying updated database state",
  approval: "Human manager approval",
  escalate: "Escalating to human specialist",
  respond: "Composing verified response",
};

export const STAGE_LABEL: Record<string, { label: string; color: string }> = {
  intent_detected: { label: "Intent Detected", color: "bg-blue-100 text-blue-800" },
  order_retrieved: { label: "Order Retrieved", color: "bg-indigo-100 text-indigo-800" },
  action_proposed: { label: "Action Proposed", color: "bg-purple-100 text-purple-800" },
  policy_validated: { label: "Policy Validated", color: "bg-amber-100 text-amber-800" },
  confirmation_requested: { label: "Confirmation Requested", color: "bg-violet-100 text-violet-800" },
  customer_authorized: { label: "Customer Authorized", color: "bg-sky-100 text-sky-800" },
  confirmation_cancelled: { label: "Cancelled by Customer", color: "bg-slate-200 text-slate-700" },
  confirmation_expired: { label: "Confirmation Expired", color: "bg-orange-100 text-orange-800" },
  approval_pending: { label: "Awaiting Manager", color: "bg-amber-100 text-amber-800" },
  manager_approved: { label: "Manager Approved", color: "bg-emerald-100 text-emerald-800" },
  action_executed: { label: "Executed", color: "bg-emerald-100 text-emerald-800" },
  action_verified: { label: "DB Verified", color: "bg-teal-100 text-teal-800" },
  action_failed: { label: "Failed", color: "bg-rose-100 text-rose-800" },
  escalated: { label: "Escalated", color: "bg-rose-100 text-rose-800" },
  response_sent: { label: "Final Response", color: "bg-slate-100 text-slate-700" },
};

export function titleCase(s: string) {
  if (!s) return "";
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function formatCurrency(amount?: number | null) {
  if (amount === undefined || amount === null) return "$0.00";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

export function formatTimeAgo(isoString: string) {
  try {
    const normalized = isoString.endsWith("Z") ? isoString : `${isoString}Z`;
    const date = new Date(normalized);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);
    if (diffSec < 60) return "just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return date.toLocaleDateString();
  } catch {
    return isoString;
  }
}
