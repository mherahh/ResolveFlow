"use client";
import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { ApiError, get, post, Ticket, formatCurrency, formatTimeAgo, titleCase } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Approvals() {
  const { user, demoModeEnabled, switchDemo } = useAuth();
  const [rows, setRows] = useState<Ticket[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ text: string; type: "success" | "error" } | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<"all" | "awaiting_approval" | "escalated" | "failed">("all");

  const isManager = user?.role === "manager" || user?.role === "admin";

  const load = () => {
    if (!isManager) return;
    get<Ticket[]>("/api/approvals")
      .then(setRows)
      .catch(() => setRows([]));
  };

  useEffect(() => {
    if (isManager) load();
  }, [user?.id, isManager]);

  if (!isManager) {
    return (
      <div className="max-w-lg mx-auto mt-10">
        <Card
          title="Manager Authorization Required"
          subtitle="High-value financial approvals and triage actions are restricted to authorized support managers."
        >
          <div className="space-y-4 text-xs text-slate-600">
            <p>
              You are currently signed in as a customer (<strong>{user?.name}</strong>). Backend authorization blocks non-manager accounts from viewing or approving high-value refund requests.
            </p>
            {demoModeEnabled && (
              <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50 p-3 space-y-2">
                <div className="font-semibold text-amber-950">Developer / Demo Mode</div>
                <p className="text-[11px] text-amber-800">
                  Switch to the seeded Support Manager account (M001 · Tariq Mehmood) to test manager approval and triage workflows.
                </p>
                <button
                  onClick={() => switchDemo("M001")}
                  className="rounded-lg bg-amber-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-amber-700 transition-colors"
                >
                  Switch to Demo Manager (M001)
                </button>
              </div>
            )}
          </div>
        </Card>
      </div>
    );
  }

  async function decide(id: string, approve: boolean) {
    setBusy(id);
    setFeedback(null);
    try {
      const note = notes[id];
      await post(`/api/approvals/${id}`, { approve, note: note || undefined });
      setFeedback({
        text: `Ticket ${id} ${approve ? "approved, executed, and verified" : "rejected"}.`,
        type: "success",
      });
      load();
    } catch (e) {
      setFeedback({
        text: e instanceof ApiError ? e.detail : "Action failed. Please try again.",
        type: "error",
      });
    } finally {
      setBusy(null);
    }
  }

  async function markResolved(id: string) {
    setBusy(id);
    setFeedback(null);
    try {
      const note = notes[id];
      await post(`/api/tickets/${id}/resolve`, { note: note || undefined });
      setFeedback({ text: `Ticket ${id} marked as resolved.`, type: "success" });
      load();
    } catch (e) {
      setFeedback({
        text: e instanceof ApiError ? e.detail : "Action failed.",
        type: "error",
      });
    } finally {
      setBusy(null);
    }
  }

  const filteredRows = useMemo(() => {
    if (!rows) return [];
    if (filter === "all") return rows;
    return rows.filter((r) => r.status === filter);
  }, [rows, filter]);

  const approvalCount = rows?.filter((r) => r.status === "awaiting_approval").length ?? 0;
  const escalationCount = rows?.filter((r) => r.status === "escalated").length ?? 0;
  const failedCount = rows?.filter((r) => r.status === "failed").length ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Triage & Approvals Hub</h1>
        <p className="text-sm text-slate-500 mt-1">
          Authorized Manager Queue — review customer-confirmed high-value refunds, failed operations, and escalations.
        </p>
      </div>

      {/* Filter Tabs */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 pb-2">
        {[
          { id: "all" as const, label: "All Items", count: rows?.length ?? 0, activeCls: "bg-slate-900 text-white" },
          { id: "awaiting_approval" as const, label: "Awaiting Approval", count: approvalCount, activeCls: "bg-amber-600 text-white" },
          { id: "escalated" as const, label: "Escalated", count: escalationCount, activeCls: "bg-rose-600 text-white" },
          { id: "failed" as const, label: "Failed Operations", count: failedCount, activeCls: "bg-red-700 text-white" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setFilter(tab.id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 ${
              filter === tab.id ? tab.activeCls : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            <span>{tab.label}</span>
            <span className="text-[10px] rounded-full px-1.5 bg-black/15">{tab.count}</span>
          </button>
        ))}
      </div>

      {feedback && (
        <div
          className={`rounded-lg p-3 text-xs flex items-center justify-between ${
            feedback.type === "success"
              ? "border border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border border-rose-200 bg-rose-50 text-rose-900"
          }`}
        >
          <span>{feedback.text}</span>
          <button onClick={() => setFeedback(null)} className="font-bold">
            ✕
          </button>
        </div>
      )}

      {!rows ? (
        <div className="p-8 text-center text-sm text-slate-400 animate-pulse">Loading triage items...</div>
      ) : filteredRows.length === 0 ? (
        <Card>
          <div className="py-8 text-center text-sm text-slate-500">
            <div className="mx-auto h-8 w-8 text-emerald-500 mb-2 font-bold text-lg">✓</div>
            <p className="font-semibold text-slate-800">Queue Clear</p>
            <p className="text-xs text-slate-400 mt-0.5">No tickets currently require manager approval or triage.</p>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredRows.map((t) => (
            <Card
              key={t.id}
              className={`border-l-4 ${
                t.status === "awaiting_approval"
                  ? "border-l-amber-500"
                  : t.status === "failed"
                  ? "border-l-red-600"
                  : "border-l-rose-500"
              }`}
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2">
                  <Link href={`/tickets/${t.id}`} className="font-mono text-sm font-bold text-indigo-600 hover:underline">
                    {t.id}
                  </Link>
                  <StatusBadge status={t.status} size="xs" />
                  <span className="text-xs text-slate-500">Customer: {t.customer_id}</span>
                  {t.order_id && (
                    <span className="font-mono text-xs bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded">{t.order_id}</span>
                  )}
                </div>
                <span className="text-xs text-slate-400">{formatTimeAgo(t.created_at)}</span>
              </div>

              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-800 mb-3 border border-slate-100">
                <span className="font-semibold text-slate-500 block mb-0.5">Customer Request:</span>
                &ldquo;{t.message}&rdquo;
              </div>

              {t.status === "awaiting_approval" && t.pending_action && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3.5 mb-3 text-xs space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-amber-950 text-sm">
                      Customer-Confirmed Action: {titleCase(t.pending_action.plan.action)}
                    </span>
                    {t.pending_action.plan.amount && (
                      <span className="font-mono font-bold text-emerald-800 text-base">
                        {formatCurrency(t.pending_action.plan.amount)}
                      </span>
                    )}
                  </div>
                  <p className="text-amber-900">{t.pending_action.plan.reason}</p>
                  {t.pending_action.policy?.reasons && (
                    <div className="text-[11px] text-amber-800 font-medium bg-white/70 p-2 rounded border border-amber-200/50">
                      <span className="font-semibold">Policy Trigger: </span>
                      {t.pending_action.policy.reasons.join(" ")}
                    </div>
                  )}
                </div>
              )}

              {(t.status === "escalated" || t.status === "failed") && (
                <div className="rounded-lg border border-rose-200 bg-rose-50/40 p-3.5 mb-3 text-xs space-y-1.5">
                  <div className="font-bold text-rose-950">
                    {t.status === "failed" ? "Failure Details:" : "Escalation Trigger:"}
                  </div>
                  <p className="text-rose-900 font-medium">
                    {t.handoff?.reason_for_escalation || t.final_response || "Requires human follow-up."}
                  </p>
                  <p className="text-[11px] text-rose-700">
                    Note: Closing an escalation records a manual resolution note only and does not execute an automated refund or order write.
                  </p>
                </div>
              )}

              <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 pt-2 border-t border-slate-100">
                <input
                  type="text"
                  value={notes[t.id] || ""}
                  onChange={(e) => setNotes({ ...notes, [t.id]: e.target.value })}
                  placeholder="Manager review note (recorded in immutable audit log)..."
                  className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:border-indigo-500 focus:outline-hidden"
                />

                {t.status === "awaiting_approval" ? (
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => decide(t.id, true)}
                      disabled={busy === t.id}
                      className="rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-emerald-700 transition-colors disabled:opacity-50"
                    >
                      {busy === t.id ? "Executing..." : "✓ Approve & Execute"}
                    </button>
                    <button
                      onClick={() => decide(t.id, false)}
                      disabled={busy === t.id}
                      className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
                    >
                      ✕ Reject
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => markResolved(t.id)}
                    disabled={busy === t.id}
                    className="rounded-lg bg-slate-800 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-slate-900 transition-colors disabled:opacity-50 shrink-0"
                  >
                    {busy === t.id ? "Processing..." : "✓ Close Ticket (No Auto-Write)"}
                  </button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
