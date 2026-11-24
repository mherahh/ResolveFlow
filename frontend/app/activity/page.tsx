"use client";
import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import Timeline from "@/components/Timeline";
import { ActivityRun, STAGE_LABEL, formatCurrency, formatTimeAgo, get, titleCase } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Activity() {
  const { user } = useAuth();
  const [runs, setRuns] = useState<ActivityRun[] | null>(null);
  const [filterStage, setFilterStage] = useState<string>("all");

  useEffect(() => {
    if (!user) return;
    get<ActivityRun[]>("/api/activity")
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [user?.id]);

  const availableStages = useMemo(() => {
    const s = new Set<string>();
    runs?.forEach((r) => r.trace.forEach((e) => s.add(e.stage || e.node)));
    return Array.from(s);
  }, [runs]);

  const filteredRuns = useMemo(() => {
    if (!runs) return [];
    if (filterStage === "all") return runs;
    return runs.filter((r) => r.trace.some((e) => (e.stage || e.node) === filterStage));
  }, [runs, filterStage]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Agent Activity & Execution Audit</h1>
        <p className="text-sm text-slate-500 mt-1">
          Inspect real recorded execution traces distinguishing proposed actions, customer authorization, database writes, and post-write verification.
        </p>
      </div>

      {/* Filter by Stage */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider mr-1">Filter Stage:</span>
        <button
          onClick={() => setFilterStage("all")}
          className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
            filterStage === "all" ? "bg-slate-900 text-white" : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
          }`}
        >
          All Stages
        </button>
        {availableStages.map((st) => (
          <button
            key={st}
            onClick={() => setFilterStage(st)}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
              filterStage === st ? "bg-indigo-600 text-white" : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
            }`}
          >
            {STAGE_LABEL[st]?.label ?? titleCase(st)}
          </button>
        ))}
      </div>

      {!runs ? (
        <div className="p-8 text-center text-sm text-slate-400 animate-pulse">Loading execution traces...</div>
      ) : filteredRuns.length === 0 ? (
        <Card>
          <div className="py-8 text-center text-sm text-slate-500">
            No agent execution traces recorded yet. Start a request in the{" "}
            <Link href="/chat" className="text-indigo-600 underline font-medium">
              Support Inbox
            </Link>
            .
          </div>
        </Card>
      ) : (
        <div className="space-y-5">
          {filteredRuns.map((r) => (
            <Card
              key={r.ticket_id}
              title={
                <div className="flex flex-wrap items-center gap-2.5">
                  <Link href={`/tickets/${r.ticket_id}`} className="font-mono text-sm font-bold text-indigo-600 hover:underline">
                    Ticket {r.ticket_id}
                  </Link>
                  <StatusBadge status={r.status} size="xs" />
                  {r.order_id && (
                    <span className="font-mono text-[11px] bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded">
                      {r.order_id}
                    </span>
                  )}
                  {r.resolution_mode && (
                    <span className="rounded bg-emerald-50 text-emerald-700 border border-emerald-200 px-1.5 py-0.5 text-[10px] font-semibold">
                      {titleCase(r.resolution_mode)}
                    </span>
                  )}
                </div>
              }
              subtitle={`Customer ${r.customer_id ?? "—"} · ${titleCase(r.issue_type)} · ${formatTimeAgo(r.created_at)}`}
              action={
                <Link href={`/tickets/${r.ticket_id}`} className="text-xs font-semibold text-slate-500 hover:text-slate-800">
                  View Ticket →
                </Link>
              }
            >
              <div className="space-y-5">
                <Timeline trace={r.trace} />

                {/* Confirmation Lifecycle Records */}
                {r.confirmations && r.confirmations.length > 0 && (
                  <div className="pt-4 border-t border-slate-100">
                    <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
                      Customer Confirmation Records
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                      {r.confirmations.map((c) => (
                        <div key={c.id} className="rounded-lg border border-slate-200 bg-slate-50/70 p-2.5 text-xs space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="font-mono font-bold text-slate-800">{c.id}</span>
                            <StatusBadge status={c.status} size="xs" />
                          </div>
                          <div className="text-slate-600">
                            {titleCase(c.action)} · <span className="font-mono">{c.order_id}</span>
                            {c.amount !== null && c.amount !== undefined ? ` (${formatCurrency(c.amount)})` : ""}
                          </div>
                          <div className="text-[10px] text-slate-400">
                            Policy: {c.policy_decision} · Created {formatTimeAgo(c.created_at)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Immutable Audit Trail */}
                {r.audit_logs && r.audit_logs.length > 0 && (
                  <div className="pt-4 border-t border-slate-100">
                    <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
                      Immutable Audit Log
                    </div>
                    <div className="space-y-1.5">
                      {r.audit_logs.map((a) => (
                        <div
                          key={a.id}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-100 bg-white px-3 py-2 text-[11px]"
                        >
                          <div className="flex items-center gap-2">
                            <span className="font-mono font-semibold text-slate-800">{a.action}</span>
                            <span className="text-slate-500">{a.detail}</span>
                          </div>
                          <div className="flex items-center gap-2 text-[10px] text-slate-400">
                            <span>actor: {a.actor_id || "system"} ({a.actor_role || "system"})</span>
                            <StatusBadge status={a.status === "success" || a.status === "approved" ? "resolved" : a.status} size="xs" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
