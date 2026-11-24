const STYLES: Record<string, { bg: string; text: string; dot: string; ring: string; label?: string }> = {
  open: {
    bg: "bg-slate-100",
    text: "text-slate-700",
    dot: "bg-slate-500",
    ring: "ring-slate-300/70",
    label: "Open",
  },
  resolved: {
    bg: "bg-emerald-50",
    text: "text-emerald-700",
    dot: "bg-emerald-500",
    ring: "ring-emerald-200/70",
    label: "Resolved",
  },
  confirmed: {
    bg: "bg-emerald-50",
    text: "text-emerald-700",
    dot: "bg-emerald-500",
    ring: "ring-emerald-200/70",
    label: "Confirmed",
  },
  escalated: {
    bg: "bg-rose-50",
    text: "text-rose-700",
    dot: "bg-rose-500",
    ring: "ring-rose-200/70",
    label: "Escalated",
  },
  failed: {
    bg: "bg-red-50",
    text: "text-red-700",
    dot: "bg-red-600",
    ring: "ring-red-200/80",
    label: "Failed",
  },
  awaiting_approval: {
    bg: "bg-amber-50",
    text: "text-amber-700",
    dot: "bg-amber-500",
    ring: "ring-amber-200/70",
    label: "Awaiting Approval",
  },
  pending_approval: {
    bg: "bg-amber-50",
    text: "text-amber-700",
    dot: "bg-amber-500",
    ring: "ring-amber-200/70",
    label: "Awaiting Approval",
  },
  rejected: {
    bg: "bg-slate-100",
    text: "text-slate-600",
    dot: "bg-slate-400",
    ring: "ring-slate-200",
    label: "Rejected",
  },
  cancelled: {
    bg: "bg-slate-100",
    text: "text-slate-600",
    dot: "bg-slate-400",
    ring: "ring-slate-200",
    label: "Cancelled",
  },
  expired: {
    bg: "bg-orange-50",
    text: "text-orange-700",
    dot: "bg-orange-500",
    ring: "ring-orange-200/70",
    label: "Expired",
  },
  superseded: {
    bg: "bg-slate-100",
    text: "text-slate-500",
    dot: "bg-slate-400",
    ring: "ring-slate-200",
    label: "Superseded",
  },
  awaiting_customer: {
    bg: "bg-sky-50",
    text: "text-sky-700",
    dot: "bg-sky-500",
    ring: "ring-sky-200/70",
    label: "Awaiting Customer",
  },
  needs_confirmation: {
    bg: "bg-violet-50",
    text: "text-violet-700",
    dot: "bg-violet-500",
    ring: "ring-violet-200/70",
    label: "Confirmation Needed",
  },
  needs_info: {
    bg: "bg-sky-50",
    text: "text-sky-700",
    dot: "bg-sky-500",
    ring: "ring-sky-200/70",
    label: "Awaiting Customer",
  },
  processing: {
    bg: "bg-indigo-50",
    text: "text-indigo-700",
    dot: "bg-indigo-500",
    ring: "ring-indigo-200/70",
    label: "Processing",
  },
  pending: {
    bg: "bg-violet-50",
    text: "text-violet-700",
    dot: "bg-violet-500",
    ring: "ring-violet-200/70",
    label: "Pending",
  },
  delivered: {
    bg: "bg-emerald-50",
    text: "text-emerald-700",
    dot: "bg-emerald-500",
    ring: "ring-emerald-200/70",
    label: "Delivered",
  },
  shipped: {
    bg: "bg-sky-50",
    text: "text-sky-700",
    dot: "bg-sky-500",
    ring: "ring-sky-200/70",
    label: "Shipped",
  },
};

export default function StatusBadge({ status, size = "sm" }: { status: string; size?: "xs" | "sm" | "md" }) {
  const s = STYLES[status] ?? STYLES.processing;
  const sizeClasses =
    size === "xs"
      ? "px-2 py-0.5 text-[11px]"
      : size === "md"
      ? "px-3 py-1 text-xs"
      : "px-2.5 py-0.5 text-xs";

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium rounded-full ring-1 ring-inset whitespace-nowrap ${sizeClasses} ${s.bg} ${s.text} ${s.ring}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${s.dot}`} />
      <span>{s.label ?? status.replace(/_/g, " ")}</span>
    </span>
  );
}
