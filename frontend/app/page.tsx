"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { get, DashboardStats, titleCase, formatTimeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Dashboard() {
  const { user, demoModeEnabled, switchDemo } = useAuth();
  const [d, setD] = useState<DashboardStats | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  const fetchStats = () => {
    setLoading(true);
    setErr("");
    get<DashboardStats>("/api/dashboard")
      .then((data) => {
        setD(data);
        setLoading(false);
      })
      .catch((e) => {
        setErr(e?.message || "Cannot reach backend API. Ensure the backend server is running on port 8000.");
        setLoading(false);
      });
  };

  useEffect(() => {
    if (user) fetchStats();
  }, [user?.id]);

  if (err) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-rose-800">
        <h3 className="font-semibold text-base mb-1">Backend Connection Notice</h3>
        <p className="text-sm mb-2">{err}</p>
        <button
          onClick={fetchStats}
          className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-700 transition-colors"
        >
          Retry Connection
        </button>
      </div>
    );
  }

  if (loading || !d) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-8 w-48 bg-slate-200 rounded"></div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[1, 2, 3, 4, 5, 6, 7].map((i) => (
            <div key={i} className="h-24 bg-slate-200 rounded-xl"></div>
          ))}
        </div>
      </div>
    );
  }

  const kpis = [
    {
      label: "Total Tickets",
      val: d.total,
      sub: `${d.open} open · ${d.processing} processing`,
      col: "text-slate-900",
      bg: "bg-white",
    },
    {
      label: "Resolved Tickets",
      val: d.resolved,
      sub: `${d.resolution_rate}% total resolution`,
      col: "text-emerald-700",
      bg: "bg-emerald-50/40",
    },
    {
      label: "Autonomous Rate",
      val: `${d.autonomous_resolution_rate}%`,
      sub: `${d.autonomous_resolved} resolved autonomously`,
      col: "text-indigo-700",
      bg: "bg-indigo-50/40",
    },
    {
      label: "Pending Approvals",
      val: d.pending_approvals,
      sub: "Awaiting human manager",
      col: "text-amber-700",
      bg: "bg-amber-50/40",
    },
    {
      label: "Awaiting Customer",
      val: d.awaiting_customer,
      sub: "Pending confirmation / info",
      col: "text-sky-700",
      bg: "bg-sky-50/40",
    },
    {
      label: "Escalated Tickets",
      val: d.escalated,
      sub: "Human specialist handoff",
      col: "text-rose-700",
      bg: "bg-rose-50/40",
    },
    {
      label: "Failed Operations",
      val: d.failed,
      sub: "Execution / verification failures",
      col: "text-red-700",
      bg: "bg-red-50/40",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Scope Notice when logged in as customer */}
      {d.scope === "customer" && (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-600 flex flex-wrap items-center justify-between gap-2">
          <span>
            Showing metrics scoped strictly to your account (<strong>{user?.name}</strong>). Full organization-wide operations metrics require a Manager account.
          </span>
          {demoModeEnabled && (
            <button
              onClick={() => switchDemo("M001")}
              className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 transition-colors"
            >
              Switch to Demo Manager (M001)
            </button>
          )}
        </div>
      )}

      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Support Operations Dashboard</h1>
          <p className="text-sm text-slate-500 mt-1">
            Live workflow metrics computed strictly from verified database records.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/chat"
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700 transition-colors"
          >
            Open Support Inbox
          </Link>
          {user?.role !== "customer" && (
            <Link
              href="/approvals"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              Review Approvals ({d.pending_approvals})
            </Link>
          )}
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
        {kpis.map((k) => (
          <div
            key={k.label}
            className={`rounded-xl border border-slate-200/80 p-4 shadow-xs ${k.bg}`}
          >
            <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider leading-tight">
              {k.label}
            </div>
            <div className={`text-2xl font-bold tracking-tight mt-2 ${k.col}`}>{k.val}</div>
            <div className="mt-1 text-[11px] text-slate-500 leading-snug">{k.sub}</div>
          </div>
        ))}
      </div>

      {/* Intent Breakdown & Recent Ticket Feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card
          title="Intent & Inquiry Distribution"
          subtitle="Customer inquiries categorized by intake agent"
          className="lg:col-span-1"
        >
          {Object.keys(d.intents || {}).length === 0 ? (
            <p className="text-xs text-slate-400 py-4 text-center">No classified inquiries yet.</p>
          ) : (
            <div className="space-y-3.5">
              {Object.entries(d.intents).map(([intent, count]) => {
                const pct = d.total > 0 ? Math.round((count / d.total) * 100) : 0;
                return (
                  <div key={intent} className="space-y-1">
                    <div className="flex justify-between text-xs font-medium">
                      <span className="text-slate-700">{titleCase(intent)}</span>
                      <span className="text-slate-500 font-mono">
                        {count} ({pct}%)
                      </span>
                    </div>
                    <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-indigo-600 transition-all duration-500"
                        style={{ width: `${Math.max(pct, 4)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card
          title="Recent Tickets"
          subtitle="Latest support interactions and verified outcomes"
          action={
            <Link href="/tickets" className="text-xs font-semibold text-indigo-600 hover:text-indigo-800">
              View all tickets →
            </Link>
          }
          className="lg:col-span-2"
          noPadding
        >
          {d.recent.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-500">
              No tickets recorded yet. Open the{" "}
              <Link href="/chat" className="text-indigo-600 underline font-medium">
                Support Inbox
              </Link>{" "}
              to initiate a support request.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50/70 border-b border-slate-100 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  <tr>
                    <th className="px-5 py-3">Ticket ID</th>
                    <th className="px-3 py-3">Customer</th>
                    <th className="px-3 py-3">Inquiry</th>
                    <th className="px-3 py-3">Order</th>
                    <th className="px-3 py-3">Status</th>
                    <th className="px-5 py-3 text-right">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {d.recent.map((t) => (
                    <tr key={t.id} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-5 py-3.5">
                        <Link
                          href={`/tickets/${t.id}`}
                          className="font-mono text-xs font-semibold text-indigo-600 hover:underline"
                        >
                          {t.id}
                        </Link>
                      </td>
                      <td className="px-3 py-3.5 text-xs text-slate-600 font-medium">
                        {t.customer_id ?? "—"}
                      </td>
                      <td className="px-3 py-3.5 max-w-xs truncate text-xs text-slate-800">
                        {t.message}
                      </td>
                      <td className="px-3 py-3.5">
                        {t.order_id ? (
                          <span className="inline-flex items-center font-mono text-[11px] font-medium text-slate-700 bg-slate-100 rounded px-1.5 py-0.5">
                            {t.order_id}
                          </span>
                        ) : (
                          <span className="text-slate-400 text-xs">—</span>
                        )}
                      </td>
                      <td className="px-3 py-3.5">
                        <StatusBadge status={t.status} size="xs" />
                      </td>
                      <td className="px-5 py-3.5 text-right text-xs text-slate-400">
                        {formatTimeAgo(t.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
