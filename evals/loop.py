"""The improvement loop: run -> diagnose -> patch the versioned policy -> re-run -> gate -> (human approves) -> promote.

  python -m evals.loop --runs 3 --max-iters 3

A patch is promoted only if: (1) the target scenarios gain passes, (2) no other scenario confirmed-regresses
(a drop is re-run once to rule out flakiness), and (3) safety and privacy rates do not fall.
Nothing is auto-shipped by default: --approve ask shows the diff and waits for a human.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agent import policy as P
from agent.llm import get_llm

from .diagnoser import diagnose
from .report import table
from .runner import ROOT, Suite, run_suite
from .scenarios import BY_ID, DEV, HELDOUT

LEDGER = Path(__file__).resolve().parent / "ledger.jsonl"
PROPOSALS = Path(__file__).resolve().parent.parent / "proposals"
SEVERITY = {"safety": 0, "privacy": 1, "correctness": 2, "ux": 3}


def worst_category(d: dict, base: Suite) -> int:
    cats = [c["category"] for r in base.failing() if r["scenario"] in d["scenario_ids"] for c in r["checks"] if not c["passed"]]
    return min((SEVERITY.get(c, 9) for c in cats), default=9)


def gate(base: Suite, cand: Suite, targets: list, rerun) -> dict:
    b, c = base.stats(), cand.stats()
    gain = sum(c.get(s, {"passed": 0})["passed"] - b.get(s, {"passed": 0})["passed"] for s in targets)
    regressed = [s for s in b if s not in targets and s in c and c[s]["passed"] < b[s]["passed"]]
    confirmed, flaky, reruns = [], [], []
    for s in regressed:  # one re-run to separate real regressions from model noise
        extra = rerun(s)
        (confirmed if extra.failing() else flaky).append(s)
        reruns += extra.runs
    # Judge safety/privacy on the same evidence: a flaky scenario counts by its clean re-run, not its one bad sample.
    # Flaky failures are not hidden: they are printed for the human who approves the patch.
    judged = Suite(tag=cand.tag, runs=[r for r in cand.runs if r["scenario"] not in flaky]
                   + [r for r in reruns if r["scenario"] in flaky])
    drops = [cat for cat in ("safety", "privacy")
             if (base.category_rate(cat) is not None and (judged.category_rate(cat) or 0) < base.category_rate(cat))]
    accept = gain > 0 and not confirmed and not drops
    reason = ("ok" if accept else
              f"gain={gain}" + (f", confirmed regressions={confirmed}" if confirmed else "") +
              (f", {drops} rate dropped" if drops else ""))
    return {"accept": accept, "gain": gain, "regressed": regressed, "confirmed": confirmed, "flaky": flaky,
            "category_drops": drops, "reason": reason, "judged": judged}


def log(event: dict) -> None:
    ROOT.mkdir(exist_ok=True)
    with open(ROOT / "loop_log.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **event}) + "\n")


def write_proposal(d: dict, n: int) -> None:
    PROPOSALS.mkdir(exist_ok=True)
    p = PROPOSALS / f"proposal_{int(time.time())}_{n}.md"
    p.write_text(f"# Code change proposed by diagnoser (needs human review, not applied)\n\n"
                 f"failure_class: {d['failure_class']}\nscenarios: {d['scenario_ids']}\n\nroot cause: {d['root_cause']}\n\n"
                 f"{d['patch'].get('description', '')}\n")
    print(f"  -> wrote {p.name} for human review")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per scenario (agents are stochastic)")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--max-patches", type=int, default=3, help="candidate patches tried per iteration")
    ap.add_argument("--approve", choices=["ask", "auto"], default="ask",
                    help="ask = human reviews the diff before promotion (default); auto = demo/CI only")
    ap.add_argument("--from-version", type=int, default=0)
    ap.add_argument("--no-heldout", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated dev scenario ids (a short demo); default: all")
    args = ap.parse_args()

    unknown = [i for i in args.only.split(",") if i and i not in BY_ID]
    if unknown:
        ap.error(f"unknown scenario ids {unknown}; choose from {[s.id for s in DEV]}")
    dev = [BY_ID[i] for i in args.only.split(",") if i] or DEV
    heldout = [s for s in HELDOUT if not args.only or s.heldout_of in {d.id for d in dev}]
    llm = get_llm()
    policy = P.load(args.from_version)
    print(f"\n=== baseline: policy v{policy['version']} ===")
    baseline = first = run_suite(dev, policy, args.runs, llm)
    print("\n" + table(baseline, title=f"baseline v{policy['version']}"))
    history: list = []

    for it in range(1, args.max_iters + 1):
        if not baseline.failing():
            print("\nAll dev scenarios pass. Nothing to fix.")
            break
        print(f"\n=== iteration {it}: diagnosing {len(baseline.failing())} failing runs ===")
        diags = diagnose(llm, policy, baseline.failing(), history)
        diags.sort(key=lambda d: worst_category(d, baseline))
        promoted = False
        for n, d in enumerate(diags[: args.max_patches]):
            print(f"\n- {d['failure_class']} [{d['fix_layer']}] scenarios={d['scenario_ids']}\n  root cause: {d['root_cause']}"
                  f"\n  patch: {json.dumps(d['patch'])}")
            if d["fix_layer"] == "code_proposal":
                write_proposal(d, n)
                continue
            try:
                cand = P.apply_patch(policy, d["patch"])
            except P.PatchError as e:
                print(f"  rejected before eval: {e}")
                history.append({"patch": d["patch"], "result": f"invalid: {e}"})
                continue
            targets = [s for s in d["scenario_ids"] if s in BY_ID]
            res = run_suite(dev, cand, args.runs, llm)
            verdict = gate(baseline, res, targets,
                           lambda s: run_suite([BY_ID[s]], cand, args.runs, llm, start=args.runs))
            judged = verdict.pop("judged")  # the evidence the gate decided on; tables and the next baseline use it too
            print(f"  gate: {'ACCEPT' if verdict['accept'] else 'REJECT'} ({verdict['reason']}; flaky={verdict['flaky']})")
            log({"iteration": it, "diagnosis": d, "verdict": verdict, "candidate": P.tag(cand)})
            if not verdict["accept"]:
                history.append({"patch": d["patch"], "result": verdict["reason"]})
                continue
            print("\n" + P.diff(policy, cand) + "\n\n"
                  + table(baseline, judged, title="candidate vs current (flaky scenarios counted by their re-run)"))
            for s in verdict["flaky"]:
                failed = {c["name"] for r in res.runs if r["scenario"] == s for c in r["checks"] if not c["passed"]}
                print(f"  ! {s} failed once ({', '.join(sorted(failed))}), then passed on re-run. Review before approving.")
            if args.approve == "ask" and input("\nPromote this patch? [y/N] ").strip().lower() != "y":
                history.append({"patch": d["patch"], "result": "rejected by human reviewer"})
                continue
            P.save(cand)
            with open(LEDGER, "a") as f:
                f.write(json.dumps({"from": policy["version"], "to": cand["version"], "diagnosis": d,
                                    "before": baseline.stats(), "after": judged.stats(),
                                    "flaky_first_samples": verdict["flaky"]}) + "\n")
            policy, baseline, promoted = cand, judged, True
            print(f"  promoted -> policy v{policy['version']}")
            break
        if not promoted:
            print("\nNo candidate passed the gate. Stopping; see runs/loop_log.jsonl and proposals/.")
            break

    report = [table(first, baseline, title=f"dev set: v{args.from_version} -> v{policy['version']}")]
    if heldout and not args.no_heldout and policy["version"] != args.from_version:
        print("\n=== held-out check (variants the loop never saw) ===")
        h0 = run_suite(heldout, P.load(args.from_version), args.runs, llm, label="_heldout")
        h1 = run_suite(heldout, policy, args.runs, llm, label="_heldout")
        report.append(table(h0, h1, title="held-out generalisation"))
    out = "\n\n".join(report)
    print("\n" + out)
    ROOT.mkdir(exist_ok=True)
    (ROOT / "report.md").write_text(out + "\n")
    print(f"\nreport written to {ROOT / 'report.md'}")


if __name__ == "__main__":
    main()
