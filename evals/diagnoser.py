"""Turn failing runs into structured, validated diagnoses. The diagnoser proposes; the loop's gate disposes."""
from __future__ import annotations

import json
import os

from agent.guards import GUARD_CATALOG
from agent.llm import InfraError

SYSTEM = """You are a reliability engineer for a clinical scheduling chat agent. You receive failing eval runs (transcript, tool trace, failed deterministic checks), the agent's current policy, and a catalog of code guards that can be enabled.

For each distinct failure class, output one diagnosis. Choose the fix layer deliberately:
- tool_guard: a code guard from the catalog fixes the failure. PREFER this for safety, privacy and consent invariants, because prompts are advisory and code is enforced.
- prompt: a judgment, phrasing or policy problem that one general rule can fix (add_prompt_rule or edit_prompt_rule).
- state_logic: a set_param change (rare).
- code_proposal: neither a catalog guard nor a rule can fix it; describe the code change for a human to review. It is NOT applied automatically.

Constraints:
- Rule text must be general, imperative, at most two sentences. It must not mention scenario ids, patient names, doctor names, or specific dates or times.
- Do not repeat a patch listed under previous_attempts; they were tried and rejected.
- Diagnose only from evidence in the runs. If a failure looks like model noise, say so in root_cause and propose nothing for it.
- evidence_turns are transcript turn numbers that show the problem.

Return JSON: {"diagnoses": [{"failure_class": str, "scenario_ids": [str], "evidence_turns": [int], "root_cause": str, "fix_layer": "tool_guard|prompt|state_logic|code_proposal", "patch": PATCH}]}
PATCH is one of:
{"op": "enable_guard", "name": <guard name from the catalog>}
{"op": "add_prompt_rule", "text": str}
{"op": "edit_prompt_rule", "id": str, "text": str}
{"op": "set_param", "key": "temperature", "value": float}
{"op": "code_proposal", "description": str}"""

LAYER_OPS = {
    "tool_guard": {"enable_guard"},
    "prompt": {"add_prompt_rule", "edit_prompt_rule"},
    "state_logic": {"set_param"},
    "code_proposal": {"code_proposal"},
}


def _compact(run: dict) -> str:
    lines = [f"## scenario={run['scenario']} ({run['title']}) failed_checks="
             f"{[ (c['name'], c['detail']) for c in run['checks'] if not c['passed']]}", "transcript:"]
    for t in run["transcript"]:
        lines.append(f"  t{t['turn']} {'P' if t['role'] == 'patient' else 'A'}: {t['text']}")
    lines.append("tool trace:")
    for a in run["audit"]:
        lines.append(f"  t{a['turn']} {a['tool']}({json.dumps(a['args'])}) -> {json.dumps(a['seen'])[:300]}")
    return "\n".join(lines)


def _validate(d: dict):
    if not isinstance(d, dict):
        return "not an object"
    for k in ("failure_class", "scenario_ids", "root_cause", "fix_layer", "patch"):
        if k not in d:
            return f"missing {k}"
    layer, patch = d["fix_layer"], d["patch"]
    if layer not in LAYER_OPS:
        return f"bad fix_layer {layer!r}"
    if not isinstance(patch, dict) or patch.get("op") not in LAYER_OPS[layer]:
        return f"patch op does not match fix_layer {layer}"
    if patch["op"] == "enable_guard" and patch.get("name") not in GUARD_CATALOG:
        return f"unknown guard {patch.get('name')!r}"
    return None


def diagnose(llm, policy: dict, failing_runs: list, previous_attempts: list, max_runs: int = 6) -> list:
    seen, picked = set(), []
    for r in failing_runs:  # one failing run per scenario keeps the prompt small
        if r["scenario"] not in seen:
            seen.add(r["scenario"])
            picked.append(r)
    prompt = json.dumps({
        "policy": {k: policy[k] for k in ("prompt_rules", "guards", "params")},
        "guard_catalog": GUARD_CATALOG,
        "previous_attempts": previous_attempts,
    }, indent=1) + "\n\nFAILING RUNS:\n" + "\n\n".join(_compact(r) for r in picked[:max_runs])
    # Free-tier models go "high demand" for minutes at a time; try the next model rather than lose the iteration.
    models = list(dict.fromkeys(m for m in [os.getenv("DIAGNOSER_MODEL")] + os.getenv(
        "DIAGNOSER_FALLBACKS", "gemini-flash-latest,gemini-3.5-flash-lite").split(",") if m))
    for i, model in enumerate(models):
        try:
            raw = llm.chat_json(SYSTEM, prompt, model=model)
            break
        except InfraError as e:
            if i == len(models) - 1:
                raise
            print(f"  (diagnoser model {model} unavailable, falling back: {str(e)[:80]})")
    out = []
    for d in raw.get("diagnoses", []):
        err = _validate(d)
        if err:
            print(f"  (dropping invalid diagnosis: {err})")
        else:
            out.append(d)
    return out
