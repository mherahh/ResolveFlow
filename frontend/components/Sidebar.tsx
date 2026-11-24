"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { API, AuthUser, getDemoAccounts, resetDemo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const LINKS = [
  {
    href: "/",
    label: "Dashboard",
    icon: (
      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
      </svg>
    ),
  },
  {
    href: "/chat",
    label: "Support Inbox",
    badge: "AI",
    icon: (
      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    href: "/tickets",
    label: "Tickets",
    icon: (
      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
  {
    href: "/approvals",
    label: "Approvals & Triage",
    managerOnly: true,
    icon: (
      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    href: "/activity",
    label: "Agent Activity",
    icon: (
      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const path = usePathname();
  const { user, isDemo, demoModeEnabled, logout, switchDemo } = useAuth();
  const [online, setOnline] = useState<boolean | null>(null);
  const [resetting, setResetting] = useState(false);
  const [demoAccounts, setDemoAccounts] = useState<AuthUser[]>([]);
  const [showDemoSwitcher, setShowDemoSwitcher] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/health`)
      .then((r) => setOnline(r.ok))
      .catch(() => setOnline(false));
  }, []);

  useEffect(() => {
    if (demoModeEnabled && user) {
      getDemoAccounts()
        .then(setDemoAccounts)
        .catch(() => setDemoAccounts([]));
    }
  }, [demoModeEnabled, user]);

  async function handleReset() {
    if (!confirm("Reset database to initial sample orders and customers?")) return;
    setResetting(true);
    try {
      await resetDemo();
      await logout();
      window.location.reload();
    } catch {
      alert("Failed to reset database.");
    } finally {
      setResetting(false);
    }
  }

  return (
    <aside className="md:w-64 md:min-h-screen bg-slate-900 text-slate-300 flex flex-col justify-between border-b md:border-b-0 md:border-r border-slate-800 shrink-0">
      <div>
        {/* Brand header */}
        <div className="p-5 flex items-center justify-between border-b border-slate-800/80">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-base shadow-sm group-hover:bg-indigo-500 transition-colors">
              R
            </div>
            <div>
              <span className="font-semibold text-white text-base tracking-tight block">ResolveFlow</span>
              <span className="text-[10px] text-slate-400 font-medium block uppercase tracking-wider">AI Support Platform</span>
            </div>
          </Link>
          <div className="flex items-center gap-1.5" title={online ? "Backend Online" : "Backend Offline"}>
            <span className={`h-2 w-2 rounded-full ${online === null ? "bg-slate-500 animate-pulse" : online ? "bg-emerald-500" : "bg-rose-500"}`} />
            <span className="text-[11px] text-slate-400 font-mono hidden md:inline">
              {online ? "v2.0" : "offline"}
            </span>
          </div>
        </div>

        {/* Navigation list */}
        <nav className="p-3">
          <div className="text-[11px] font-semibold text-slate-400 px-3 py-2 uppercase tracking-wider hidden md:block">
            Workspace
          </div>
          <ul className="flex md:flex-col gap-1 overflow-x-auto">
            {LINKS.map((l) => {
              const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
              return (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    className={`flex items-center justify-between gap-3 px-3 py-2 text-sm rounded-lg font-medium transition-all ${
                      active
                        ? "bg-indigo-600/15 text-indigo-400 shadow-xs border border-indigo-500/20"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <span className={active ? "text-indigo-400" : "text-slate-400"}>{l.icon}</span>
                      <span>{l.label}</span>
                    </div>
                    {l.badge && (
                      <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-300">
                        {l.badge}
                      </span>
                    )}
                    {l.managerOnly && (
                      <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-semibold text-amber-300 uppercase">
                        Mgr
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>

      {/* Authenticated Account + Separated Dev/Demo Controls */}
      <div className="p-4 border-t border-slate-800/80 space-y-3">
        {user && (
          <div className="rounded-lg bg-slate-800/80 p-3 border border-slate-700/60 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-semibold text-white truncate">{user.name}</div>
                <div className="text-[11px] text-slate-400 truncate">{user.email}</div>
              </div>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                  user.role === "manager" || user.role === "admin"
                    ? "bg-amber-500/20 text-amber-300"
                    : "bg-indigo-500/20 text-indigo-300"
                }`}
              >
                {user.role}
              </span>
            </div>
            {isDemo && (
              <div className="rounded bg-amber-500/15 border border-amber-500/30 px-2 py-1 text-[10px] font-semibold text-amber-300">
                DEV / DEMO SESSION
              </div>
            )}
            <button
              onClick={logout}
              className="w-full rounded-md border border-slate-700 bg-slate-900/70 px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700 hover:text-white transition-colors"
            >
              Sign Out
            </button>
          </div>
        )}

        {/* Separated Developer / Demo Mode Account Switcher (Only when DEMO_MODE=true) */}
        {demoModeEnabled && demoAccounts.length > 0 && (
          <div className="rounded-lg border border-dashed border-amber-500/40 bg-amber-950/30 p-2.5 hidden md:block">
            <button
              type="button"
              onClick={() => setShowDemoSwitcher((v) => !v)}
              className="w-full flex items-center justify-between text-[11px] font-semibold text-amber-300 hover:text-amber-200"
            >
              <span>DEV / DEMO SWITCHER</span>
              <span>{showDemoSwitcher ? "▲" : "▼"}</span>
            </button>
            {showDemoSwitcher && (
              <div className="mt-2 space-y-1.5">
                <select
                  value={user?.id ?? ""}
                  onChange={(e) => {
                    if (e.target.value) switchDemo(e.target.value);
                  }}
                  className="w-full rounded border border-amber-500/40 bg-slate-900 px-2 py-1.5 text-[11px] text-amber-100 focus:outline-hidden"
                >
                  {demoAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      [{a.role === "customer" ? a.id : "MGR"}] {a.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleReset}
                  disabled={resetting}
                  className="w-full text-left flex items-center justify-between text-[11px] text-amber-300/80 hover:text-amber-200 px-1.5 py-1 rounded transition-colors disabled:opacity-50"
                >
                  <span>Reset Demo Database</span>
                  <span>↻</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
