"""Turns results.json into a readable Markdown report. It only prints numbers that were measured."""
import math


def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.1%}" if isinstance(v, float) and v <= 1 else str(v)


def write_report(report: dict, path):
    systems = report["systems"]
    names = list(systems)
    lines = ["# ResolveFlow Evaluation Report", "",
             f"- Model: **{report['model']}**", f"- Date (UTC): {report['date']}",
             f"- Scenarios: {report['scenarios']}, trials per scenario: {report['trials']}", ""]
    if "STUB" in report["model"]:
        lines += ["> **This run used a rule-based stub, not a real LLM. It only checks that the evaluation code runs. "
                  "Do not cite these numbers.**", ""]
    keys = [k for k in systems[names[0]]["summary"] if k not in ("runs", "seconds")]
    lines += ["## Summary", "", "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(_fmt(systems[n]["summary"].get(k)) for n in names) + " |")
    lines += ["", "Metric definitions are in `docs/TECHNICAL_DOCUMENTATION.md` (Evaluation metrics).", "",
              "## Failures", ""]
    for n in names:
        fails = [r for r in systems[n]["rows"] if not r["success"]]
        lines.append(f"### {n}: {len(fails)} failed runs of {systems[n]['summary']['runs']}")
        seen = set()
        for r in fails:
            if r["scenario"] in seen:
                continue
            seen.add(r["scenario"])
            why = r["error"] or f"db_ok={r['db_ok']}, human_routed={r['human_routed']} (expected {r['expected_human']})"
            lines.append(f"- `{r['scenario']}`: {why}")
        lines.append("")
    path.write_text("\n".join(lines))
