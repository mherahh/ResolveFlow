import { TraceEvent, STEP_LABEL, STAGE_LABEL } from "@/lib/api";

const NODE_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  intake: { bg: "bg-blue-500", border: "border-blue-200", text: "text-blue-700" },
  retrieve: { bg: "bg-indigo-500", border: "border-indigo-200", text: "text-indigo-700" },
  verify_order: { bg: "bg-sky-500", border: "border-sky-200", text: "text-sky-700" },
  resolve: { bg: "bg-purple-500", border: "border-purple-200", text: "text-purple-700" },
  policy: { bg: "bg-amber-500", border: "border-amber-200", text: "text-amber-700" },
  confirmation: { bg: "bg-violet-500", border: "border-violet-200", text: "text-violet-700" },
  execute: { bg: "bg-emerald-500", border: "border-emerald-200", text: "text-emerald-700" },
  verify: { bg: "bg-teal-500", border: "border-teal-200", text: "text-teal-700" },
  approval: { bg: "bg-amber-600", border: "border-amber-300", text: "text-amber-800" },
  escalate: { bg: "bg-rose-500", border: "border-rose-200", text: "text-rose-700" },
  respond: { bg: "bg-slate-700", border: "border-slate-200", text: "text-slate-700" },
};

export default function Timeline({ trace }: { trace: TraceEvent[] }) {
  if (!trace?.length) {
    return (
      <div className="py-6 text-center text-sm text-slate-400">
        No workflow events recorded for this session.
      </div>
    );
  }

  return (
    <div className="relative pl-6">
      <div className="absolute left-[11px] top-3 bottom-3 w-0.5 bg-slate-200" />

      <ol className="space-y-3.5">
        {trace.map((e, i) => {
          const col = NODE_COLORS[e.node] ?? { bg: "bg-slate-400", border: "border-slate-200", text: "text-slate-600" };
          const stageInfo = e.stage ? STAGE_LABEL[e.stage] : null;
          return (
            <li key={i} className="relative flex items-start gap-3">
              <div
                className={`relative z-10 -ml-6 mt-1 flex h-6 w-6 items-center justify-center rounded-full bg-white ring-2 ${col.border} shadow-xs`}
              >
                <div className={`h-2 w-2 rounded-full ${col.bg}`} />
              </div>

              <div className="flex-1 rounded-lg border border-slate-100 bg-slate-50/60 p-3 transition-colors hover:bg-slate-50">
                <div className="flex flex-wrap items-center justify-between gap-1.5 mb-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-700">
                      {STEP_LABEL[e.node] ?? e.node}
                    </span>
                    {stageInfo && (
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${stageInfo.color}`}>
                        {stageInfo.label}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {e.tool && (
                      <span className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-mono font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200/50">
                        tool: {e.tool}
                      </span>
                    )}
                    <span className="text-[10px] text-slate-400 font-mono">step {i + 1}</span>
                  </div>
                </div>

                <div className="text-xs text-slate-700 font-medium">{e.summary}</div>

                {e.node === "policy" && e.detail && (
                  <div className="mt-1.5 rounded border border-amber-200/60 bg-amber-50/50 p-2 text-xs text-amber-900">
                    <span className="font-semibold text-amber-800">Policy Evaluation: </span>
                    {e.detail}
                  </div>
                )}
                {e.node !== "policy" && e.detail && (
                  <div className="mt-1 text-xs text-slate-500">{e.detail}</div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
