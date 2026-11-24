"use client";
import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { get, Ticket, titleCase, formatTimeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_FILTERS = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "awaiting_customer", label: "Awaiting Customer" },
  { id: "awaiting_approval", label: "Awaiting Approval" },
  { id: "processing", label: "Processing" },
  { id: "resolved", label: "Resolved" },
  { id: "escalated", label: "Escalated" },
  { id: "failed", label: "Failed" },
];

export default function Tickets() {
  const { user } = useAuth();
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [selectedStatus, setSelectedStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  function loadTickets() {
    setLoading(true);
    get<Ticket[]>("/api/tickets")
      .then((data) => {
        setTickets(data);
        setLoading(false);
      })
      .catch(() => {
        setTickets([]);
        setLoading(false);
      });
  }

  useEffect(() => {
    if (user) loadTickets();
  }, [user?.id]);

  const filteredTickets = useMemo(() => {
    if (!tickets) return [];
    return tickets.filter((t) => {
      const matchesStatus = selectedStatus === "all" || t.status === selectedStatus;
      const q = search.toLowerCase().trim();
      const matchesSearch =
        !q ||
        t.id.toLowerCase().includes(q) ||
        (t.customer_id && t.customer_id.toLowerCase().includes(q)) ||
        (t.order_id && t.order_id.toLowerCase().includes(q)) ||
        (t.issue_type && t.issue_type.toLowerCase().includes(q)) ||
        t.message.toLowerCase().includes(q);
      return matchesStatus && matchesSearch;
    });
  }, [tickets, selectedStatus, search]);

  const countsByStatus = useMemo(() => {
    const res: Record<string, number> = { all: tickets?.length ?? 0 };
    tickets?.forEach((t) => {
      res[t.status] = (res[t.status] || 0) + 1;
    });
    return res;
  }, [tickets]);

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Support Tickets</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {user?.role === "customer"
              ? "Your authenticated support ticket history and verified statuses."
              : "Manage, triage, and audit all customer support tickets."}
          </p>
        </div>
        <Link
          href="/chat"
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700 transition-colors self-start"
        >
          + Create Request
        </Link>
      </div>

      {/* Filter & Search */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 bg-white p-3 rounded-xl border border-slate-200/80 shadow-xs">
        <div className="flex items-center gap-1 overflow-x-auto pb-1 lg:pb-0">
          {STATUS_FILTERS.map((f) => {
            const active = selectedStatus === f.id;
            const count = countsByStatus[f.id] ?? 0;
            return (
              <button
                key={f.id}
                onClick={() => setSelectedStatus(f.id)}
                className={`whitespace-nowrap px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 ${
                  active ? "bg-slate-900 text-white shadow-xs" : "text-slate-600 hover:text-slate-900 hover:bg-slate-100"
                }`}
              >
                <span>{f.label}</span>
                <span
                  className={`text-[10px] rounded-full px-1.5 ${
                    active ? "bg-slate-700 text-slate-200" : "bg-slate-200 text-slate-600"
                  }`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search ID, order, inquiry..."
          className="lg:w-64 rounded-lg border border-slate-300 bg-slate-50/50 px-3 py-1.5 text-xs text-slate-800 placeholder-slate-400 focus:bg-white focus:border-indigo-500 focus:outline-hidden"
        />
      </div>

      <Card noPadding>
        {loading ? (
          <div className="p-8 text-center text-sm text-slate-400 animate-pulse">Loading tickets...</div>
        ) : filteredTickets.length === 0 ? (
          <div className="p-12 text-center text-sm text-slate-500">
            <p className="font-medium text-slate-700">No tickets match your filter.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50/70 border-b border-slate-100 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                <tr>
                  <th className="px-5 py-3.5">Ticket ID</th>
                  <th className="px-3 py-3.5">Customer</th>
                  <th className="px-3 py-3.5">Category</th>
                  <th className="px-3 py-3.5">Linked Order</th>
                  <th className="px-4 py-3.5">Status</th>
                  <th className="px-4 py-3.5">Inquiry</th>
                  <th className="px-5 py-3.5 text-right">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredTickets.map((t) => (
                  <tr key={t.id} className="hover:bg-slate-50/70 transition-colors">
                    <td className="px-5 py-3.5 font-mono text-xs font-bold">
                      <Link href={`/tickets/${t.id}`} className="text-indigo-600 hover:underline">
                        {t.id}
                      </Link>
                    </td>
                    <td className="px-3 py-3.5 text-xs font-medium text-slate-700">{t.customer_id ?? "—"}</td>
                    <td className="px-3 py-3.5">
                      <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                        {titleCase(t.issue_type)}
                      </span>
                    </td>
                    <td className="px-3 py-3.5 font-mono text-xs text-slate-600">
                      {t.order_id ? (
                        <span className="bg-indigo-50 text-indigo-700 px-1.5 py-0.5 rounded text-[11px] font-semibold">
                          {t.order_id}
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5">
                      <StatusBadge status={t.status} size="xs" />
                    </td>
                    <td className="px-4 py-3.5 max-w-sm truncate text-xs text-slate-700">{t.message}</td>
                    <td className="px-5 py-3.5 text-right text-xs text-slate-400 whitespace-nowrap">
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
  );
}
