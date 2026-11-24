"use client";
import { useEffect, useRef, useState } from "react";
import StatusBadge from "@/components/StatusBadge";
import {
  ApiError,
  CustomerOrder,
  MessageMetadata,
  PendingConfirmation,
  STEP_LABEL,
  Ticket,
  cancelPendingAction,
  confirmPendingAction,
  fetchWithFallback,
  formatCurrency,
  formatTimeAgo,
  get,
  getMyOrders,
  titleCase,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Msg = {
  id?: number | string;
  role: "customer" | "agent" | "system";
  text: string;
  ticket?: string;
  order?: string | null;
  outcome?: string;
  metadata?: MessageMetadata | null;
  created_at?: string;
};

const QUICK_PROMPTS = [
  { label: "Refund an order", text: "Refund my order." },
  { label: "Check refund eligibility", text: "I want to know if I can get a refund." },
  { label: "Cancel an order", text: "Can you cancel my order?" },
  { label: "Change shipping address", text: "I want to change my shipping address." },
  { label: "Return & shipping policy", text: "What is your return policy?" },
];

export default function Chat() {
  const { user, isDemo } = useAuth();
  const [orders, setOrders] = useState<CustomerOrder[]>([]);
  const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);
  const [selectedOrderContext, setSelectedOrderContext] = useState<string | null>(null);

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [activeTicketId, setActiveTicketId] = useState<string | null>(null);
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);

  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [steps, setSteps] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirmBusyId, setConfirmBusyId] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  async function refreshOrders() {
    try {
      const data = await getMyOrders();
      setOrders(data);
    } catch {
      setOrders([]);
    }
  }

  async function refreshTickets(autoSelectFirst = false) {
    try {
      const list = await get<Ticket[]>("/api/tickets");
      setTickets(list);
      if (autoSelectFirst) {
        if (list.length > 0) {
          loadTicketThread(list[0].id);
        } else {
          startNewTicket();
        }
      }
    } catch {
      setTickets([]);
    }
  }

  // Reload whenever authenticated user changes
  useEffect(() => {
    if (!user) return;
    setActiveTicketId(null);
    setActiveTicket(null);
    setMsgs([]);
    setSelectedOrderContext(null);
    setErrorBanner("");
    refreshOrders();
    refreshTickets(true);
  }, [user?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, steps]);

  async function loadTicketThread(tid: string) {
    setActiveTicketId(tid);
    setErrorBanner("");
    try {
      const full = await get<Ticket>(`/api/tickets/${tid}`);
      setActiveTicket(full);
      setSelectedOrderContext(full.order_id ?? null);
      if (full.messages && full.messages.length > 0) {
        setMsgs(
          full.messages.map((m) => ({
            id: m.id,
            role: m.role,
            text: m.text,
            ticket: full.id,
            order: full.order_id,
            metadata: m.metadata ?? null,
            created_at: m.created_at,
          }))
        );
      } else {
        const initial: Msg[] = [];
        if (full.message) {
          initial.push({ role: "customer", text: full.message, ticket: full.id, created_at: full.created_at });
        }
        if (full.final_response) {
          initial.push({
            role: "agent",
            text: full.final_response,
            ticket: full.id,
            order: full.order_id,
            outcome: full.status,
            created_at: full.created_at,
          });
        }
        setMsgs(initial);
      }
    } catch (e) {
      setErrorBanner(e instanceof ApiError ? e.detail : "Could not load support conversation.");
    }
  }

  function startNewTicket() {
    setActiveTicketId(null);
    setActiveTicket(null);
    setMsgs([]);
    setSteps([]);
    setSelectedOrderContext(null);
    setErrorBanner("");
  }

  async function send(text: string, orderContextId?: string) {
    if (!text.trim() || busy || confirmBusyId) return;

    setBusy(true);
    setErrorBanner("");
    setSteps([]);
    setInput("");

    const tempCustomerMsg: Msg = {
      role: "customer",
      text: text.trim(),
      created_at: new Date().toISOString(),
    };
    setMsgs((m) => [...m, tempCustomerMsg]);

    try {
      const payload: { message: string; ticket_id?: string; order_id?: string } = {
        message: text.trim(),
      };
      if (activeTicketId) payload.ticket_id = activeTicketId;
      const oid = orderContextId || selectedOrderContext;
      if (oid) payload.order_id = oid;

      const res = await fetchWithFallback("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !res.body) {
        let msg = "Request failed";
        try {
          const errJson = await res.json();
          msg = errJson?.detail || msg;
        } catch {
          // ignore
        }
        throw new Error(msg);
      }

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let resolvedTid = activeTicketId;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines.filter(Boolean)) {
          try {
            const ev = JSON.parse(line);
            if (ev.type === "ticket") {
              resolvedTid = ev.ticket_id;
              setActiveTicketId(ev.ticket_id);
            } else if (ev.type === "event") {
              const label = STEP_LABEL[ev.event.node] ?? ev.event.node;
              setSteps((s) => (s[s.length - 1] === label ? s : [...s, label]));
            } else if (ev.type === "final") {
              resolvedTid = ev.ticket_id;
              setActiveTicketId(ev.ticket_id);
              setMsgs((m) => [
                ...m,
                {
                  role: "agent",
                  text: ev.response,
                  ticket: ev.ticket_id,
                  order: ev.order_id,
                  outcome: ev.status || ev.outcome,
                  metadata: ev.metadata ?? (ev.confirmation ? { card_type: "confirmation", confirmation: ev.confirmation } : null),
                  created_at: new Date().toISOString(),
                },
              ]);
            } else if (ev.type === "error") {
              setErrorBanner(ev.message || "Could not process your request automatically.");
              setMsgs((m) => [
                ...m,
                {
                  role: "agent",
                  text: ev.message,
                  outcome: "escalated",
                  created_at: new Date().toISOString(),
                },
              ]);
            }
          } catch {
            // ignore partial JSON
          }
        }
      }

      await refreshTickets(false);
      await refreshOrders();
      if (resolvedTid) {
        await loadTicketThread(resolvedTid);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not reach the support system.";
      setErrorBanner(msg);
      setMsgs((m) => [
        ...m,
        {
          role: "agent",
          text: `Request could not be completed: ${msg}`,
          outcome: "failed",
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setBusy(false);
      setSteps([]);
    }
  }

  /** Direct backend confirmation execution — NEVER sends "yes" text to an LLM! */
  async function handleConfirmAction(confirmation: PendingConfirmation) {
    if (confirmBusyId || busy) return;
    setConfirmBusyId(confirmation.id);
    setErrorBanner("");
    try {
      await confirmPendingAction(confirmation.id);
      await refreshOrders();
      await refreshTickets(false);
      await loadTicketThread(confirmation.ticket_id);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "Action confirmation failed.";
      setErrorBanner(detail);
      await refreshOrders();
      await refreshTickets(false);
      await loadTicketThread(confirmation.ticket_id);
    } finally {
      setConfirmBusyId(null);
    }
  }

  /** Direct backend cancellation of pending confirmation. */
  async function handleCancelAction(confirmation: PendingConfirmation) {
    if (confirmBusyId || busy) return;
    setConfirmBusyId(confirmation.id);
    setErrorBanner("");
    try {
      await cancelPendingAction(confirmation.id);
      await refreshTickets(false);
      await loadTicketThread(confirmation.ticket_id);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "Could not cancel pending action.";
      setErrorBanner(detail);
      await loadTicketThread(confirmation.ticket_id);
    } finally {
      setConfirmBusyId(null);
    }
  }

  // Look up the freshest status of a confirmation by ID from activeTicket
  function getLatestConfirmationState(conf: PendingConfirmation): PendingConfirmation {
    const match = activeTicket?.confirmations?.find((c) => c.id === conf.id);
    return match ?? conf;
  }

  const activePendingConf = activeTicket?.active_confirmation ?? null;

  function renderConfirmationCard(confRaw: PendingConfirmation) {
    const conf = getLatestConfirmationState(confRaw);
    const isPending = conf.status === "pending";
    const isThisBusy = confirmBusyId === conf.id;
    const actionTitle =
      conf.action === "refund"
        ? "Refund Request Confirmation"
        : conf.action === "cancel"
        ? "Order Cancellation Confirmation"
        : "Shipping Address Update Confirmation";
    const confirmBtnLabel =
      conf.action === "refund"
        ? conf.policy_decision === "needs_approval"
          ? "Confirm & Submit for Review"
          : "Confirm Refund"
        : conf.action === "cancel"
        ? "Confirm Cancellation"
        : "Confirm Address Change";

    return (
      <div className="mt-2.5 rounded-xl border border-violet-200 bg-violet-50/40 p-3.5 text-xs space-y-2.5 shadow-2xs">
        <div className="flex items-center justify-between gap-2 pb-2 border-b border-violet-100">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-violet-600" />
            <span className="font-bold text-violet-950">{actionTitle}</span>
          </div>
          <StatusBadge status={conf.status} size="xs" />
        </div>

        <div className="grid grid-cols-2 gap-2 text-[11px] bg-white rounded-lg p-2.5 border border-violet-100">
          <div>
            <span className="text-slate-400 block">Order ID</span>
            <span className="font-mono font-bold text-slate-900">{conf.order_id}</span>
          </div>
          <div>
            <span className="text-slate-400 block">Product</span>
            <span className="font-semibold text-slate-800">{conf.item}</span>
          </div>
          {conf.amount !== null && conf.amount !== undefined && (
            <div>
              <span className="text-slate-400 block">{conf.action === "refund" ? "Refund Amount" : "Order Amount"}</span>
              <span className="font-mono font-bold text-emerald-700">{formatCurrency(conf.amount)}</span>
            </div>
          )}
          <div>
            <span className="text-slate-400 block">Eligibility</span>
            <span className="font-medium text-slate-700">{conf.eligibility_label}</span>
          </div>
        </div>

        {conf.action === "change_address" && (
          <div className="grid grid-cols-1 gap-1.5 text-[11px] bg-white rounded-lg p-2.5 border border-violet-100">
            <div>
              <span className="text-slate-400 uppercase text-[10px] font-semibold">Current Address: </span>
              <span className="text-slate-600 line-through">{conf.current_address || "N/A"}</span>
            </div>
            <div>
              <span className="text-emerald-700 uppercase text-[10px] font-semibold">Proposed New Address: </span>
              <span className="font-semibold text-slate-900">{conf.new_address}</span>
            </div>
          </div>
        )}

        {conf.reason && (
          <div className="text-[11px] text-slate-600">
            <span className="font-semibold text-slate-700">Reason: </span>
            {conf.reason}
          </div>
        )}

        <div className="text-[11px] text-violet-900/90 bg-white/70 rounded px-2.5 py-1.5 border border-violet-100">
          {conf.processing_info}
        </div>

        {isPending ? (
          <div className="space-y-2 pt-1">
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={Boolean(confirmBusyId) || busy}
                onClick={() => handleConfirmAction(conf)}
                className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white shadow-xs hover:bg-emerald-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
              >
                {isThisBusy ? (
                  <>
                    <span className="h-3 w-3 rounded-full border-2 border-white border-t-transparent animate-spin" />
                    <span>Executing & Verifying...</span>
                  </>
                ) : (
                  <>
                    <span>✓</span>
                    <span>{confirmBtnLabel}</span>
                  </>
                )}
              </button>
              <button
                type="button"
                disabled={Boolean(confirmBusyId) || busy}
                onClick={() => handleCancelAction(conf)}
                className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
            <div className="text-[10px] text-slate-400 font-mono flex items-center justify-between">
              <span>Ref: {conf.id}</span>
              <span>Expires: {new Date(conf.expires_at.endsWith("Z") ? conf.expires_at : `${conf.expires_at}Z`).toLocaleTimeString()}</span>
            </div>
          </div>
        ) : (
          <div className="text-[11px] font-medium text-slate-500 italic">
            This confirmation is {conf.status} and cannot be resubmitted.
          </div>
        )}
      </div>
    );
  }

  function renderStructuredCard(meta?: MessageMetadata | null) {
    if (!meta) return null;

    if ((meta.card_type === "confirmation" || meta.card_type === "confirmation_reminder") && meta.confirmation) {
      return renderConfirmationCard(meta.confirmation);
    }

    if (meta.card_type === "receipt") {
      return (
        <div className="mt-2.5 rounded-xl border border-emerald-200 bg-emerald-50/50 p-3 text-xs space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="font-bold text-emerald-900">✓ Verified Transaction Receipt</span>
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800">
              DB Verified
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-[11px] text-emerald-950">
            {meta.order_id && <div>Order: <span className="font-mono font-bold">{meta.order_id}</span></div>}
            {meta.item && <div>Product: <span className="font-semibold">{meta.item}</span></div>}
            {meta.amount !== undefined && <div>Amount: <span className="font-mono font-bold">{formatCurrency(meta.amount)}</span></div>}
            {meta.reference_id && <div>Reference: <span className="font-mono font-bold">{meta.reference_id}</span></div>}
            {meta.order_status && <div>Order Status: <span className="capitalize font-semibold">{meta.order_status}</span></div>}
          </div>
        </div>
      );
    }

    if (meta.card_type === "approval_pending") {
      return (
        <div className="mt-2.5 rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-xs space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-bold text-amber-950">Pending Human Manager Review</span>
            <StatusBadge status="awaiting_approval" size="xs" />
          </div>
          <p className="text-[11px] text-amber-900">
            Submitted for manager approval. No refund or mutation has been processed yet.
          </p>
        </div>
      );
    }

    if (meta.card_type === "execution_failed" || meta.card_type === "verification_failed") {
      return (
        <div className="mt-2.5 rounded-xl border border-rose-200 bg-rose-50/70 p-3 text-xs space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-bold text-rose-950">
              {meta.card_type === "verification_failed" ? "Verification Uncertain" : "Action Unsuccessful"}
            </span>
            <StatusBadge status="failed" size="xs" />
          </div>
          {meta.detail && <p className="text-[11px] text-rose-900">{meta.detail}</p>}
        </div>
      );
    }

    if (meta.card_type === "order_selection" && meta.available_orders && meta.available_orders.length > 0) {
      const actWord = meta.requested_action_label || "check";
      return (
        <div className="mt-2.5 space-y-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Select an order to continue:
          </div>
          <div className="grid grid-cols-1 gap-1.5">
            {meta.available_orders.map((o) => (
              <button
                key={o.id}
                type="button"
                disabled={busy || Boolean(confirmBusyId)}
                onClick={() => send(`Please ${actWord} order ${o.id}`, o.id)}
                className="text-left rounded-lg border border-slate-200 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/50 p-2 text-[11px] transition-colors disabled:opacity-50"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono font-bold text-slate-900">
                    {o.id} · {o.item}
                  </span>
                  <span className="font-mono font-semibold text-slate-700">{formatCurrency(o.amount)}</span>
                </div>
                {o.eligibility_note && (
                  <div className="text-[10px] text-slate-500 mt-0.5">{o.eligibility_note}</div>
                )}
              </button>
            ))}
          </div>
        </div>
      );
    }

    if (meta.order) {
      return (
        <div className="mt-2 rounded-lg border border-slate-200/80 bg-slate-50 px-2.5 py-1.5 text-[11px] flex items-center justify-between gap-2">
          <div>
            <span className="font-mono font-bold text-slate-800">{meta.order.id}</span>
            <span className="text-slate-500"> · {meta.order.item} ({formatCurrency(meta.order.amount)})</span>
          </div>
          <StatusBadge status={meta.order.refunded ? "resolved" : meta.order.status} size="xs" />
        </div>
      );
    }

    return null;
  }

  function triggerOrderAction(order: CustomerOrder, action: "refund" | "cancel" | "change_address" | "track") {
    setSelectedOrderContext(order.id);
    if (action === "refund") {
      send(`I would like a refund for order ${order.id}.`, order.id);
    } else if (action === "cancel") {
      send(`Can you cancel my order ${order.id}?`, order.id);
    } else if (action === "change_address") {
      send(`I want to change the shipping address for order ${order.id}.`, order.id);
    } else {
      send(`Where is my order ${order.id}?`, order.id);
    }
  }

  return (
    <div className="space-y-4">
      {/* Visually Distinct Demo Mode Banner (only shown if logged in via Demo Mode) */}
      {isDemo && (
        <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50 px-4 py-2.5 text-xs text-amber-950 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="rounded bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">
              Dev / Demo Mode
            </span>
            <span>
              Testing as <strong>{user?.name}</strong> ({user?.id} · {user?.role}). Server-side ownership and confirmation guardrails remain strictly enforced.
            </span>
          </div>
          <span className="text-[11px] text-amber-800">Switch accounts from the left sidebar footer</span>
        </div>
      )}

      {/* Top Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-4 rounded-xl border border-slate-200/80 shadow-xs">
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <span>Support Inbox</span>
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
              Protected Actions
            </span>
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Signed in as <strong className="text-slate-700">{user?.name}</strong> ({user?.email}). All refunds, cancellations, and address changes require explicit confirmation.
          </p>
        </div>

        <button
          onClick={startNewTicket}
          className="rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 px-3.5 py-2 text-xs font-semibold transition-colors flex items-center gap-1.5 shadow-xs self-start sm:self-auto"
        >
          <span>+ New Support Request</span>
        </button>
      </div>

      {errorBanner && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-900 flex items-center justify-between">
          <span>{errorBanner}</span>
          <button onClick={() => setErrorBanner("")} className="font-bold text-rose-600 hover:text-rose-800 ml-3">
            ✕
          </button>
        </div>
      )}

      {/* 3-Column Support Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 h-[calc(100vh-14.5rem)] min-h-[600px]">
        {/* Left Column: Conversations / Tickets (3 cols) */}
        <div className="lg:col-span-3 bg-white rounded-xl border border-slate-200/80 shadow-xs flex flex-col overflow-hidden">
          <div className="p-3.5 border-b border-slate-100 bg-slate-50/50 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              My Tickets ({tickets.length})
            </span>
            <button onClick={startNewTicket} className="text-xs text-indigo-600 hover:text-indigo-800 font-semibold">
              + New
            </button>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-slate-100">
            {tickets.length === 0 ? (
              <div className="p-6 text-center text-xs text-slate-400">
                No conversations yet. Start a new support request.
              </div>
            ) : (
              tickets.map((t) => {
                const isActive = t.id === activeTicketId;
                return (
                  <button
                    key={t.id}
                    onClick={() => loadTicketThread(t.id)}
                    className={`w-full text-left p-3 transition-colors flex flex-col gap-1.5 ${
                      isActive ? "bg-indigo-50/70 border-l-3 border-indigo-600" : "hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-mono text-xs font-bold text-slate-900">{t.id}</span>
                      <StatusBadge status={t.status} size="xs" />
                    </div>
                    <p className="text-xs text-slate-600 line-clamp-2 leading-relaxed">{t.message}</p>
                    <div className="flex items-center justify-between text-[10px] text-slate-400">
                      <span className="capitalize">{t.order_id ? t.order_id : t.issue_type.replace(/_/g, " ")}</span>
                      <span>{formatTimeAgo(t.created_at)}</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Center Column: Conversation Thread (6 cols) */}
        <div className="lg:col-span-6 bg-white rounded-xl border border-slate-200/80 shadow-xs flex flex-col overflow-hidden">
          {/* Thread Header */}
          <div className="px-5 py-3 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <div className="h-7 w-7 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-xs shadow-xs">
                AI
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-xs text-slate-900">
                    {activeTicketId ? `Ticket ${activeTicketId}` : "New Support Conversation"}
                  </span>
                  {activeTicket && <StatusBadge status={activeTicket.status} size="xs" />}
                </div>
                <div className="text-[11px] text-slate-500">
                  {user?.name} ({user?.id})
                </div>
              </div>
            </div>

            {(activeTicket?.order_id || selectedOrderContext) && (
              <span className="inline-flex items-center gap-1.5 font-mono text-[11px] font-semibold text-indigo-800 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded-md">
                <span>Order: {activeTicket?.order_id || selectedOrderContext}</span>
                {!activeTicketId && selectedOrderContext && (
                  <button
                    onClick={() => setSelectedOrderContext(null)}
                    className="text-indigo-400 hover:text-indigo-700"
                    title="Clear order context"
                  >
                    ✕
                  </button>
                )}
              </span>
            )}
          </div>

          {/* Pending Action Alert Strip */}
          {activePendingConf && (
            <div className="px-4 py-2 bg-violet-50 border-b border-violet-200 flex items-center justify-between text-xs text-violet-950">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-violet-600 animate-pulse" />
                <span className="font-semibold">
                  Pending confirmation: {titleCase(activePendingConf.action)} on {activePendingConf.order_id}
                </span>
              </div>
              <span className="text-[11px] text-violet-700">Action paused until you confirm</span>
            </div>
          )}

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50/30">
            {msgs.length === 0 && (
              <div className="py-6 px-3">
                <div className="text-center mb-5">
                  <div className="mx-auto h-10 w-10 rounded-full bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 mb-2 font-bold">
                    R
                  </div>
                  <h3 className="text-sm font-bold text-slate-800">How can we help today?</h3>
                  <p className="text-xs text-slate-500 max-w-sm mx-auto mt-1">
                    Ask about your orders, check eligibility, or click an order on the right to start a protected request.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                    Common Requests
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {QUICK_PROMPTS.map((q) => (
                      <button
                        key={q.text}
                        type="button"
                        disabled={busy}
                        onClick={() => send(q.text)}
                        className="text-left rounded-lg border border-slate-200 bg-white p-2.5 text-xs hover:border-indigo-400 hover:bg-indigo-50/40 transition-colors shadow-2xs group"
                      >
                        <div className="font-semibold text-slate-800 group-hover:text-indigo-700">{q.label}</div>
                        <div className="text-slate-500 line-clamp-1 mt-0.5 text-[11px]">{q.text}</div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {msgs.map((m, i) => {
              const isUser = m.role === "customer";
              const isSystem = m.role === "system";
              if (isSystem) {
                return (
                  <div key={m.id ?? i} className="flex justify-center">
                    <div className="rounded-md bg-slate-100 px-3 py-1 text-[11px] font-mono text-slate-600">
                      {m.text}
                    </div>
                  </div>
                );
              }
              return (
                <div key={m.id ?? i} className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}>
                  <div
                    className={`max-w-[88%] rounded-2xl px-4 py-2.5 text-xs leading-relaxed shadow-xs ${
                      isUser
                        ? "bg-indigo-600 text-white rounded-br-xs"
                        : "bg-white border border-slate-200/90 text-slate-800 rounded-bl-xs"
                    }`}
                  >
                    <div className="whitespace-pre-wrap">{m.text}</div>
                    {!isUser && renderStructuredCard(m.metadata)}
                  </div>
                  <span className="text-[10px] text-slate-400 mt-1 px-1">
                    {isUser ? "You" : "ResolveFlow Support"} {m.created_at ? `· ${formatTimeAgo(m.created_at)}` : ""}
                  </span>
                </div>
              );
            })}

            {/* Live Progress Indicator */}
            {(busy || confirmBusyId) && (
              <div className="rounded-xl border border-indigo-200/70 bg-indigo-50/50 p-3.5 text-xs text-indigo-950 shadow-xs">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="h-3 w-3 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin" />
                  <span className="font-semibold text-indigo-900">
                    {confirmBusyId ? "Revalidating policy, executing transaction & verifying DB..." : "Processing your request..."}
                  </span>
                </div>
                {steps.length > 0 && (
                  <ul className="space-y-1 pl-2 font-mono text-[11px]">
                    {steps.map((s, idx) => (
                      <li key={idx} className="flex items-center gap-1.5 text-indigo-900">
                        <span className="text-emerald-600 font-bold">✓</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input Bar */}
          <div className="p-3 border-t border-slate-100 bg-white">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex items-center gap-2"
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={busy || Boolean(confirmBusyId)}
                maxLength={2000}
                placeholder={
                  selectedOrderContext
                    ? `Ask about ${selectedOrderContext} or describe how we can help...`
                    : "Describe your request (e.g. 'Refund my order' or 'Where is my package?')..."
                }
                className="flex-1 rounded-lg border border-slate-300 bg-slate-50/50 px-3.5 py-2.5 text-xs text-slate-800 placeholder-slate-400 shadow-2xs focus:border-indigo-500 focus:bg-white focus:outline-hidden transition-all disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={busy || Boolean(confirmBusyId) || !input.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700 transition-colors disabled:opacity-50 shrink-0"
              >
                {busy ? "Sending..." : "Send"}
              </button>
            </form>
          </div>
        </div>

        {/* Right Column: Authenticated Customer Orders Sidebar (3 cols) */}
        <div className="lg:col-span-3 bg-white rounded-xl border border-slate-200/80 shadow-xs flex flex-col overflow-hidden">
          <div className="p-3.5 border-b border-slate-100 bg-slate-50/50 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              My Orders ({orders.length})
            </span>
            <span className="text-[10px] text-slate-400">Click to inspect</span>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
            {orders.length === 0 ? (
              <div className="p-4 text-center text-xs text-slate-400">
                No orders associated with this account.
              </div>
            ) : (
              orders.map((o) => {
                const expanded = expandedOrderId === o.id;
                const isSelected = selectedOrderContext === o.id;
                return (
                  <div
                    key={o.id}
                    className={`rounded-xl border text-xs transition-all shadow-2xs overflow-hidden ${
                      isSelected ? "border-indigo-400 bg-indigo-50/20" : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setExpandedOrderId(expanded ? null : o.id);
                        setSelectedOrderContext(o.id);
                      }}
                      className="w-full text-left p-3 space-y-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-bold text-slate-900">{o.id}</span>
                        <span className="font-mono font-semibold text-slate-900">{formatCurrency(o.amount)}</span>
                      </div>
                      <div className="text-slate-700 font-medium truncate">{o.item}</div>
                      <div className="flex items-center justify-between gap-1">
                        <StatusBadge status={o.status} size="xs" />
                        {o.refunded && (
                          <span className="rounded bg-emerald-100 text-emerald-800 px-1.5 py-0.5 text-[10px] font-semibold">
                            Refunded
                          </span>
                        )}
                      </div>
                    </button>

                    {/* Expanded Order Details + Shipment + Refund History + Available/Unavailable Actions */}
                    {expanded && (
                      <div className="px-3 pb-3 pt-2 border-t border-slate-100 space-y-2.5 text-[11px] bg-slate-50/40">
                        <div className="grid grid-cols-2 gap-1.5 text-slate-600">
                          <div>Placed: <span className="font-mono text-slate-800">{o.placed_at}</span></div>
                          <div>Delivered: <span className="font-mono text-slate-800">{o.delivered_at ?? "—"}</span></div>
                        </div>

                        <div className="rounded bg-white border border-slate-100 p-2">
                          <span className="text-[10px] uppercase font-semibold text-slate-400 block">Shipping Address</span>
                          <span className="text-slate-700">{o.shipping_address}</span>
                        </div>

                        {o.shipment && (
                          <div className="rounded bg-sky-50/60 border border-sky-100 p-2 space-y-0.5">
                            <div className="flex justify-between font-semibold text-sky-900">
                              <span>{o.shipment.carrier}</span>
                              <span className="font-mono">{o.shipment.tracking_number}</span>
                            </div>
                            <div className="text-sky-800">{o.shipment.last_update}</div>
                            {o.shipment.eta && <div className="text-[10px] text-sky-600">ETA: {o.shipment.eta}</div>}
                          </div>
                        )}

                        {o.refund && (
                          <div className="rounded bg-emerald-50/70 border border-emerald-200/70 p-2 space-y-0.5">
                            <div className="font-semibold text-emerald-900">
                              Refund History ({formatCurrency(o.refund.amount)})
                            </div>
                            <div className="text-emerald-800">Date: {o.refund.created_at} · {o.refund.reason}</div>
                            {o.refund.reference_id && (
                              <div className="font-mono text-[10px] text-emerald-700">Ref: {o.refund.reference_id}</div>
                            )}
                          </div>
                        )}

                        {/* Actions computed deterministically on backend */}
                        <div className="space-y-1.5 pt-1">
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                            Actions
                          </div>
                          <button
                            type="button"
                            disabled={busy || Boolean(confirmBusyId)}
                            onClick={() => triggerOrderAction(o, "track")}
                            className="w-full text-left rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 hover:border-indigo-400 hover:text-indigo-700 transition-colors disabled:opacity-50"
                          >
                            Track / Check Status
                          </button>

                          {(o.actions || []).map((act) =>
                            act.available ? (
                              <button
                                key={act.action}
                                type="button"
                                disabled={busy || Boolean(confirmBusyId)}
                                onClick={() => triggerOrderAction(o, act.action)}
                                className="w-full text-left rounded-md border border-indigo-200 bg-indigo-50/60 px-2.5 py-1.5 text-[11px] font-semibold text-indigo-800 hover:bg-indigo-100 transition-colors disabled:opacity-50 flex items-center justify-between"
                              >
                                <span>{act.label}</span>
                                {act.requires_approval && (
                                  <span className="text-[9px] rounded bg-amber-100 text-amber-800 px-1 py-0.5">
                                    Mgr Review
                                  </span>
                                )}
                              </button>
                            ) : (
                              <div
                                key={act.action}
                                title={act.reason}
                                className="w-full rounded-md border border-slate-200/70 bg-slate-100/70 px-2.5 py-1.5 text-[11px] text-slate-400 cursor-not-allowed"
                              >
                                <div className="font-medium line-through">{act.label}</div>
                                <div className="text-[10px] text-slate-400 leading-tight mt-0.5">{act.reason}</div>
                              </div>
                            )
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
