"use client";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError, AuthUser, getDemoAccounts } from "@/lib/api";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, demoModeEnabled, sessionNotice, clearNotice, login, register, switchDemo } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [demoAccounts, setDemoAccounts] = useState<AuthUser[]>([]);
  const [showDemoPanel, setShowDemoPanel] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);

  useEffect(() => {
    if (!user && demoModeEnabled) {
      getDemoAccounts()
        .then(setDemoAccounts)
        .catch(() => setDemoAccounts([]));
    }
  }, [user, demoModeEnabled]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="flex flex-col items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-lg animate-pulse">
            R
          </div>
          <p className="text-xs text-slate-500 font-medium">Verifying your session...</p>
        </div>
      </div>
    );
  }

  if (user) {
    return <>{children}</>;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(name.trim(), email.trim(), password);
      }
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Authentication failed. Ensure the backend API is running on port 8000.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDemoPick(accountId: string) {
    setError("");
    setSwitchingId(accountId);
    try {
      await switchDemo(accountId);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Failed to start Demo Mode session.");
    } finally {
      setSwitchingId(null);
    }
  }

  const demoCustomers = demoAccounts.filter((a) => a.role === "customer");
  const demoManagers = demoAccounts.filter((a) => a.role !== "customer");

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md space-y-5">
        {/* Brand */}
        <div className="text-center">
          <div className="mx-auto h-11 w-11 rounded-xl bg-indigo-600 flex items-center justify-center text-white font-bold text-xl shadow-sm">
            R
          </div>
          <h1 className="mt-3 text-2xl font-bold text-slate-900 tracking-tight">ResolveFlow Support</h1>
          <p className="mt-1 text-xs text-slate-500">
            Sign in to view your orders, track shipments, and manage support requests securely.
          </p>
        </div>

        {/* Session notice */}
        {sessionNotice && (
          <div className="rounded-lg border border-sky-200 bg-sky-50 px-3.5 py-2.5 text-xs text-sky-800 flex items-center justify-between">
            <span>{sessionNotice}</span>
            <button onClick={clearNotice} className="text-sky-600 hover:text-sky-900 font-bold ml-2">
              ✕
            </button>
          </div>
        )}

        {/* Standard Customer Authentication Card */}
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex rounded-lg bg-slate-100 p-1 mb-5">
            <button
              type="button"
              onClick={() => {
                setMode("login");
                setError("");
              }}
              className={`flex-1 rounded-md py-1.5 text-xs font-semibold transition-all ${
                mode === "login" ? "bg-white text-slate-900 shadow-2xs" : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("register");
                setError("");
              }}
              className={`flex-1 rounded-md py-1.5 text-xs font-semibold transition-all ${
                mode === "register" ? "bg-white text-slate-900 shadow-2xs" : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Create Account
            </button>
          </div>

          {error && (
            <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-3.5">
            {mode === "register" && (
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Full Name</label>
                <input
                  type="text"
                  required
                  minLength={2}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Ayesha Khan"
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 focus:border-indigo-500 focus:outline-hidden"
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">Email Address</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 focus:border-indigo-500 focus:outline-hidden"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">Password</label>
              <input
                type="password"
                required
                minLength={mode === "register" ? 8 : 1}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "register" ? "At least 8 characters" : "Enter your password"}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 focus:border-indigo-500 focus:outline-hidden"
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-lg bg-indigo-600 py-2.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-700 transition-colors disabled:opacity-50"
            >
              {submitting
                ? mode === "login"
                  ? "Signing in..."
                  : "Creating account..."
                : mode === "login"
                ? "Sign In"
                : "Create Customer Account"}
            </button>
          </form>

          <p className="mt-4 text-[11px] text-slate-400 text-center">
            Server-side authenticated sessions enforce strict order and ticket ownership.
          </p>
        </div>

        {/* Visually Distinct Developer / Demo Mode Drawer (ONLY shown when DEMO_MODE=true on backend) */}
        {demoModeEnabled && (
          <div className="rounded-xl border-2 border-dashed border-amber-300 bg-amber-50/70 p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="rounded bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
                  Dev / Demo Mode
                </span>
                <span className="text-xs font-semibold text-amber-950">Developer Testing Switcher</span>
              </div>
              <button
                type="button"
                onClick={() => setShowDemoPanel((v) => !v)}
                className="text-xs font-semibold text-amber-800 hover:text-amber-950 underline"
              >
                {showDemoPanel ? "Hide" : "Quick Sign-In"}
              </button>
            </div>
            <p className="mt-1 text-[11px] text-amber-800">
              Non-production testing helper. Issues real server-side sessions for seeded accounts without bypassing backend ownership or confirmation checks.
            </p>

            {showDemoPanel && (
              <div className="mt-3 space-y-3 pt-3 border-t border-amber-200/80">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wider text-amber-900 mb-1.5">
                    Seeded Customers (password: Password123!)
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                    {demoCustomers.map((acc) => (
                      <button
                        key={acc.id}
                        type="button"
                        disabled={switchingId !== null}
                        onClick={() => handleDemoPick(acc.id)}
                        className="text-left rounded-lg border border-amber-200 bg-white px-2.5 py-2 hover:border-amber-400 hover:bg-amber-50 transition-colors disabled:opacity-50"
                      >
                        <div className="text-xs font-semibold text-slate-900 flex items-center justify-between">
                          <span>{acc.name}</span>
                          <span className="font-mono text-[10px] text-slate-400">{acc.id}</span>
                        </div>
                        <div className="text-[10px] text-slate-500 truncate">{acc.email}</div>
                      </button>
                    ))}
                  </div>
                </div>

                {demoManagers.length > 0 && (
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-amber-900 mb-1.5">
                      Seeded Support Manager (password: Manager123!)
                    </div>
                    <div className="grid grid-cols-1 gap-1.5">
                      {demoManagers.map((acc) => (
                        <button
                          key={acc.id}
                          type="button"
                          disabled={switchingId !== null}
                          onClick={() => handleDemoPick(acc.id)}
                          className="text-left rounded-lg border border-indigo-200 bg-indigo-50/50 px-2.5 py-2 hover:border-indigo-400 transition-colors disabled:opacity-50 flex items-center justify-between"
                        >
                          <div>
                            <div className="text-xs font-semibold text-indigo-950">{acc.name}</div>
                            <div className="text-[10px] text-indigo-700">{acc.email}</div>
                          </div>
                          <span className="rounded bg-indigo-600 px-2 py-0.5 text-[10px] font-semibold text-white uppercase">
                            {acc.role}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
