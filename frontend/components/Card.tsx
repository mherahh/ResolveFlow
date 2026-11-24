export default function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
  noPadding = false,
}: {
  title?: React.ReactNode;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  noPadding?: boolean;
}) {
  return (
    <section className={`rounded-xl border border-slate-200/80 bg-white shadow-xs ${className}`}>
      {(title || action) && (
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <div>
            {typeof title === "string" ? (
              <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
            ) : (
              title
            )}
            {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className={noPadding ? "" : "p-5"}>{children}</div>
    </section>
  );
}
