"""Run scenarios against a policy. Resumable (one JSON per run), infra errors are never scored as agent failures."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent.agent import Agent
from agent.llm import InfraError
from agent.policy import tag as policy_tag
from agent.world import HOOKS, World

from .graders import grade
from .simulator import make_patient

ROOT = Path(__file__).resolve().parent.parent / "runs"
CATEGORIES = ("safety", "privacy", "correctness", "ux")


@dataclass
class Suite:
    tag: str
    runs: list = field(default_factory=list)
    infra_errors: int = 0

    def stats(self) -> dict:
        s: dict = {}
        for r in self.runs:
            d = s.setdefault(r["scenario"], {"n": 0, "passed": 0})
            d["n"] += 1
            d["passed"] += int(r["passed"])
        return s

    def category_rate(self, cat: str):
        rs = [r for r in self.runs if any(c["category"] == cat for c in r["checks"])]
        if not rs:
            return None
        ok = sum(all(c["passed"] for c in r["checks"] if c["category"] == cat) for r in rs)
        return ok / len(rs)

    def overall(self) -> float:
        return sum(r["passed"] for r in self.runs) / len(self.runs) if self.runs else 0.0

    def failing(self) -> list:
        return [r for r in self.runs if not r["passed"]]


def run_one(scn, policy, llm, judge=False, seed=0) -> dict:
    world = World()
    for h in scn.setup.get("hooks", []):
        tool, fn = HOOKS[h]
        world.hooks.setdefault(tool, []).append(fn)
    world.fail_plan = {k: dict(v) for k, v in scn.setup.get("fail", {}).items()}
    agent = Agent(world, policy, llm, chaos=float(os.getenv("CHAOS", "0")), seed=seed)
    patient = make_patient(scn.patient, llm)
    msg, n = patient.first(), 1
    while True:
        reply = agent.respond(msg)
        if n >= scn.max_turns:
            break
        nxt, final = patient.next(reply, n + 1)
        if nxt is None:
            break
        n += 1
        if final:
            agent.note_final_patient_message(nxt)
            break
        msg = nxt
    snap = world.snapshot()
    checks = grade(scn, agent.transcript, snap["audit"], snap)
    run = {"scenario": scn.id, "title": scn.title, "policy": policy["version"], "seed": seed,
           "passed": all(c["passed"] for c in checks), "checks": checks,
           "transcript": agent.transcript, "audit": snap["audit"], "handoffs": snap["handoffs"],
           "waitlist": snap["waitlist"]}
    if judge:
        from .judge import judge_run
        run["judge"] = judge_run(llm, run)
    return run


def run_suite(scenarios, policy, runs, llm, start=0, judge=False, label="") -> Suite:
    t = policy_tag(policy) + label
    out = ROOT / t
    out.mkdir(parents=True, exist_ok=True)
    suite = Suite(tag=t)
    for scn in scenarios:
        for k in range(start, start + runs):
            f = out / f"{scn.id}-{scn.spec_hash()}__{k}.json"
            if f.exists():  # resume
                run = json.loads(f.read_text())
            else:
                try:
                    run = run_one(scn, policy, llm, judge, seed=k)
                except InfraError as e:
                    suite.infra_errors += 1
                    print(f"[{t}] {scn.id} #{k} INFRA_ERROR (not scored, rerun to retry): {e}")
                    continue
                f.write_text(json.dumps(run, indent=1))
            suite.runs.append(run)
            failed = [c["name"] for c in run["checks"] if not c["passed"]]
            print(f"[{t}] {scn.id} #{k} {'PASS' if run['passed'] else 'FAIL ' + ', '.join(failed)}")
    return suite
