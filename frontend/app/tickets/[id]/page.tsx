"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import Timeline from "@/components/Timeline";
import {
  ApiError,
  CustomerOrder,
  Ticket,
  cancelPendingAction,
  confirmPendingAction,
  formatCurrency,
  formatTimeAgo,
  get,
  getCustomerOrders,
  post,
  resolveTicket,
  titleCase,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function TicketDetail({ params }: { params: { id: string } }) {
  const { user } = useAuth();
  const [t, setT] = useState<Ticket | null>(null);
  const [customerOrders, setCustomerOrders] = useState<CustomerOrder[]>([]);
  const [err, setErr] = useState("");
  const [replyInput, setReplyInput] = useState("");
  const [sendingReply, setSendingReply] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionNote, setActionNote] = useState("");
  const [feedbackMsg, setFeedbackMsg] = useState("");

  const isManager = user?.role === "manager" || user?.role === "admin";

  function load() {
    setErr("");
    get<Ticket>(`/api/tickets/${params.id}`)
      .then((data) => {
        setT(data);
        if (data.customer_id) {
          getCustomerOrders(data.customer_id)
            .then(setCustomerOrders)
            .catch(() => setCustomerOrders([]));
        }
      })
      .catch((e) => setErr(e instanceof ApiError ? e.detail : "Ticket not found or not authorized."));
  }

  useEffect(() => {
    if (user) load();
  }, [params.id, user?.id]);

  async function handleSendReply(e: React.FormEvent) {
    e.preventDefault();
    if (!replyInput.trim() || sendingReply || !t) return;
    setSendingReply(true);
    setFeedbackMsg("");
    try {
      await post("/api/chat", { message: replyInput, ticket_id: t.id });
      setReplyInput("");
      load();
    } catch (ex) {
      setFeedbackMsg(ex instanceof ApiError ? ex.detail : "Failed to send message.");
    } finally {
      setSendingReply(false);
    }
  }

  async function handleApproval(approve: boolean) {
    if (!t) return;
    setActionBusy(true);
    setFeedbackMsg("");
    try {
      await post(`/api/approvals/${t.id}`, { approve, note: actionNote || undefined });
      setFeedbackMsg(`Ticket ${t.id} ${approve ? "approved and executed" : "rejected"}.`);
      setActionNote("");
      load();
    } catch (ex) {
      setFeedbackMsg(ex instanceof ApiError ? ex.detail : "Approval action failed.");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleResolve() {
    if (!t) return;
    setActionBusy(true);
    setFeedbackMsg("");
    try {
      await resolveTicket(t.id, actionNote || undefined);
      setFeedbackMsg(`Ticket ${t.id} marked as resolved.`);
      setActionNote("");
      load();
    } catch (ex) {
      setFeedbackMsg(ex instanceof ApiError ? ex.detail : "Failed to resolve ticket.");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleConfirmation(confId: string, confirm: boolean) {
    setActionBusy(true);
    setFeedbackMsg("");
    try {
      if (confirm) await confirmPendingAction(confId);
      else await cancelPendingAction(confId);
      setFeedbackMsg(confirm ? "Action confirmed and processed." : "Pending action cancelled.");
      load();
    } catch (ex) {
      setFeedbackMsg(ex instanceof ApiError ? ex.detail : "Confirmation request failed.");
      load();
    } finally {
      setActionBusy(false);
    }
  }

  if (err) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-rose-800">
        <h2 className="font-semibold text-base mb-1">Unable to Open Ticket</h2>
        <p className="text-sm mb-4">{err}</p>
        <Link href="/tickets" className="text-xs font-semibold text-rose-700 underline">
          ← Return to tickets
        </Link>
      </div>
    );
  }

  if (!t) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-6 w-32 bg-slate-200 rounded"></div>
        <div className="h-48 bg-slate-200 rounded-xl"></div>
      </div>
    );
  }

  const linkedOrder = customerOrders.find((o) => o.id === t.order_id);
  const messages = t.messages || [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-200/80 pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs text-slate-500 mb-1">
            <Link href="/tickets" className="text-indigo-600 hover:underline">Tickets</Link>
            <span>/</span>
            <span className="font-mono">{t.id}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-2xl font-bold font-mono text-slate-900 tracking-tight">{t.id}</h1>
            <StatusBadge status={t.status} size="md" />
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
              {titleCase(t.issue_type)}
            </span>
            {t.resolution_mode && (
              <span className="rounded bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-xs font-medium text-emerald-700">
                {titleCase(t.resolution_mode)}
              </span>
            )}
          </div>
        </div>
        <div className="text-xs text-slate-500 text-right">
          <div>Created: {new Date(t.created_at).toLocaleString()}</div>
          <div className="text-[11px] text-slate-400 mt-0.5">{formatTimeAgo(t.created_at)}</div>
        </div>
      </div>

      {feedbackMsg && (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-xs text-indigo-900 flex items-center justify-between">
          <span>{feedbackMsg}</span>
          <button onClick={() => setFeedbackMsg("")} className="text-indigo-500 hover:text-indigo-700">✕</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-7 space-y-5">
          <Card title="Conversation History" subtitle="Multi-turn interaction stream">
            <div className="space-y-4">
              {messages.map((m, idx) => {
                const isCustomer = m.role === "customer";
                const isSystem = m.role === "system";
                return (
                  <div key={m.id || idx} className={`flex flex-col ${isCustomer ? "items-end" : isSystem ? "items-center" : "items-start"}`}>
                    {isSystem ? (
                      <div className="my-1 rounded-md bg-slate-100 px-3 py-1 text-[11px] text-slate-600 font-mono">{m.text}</div>
                    ) : (
                      <>
                        <div
                          className={`max-w-[90%] rounded-2xl px-4 py-3 text-xs leading-relaxed shadow-xs ${
                            isCustomer ? "bg-indigo-600 text-white rounded-br-xs" : "bg-slate-50 border border-slate-200 text-slate-800 rounded-bl-xs"
                          }`}
                        >
                          <div className="whitespace-pre-wrap">{m.text}</div>
                        </div>
                        <span className="text-[10px] text-slate-400 mt-1 px-1">
                          {isCustomer ? `Customer (${t.customer_id})` : "ResolveFlow Support"} · {formatTimeAgo(m.created_at)}
                        </span>
                      </>
                    )}
                  </div>
                );
              })}
            </div>

            {t.active_confirmation && (
              <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50/60 p-3.5 text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-violet-950">
                    Pending Confirmation: {titleCase(t.active_confirmation.action)} ({t.active_confirmation.order_id})
                  </span>
                  <StatusBadge status="pending" size="xs" />
                </div>
                <div className="text-violet-900">{t.active_confirmation.eligibility_label}</div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleConfirmation(t.active_confirmation!.id, true)}
                    disabled={actionBusy}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    ✓ Confirm Action
                  </button>
                  <button
                    onClick={() => handleConfirmation(t.active_confirmation!.id, false)}
                    disabled={actionBusy}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <form onSubmit={handleSendReply} className="mt-5 pt-4 border-t border-slate-100 flex gap-2">
              <input
                type="text"
                value={replyInput}
                onChange={(e) => setReplyInput(e.target.value)}
                disabled={sendingReply}
                placeholder="Send a follow-up message on this ticket..."
                className="flex-1 rounded-lg border border-slate-300 bg-slate-50/50 px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:bg-white focus:border-indigo-500 focus:outline-hidden"
              />
              <button
                type="submit"
                disabled={sendingReply || !replyInput.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700 transition-colors disabled:opacity-50"
              >
                {sendingReply ? "Sending..." : "Reply"}
              </button>
            </form>
          </Card>

          {t.handoff && (
            <Card title="Human Escalation Package" subtitle="Context prepared for human specialist review" className="border-rose-200 bg-rose-50/30">
              <div className="space-y-2 text-xs">
                <div className="font-semibold text-rose-900">{t.handoff.reason_for_escalation}</div>
                {t.handoff.previous_actions && t.handoff.previous_actions.length > 0 && (
                  <ul className="space-y-1 font-mono text-[11px] text-slate-600 bg-white border border-slate-200 rounded-lg p-2.5">
                    {t.handoff.previous_actions.map((a, i) => (
                      <li key={i}>• {a}</li>
                    ))}
                  </ul>
                )}
              </div>
            </Card>
          )}

          <Card title="Agent Workflow Execution Trace" subtitle="Recorded step-by-step telemetry">
            <Timeline trace={t.trace ?? []} />
          </Card>
        </div>

        <div className="lg:col-span-5 space-y-5">
          {isManager && ["awaiting_approval", "escalated", "awaiting_customer", "failed", "open"].includes(t.status) && (
            <Card title="Manager Actions" subtitle="Authorized support manager controls" className="border-indigo-200 bg-indigo-50/20">
              <div className="space-y-3">
                {t.status === "awaiting_approval" && t.pending_action && (
                  <div className="rounded-lg bg-white border border-amber-200 p-3 space-y-1.5 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-amber-900">
                        Pending: {titleCase(t.pending_action.plan.action)}
                      </span>
                      {t.pending_action.plan.amount && (
                        <span className="font-bold font-mono text-emerald-700">
                          {formatCurrency(t.pending_action.plan.amount)}
                        </span>
                      )}
                    </div>
                    <p className="text-slate-600">{t.pending_action.plan.reason}</p>
                  </div>
                )}

                <input
                  type="text"
                  value={actionNote}
                  onChange={(e) => setActionNote(e.target.value)}
                  placeholder="Optional manager audit note..."
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-800 focus:border-indigo-500 focus:outline-hidden"
                />

                {t.status === "awaiting_approval" ? (
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleApproval(true)}
                      disabled={actionBusy}
                      className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      ✓ Approve & Execute
                    </button>
                    <button
                      onClick={() => handleApproval(false)}
                      disabled={actionBusy}
                      className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      ✕ Reject
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleResolve}
                    disabled={actionBusy}
                    className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    ✓ Mark Ticket as Resolved
                  </button>
                )}
              </div>
            </Card>
          )}

          {t.order_id && (
            <Card title={`Linked Order: ${t.order_id}`} subtitle="Live order record">
              {linkedOrder ? (
                <div className="space-y-3 text-xs">
                  <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                    <span className="font-semibold text-slate-900 text-sm">{linkedOrder.item}</span>
                    <span className="font-mono font-bold text-slate-900 text-sm">{formatCurrency(linkedOrder.amount)}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px]">
                    <div><span className="text-slate-400 block">Status:</span><StatusBadge status={linkedOrder.status} size="xs" /></div>
                    <div>
                      <span className="text-slate-400 block">Refund:</span>
                      <span className={linkedOrder.refunded ? "text-emerald-700 font-semibold" : "text-slate-600"}>
                        {linkedOrder.refunded ? `Refunded (${linkedOrder.refund?.reference_id || "Recorded"})` : "Not refunded"}
                      </span>
                    </div>
                  </div>
                  <div className="text-[11px] bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                    <span className="text-slate-400 block text-[10px] uppercase font-semibold">Shipping Address:</span>
                    <span className="text-slate-700">{linkedOrder.shipping_address}</span>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-slate-500 font-mono">Order ID {t.order_id}</div>
              )}
            </Card>
          )}

          {/* Audit Trail */}
          {t.audit_logs && t.audit_logs.length > 0 && (
            <Card title="Immutable Audit Trail" subtitle="Verified state-changing and authorization events">
              <div className="space-y-2 text-[11px]">
                {t.audit_logs.map((a) => (
                  <div key={a.id} className="rounded-lg border border-slate-100 bg-slate-50/70 p-2.5 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-semibold text-slate-800">{a.action}</span>
                      <StatusBadge status={a.status === "success" || a.status === "approved" ? "resolved" : a.status} size="xs" />
                    </div>
                    <div className="text-slate-600">{a.detail}</div>
                    <div className="text-[10px] text-slate-400">
                      Actor: {a.actor_id || "system"} ({a.actor_role || "system"}) · {formatTimeAgo(a.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
