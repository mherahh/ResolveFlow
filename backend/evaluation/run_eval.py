"""Run the evaluation.

  python -m evaluation.run_eval --system both --trials 5 --judge       (needs an API key in .env)
  python -m evaluation.run_eval --system workflow --trials 1 --stub     (wiring smoke test, NOT a real result)
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from database.seed import reset_and_seed
from evaluation import metrics as mt
from evaluation.scenarios import SCENARIOS
from tools import write_tools

RESULTS_DIR = Path(__file__).parent / "results"


class WorkflowSystem:
    name = "workflow"

    def __init__(self, llm):
        from graph.workflow import build_graph
        self.graph = build_graph(llm)

    def handle(self, message, customer_id, ticket_id):
        try:
            r = self.graph.invoke({"message": message, "customer_id": customer_id, "ticket_id": ticket_id, "trace": []})
            return {"response": r.get("final_response", ""),
                    "human_routed": r.get("outcome") in ("escalated", "pending_approval"), "error": None}
        except Exception as e:
            return {"response": "", "human_routed": False, "error": f"{type(e).__name__}: {e}"}


class BaselineSystem:
    name = "react_baseline"

    def __init__(self, llm):
        self.llm = llm

    def handle(self, message, customer_id, ticket_id):
        from agents.react_baseline import run_baseline
        try:
            return run_baseline(self.llm, message, customer_id)
        except Exception as e:
            return {"response": "", "human_routed": False, "error": f"{type(e).__name__}: {e}"}


def run_trial(system, sc, trial):
    reset_and_seed()
    write_tools.SIMULATE_SILENT_FAILURE.clear()
    write_tools.SIMULATE_SILENT_FAILURE.update(sc.get("silent_fail", []))
    write_tools.EFFECTIVE_WRITES.clear()
    before = mt.snapshot()
    results = [system.handle(msg, sc["customer_id"], f"E-{sc['id']}-{trial}-{i}") for i, msg in enumerate(sc["messages"])]
    after = mt.snapshot()
    write_tools.SIMULATE_SILENT_FAILURE.clear()

    d = mt.diff(before, after)
    d["effective_writes"] = list(write_tools.EFFECTIVE_WRITES)
    exp_changes, exp_refund_rows = mt.expected_diff(sc["expect_write"])
    db_ok = d["changes"] == exp_changes and d["refund_rows_added"] == exp_refund_rows
    human = any(r["human_routed"] for r in results)
    errored = any(r["error"] for r in results)
    changed = bool(d["changes"]) or d["refund_rows_added"] > 0
    final = results[-1]["response"]
    bad_tokens = mt.ungrounded_tokens(" ".join(r["response"] for r in results), sc["messages"], before["raw"], after["raw"])
    return {
        "scenario": sc["id"], "category": sc["category"], "trial": trial,
        "success": db_ok and (human == sc["expect_human"]) and not errored,
        "db_ok": db_ok, "human_routed": human, "expected_human": sc["expect_human"],
        "duplicate_action": mt.is_duplicate_action(d),
        "half_finished": changed and (human or errored or not final),
        "grounded": not bad_tokens and not errored, "ungrounded": bad_tokens,
        "error": next((r["error"] for r in results if r["error"]), None),
        "response": final, "messages": sc["messages"],
    }


def summarize(rows, trials):
    n = len(rows)
    tp = sum(r["human_routed"] and r["expected_human"] for r in rows)
    fp = sum(r["human_routed"] and not r["expected_human"] for r in rows)
    fn = sum((not r["human_routed"]) and r["expected_human"] for r in rows)
    by_sc = {}
    for r in rows:
        by_sc.setdefault(r["scenario"], []).append(r["success"])
    out = {
        "runs": n,
        "task_success_rate": sum(r["success"] for r in rows) / n,
        "grounding_rate": sum(r["grounded"] for r in rows) / n,
        "duplicate_action_rate": sum(r["duplicate_action"] for r in rows) / n,
        "half_finished_rate": sum(r["half_finished"] for r in rows) / n,
        "escalation_precision": mt.safe_div(tp, tp + fp),
        "escalation_recall": mt.safe_div(tp, tp + fn),
        "errors": sum(1 for r in rows if r["error"]),
    }
    for k in sorted({1, min(trials, 5)}):
        out[f"pass^{k}"] = mt.pass_hat_k(list(by_sc.values()), k)
    return out


def judge_rows(llm, rows):
    from pydantic import BaseModel, Field

    class Score(BaseModel):
        score: int = Field(ge=1, le=5, description="1 = poor, 5 = excellent")

    judge = llm.with_structured_output(Score)
    scores = []
    for r in rows:
        if not r["response"]:
            continue
        prompt = ("Rate this customer-support reply from 1 to 5 for clarity, politeness and helpfulness. "
                  f"Customer: {' / '.join(r['messages'])}\nReply: {r['response']}")
        try:
            scores.append(judge.invoke(prompt).score)
        except Exception:
            pass
    return sum(scores) / len(scores) if scores else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["workflow", "baseline", "both"], default="both")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--stub", action="store_true", help="use the rule-based FakeLLM (wiring test only)")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N scenarios")
    args = ap.parse_args()

    if args.stub:
        from tests.fake_llm import FakeLLM
        llm = FakeLLM()
        if args.system != "workflow":
            raise SystemExit("--stub only supports --system workflow (the baseline needs a real tool-calling model).")
        label = "STUB (rule-based FakeLLM) - NOT a measurement of any real model"
    else:
        from config import get_llm, settings
        llm = get_llm()
        label = f"{settings.llm_provider} / {settings.llm_model}"

    systems = []
    if args.system in ("workflow", "both"):
        systems.append(WorkflowSystem(llm))
    if args.system in ("baseline", "both"):
        systems.append(BaselineSystem(llm))

    scenarios = SCENARIOS[: args.limit] if args.limit else SCENARIOS
    report = {"model": label, "trials": args.trials, "scenarios": len(scenarios),
              "date": datetime.now().isoformat(timespec="seconds"), "systems": {}}
    for sysm in systems:
        rows = []
        t0 = time.time()
        for sc in scenarios:
            for trial in range(args.trials):
                rows.append(run_trial(sysm, sc, trial))
            print(f"[{sysm.name}] {sc['id']}: {sum(r['success'] for r in rows if r['scenario'] == sc['id'])}/{args.trials} passed")
        summary = summarize(rows, args.trials)
        summary["seconds"] = round(time.time() - t0, 1)
        if args.judge and not args.stub:
            summary["judge_avg_1_to_5"] = judge_rows(llm, rows)
        report["systems"][sysm.name] = {"summary": summary, "rows": rows}

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(json.dumps(report, indent=2))
    from evaluation.report import write_report
    write_report(report, RESULTS_DIR / "report.md")
    print(f"Wrote {RESULTS_DIR / 'results.json'} and {RESULTS_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
