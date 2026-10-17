"""Versioned policy data: prompt rules, enabled guards, and runtime parameters.

Patches are structured operations rather than free-form text so they remain predictable and reviewable.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from pathlib import Path

from .guards import GUARD_CATALOG

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"

BASE_PROMPT = """You are the appointment-scheduling assistant for a medical clinic. You chat with patients to book, reschedule and cancel appointments.
Today is {today}. All times are clinic-local.
You act only through your tools. Tools are already scoped to the verified caller; you cannot see anyone else's records.
Follow these rules:
{rules}"""

# Rules must stay general. Anything naming seeded people, doctors or dates is a sign of overfitting to a scenario.
BANNED = re.compile(
    r"\b(priya|asha|rohan|meera|john|rao|mehta|iyer|verma|nair|shah|souza)\b|\d{4}-\d{2}-\d{2}|scenario", re.I
)


class PatchError(ValueError):
    pass


def path(version: int) -> Path:
    return POLICY_DIR / f"v{version}.json"


def load(version: int) -> dict:
    return json.loads(path(version).read_text())


def save(policy: dict) -> None:
    path(policy["version"]).write_text(json.dumps(policy, indent=2) + "\n")


def latest_version() -> int:
    return max(int(f.stem[1:]) for f in POLICY_DIR.glob("v*.json"))


def tag(policy: dict) -> str:
    """Content-addressed tag so edited policies never reuse stale checkpointed runs."""
    body = {k: policy[k] for k in ("prompt_rules", "guards", "params")}
    h = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:6]
    return f"v{policy['version']}_{h}"


def render_prompt(policy: dict, now) -> str:
    rules = "\n".join(f"- [{r['id']}] {r['text']}" for r in policy["prompt_rules"])
    return BASE_PROMPT.format(today=now.strftime("%A %Y-%m-%d"), rules=rules)


def _check_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise PatchError("rule text missing")
    if len(text) > 400:
        raise PatchError("rule text too long (max 400 chars)")
    if BANNED.search(text):
        raise PatchError("rule text is scenario-specific (names, doctors or dates); keep rules general")
    return text.strip()


def apply_patch(policy: dict, patch: dict) -> dict:
    p = copy.deepcopy(policy)
    op = patch.get("op")
    if op == "add_prompt_rule":
        text = _check_text(patch.get("text"))
        ids = {r["id"] for r in p["prompt_rules"]}
        n = len(ids) + 1
        while f"R{n}" in ids:
            n += 1
        p["prompt_rules"].append({"id": f"R{n}", "text": text})
    elif op == "edit_prompt_rule":
        rule = next((r for r in p["prompt_rules"] if r["id"] == patch.get("id")), None)
        if rule is None:
            raise PatchError(f"unknown rule id {patch.get('id')!r}")
        rule["text"] = _check_text(patch.get("text"))
    elif op == "enable_guard":
        if patch.get("name") not in GUARD_CATALOG:
            raise PatchError(f"unknown guard {patch.get('name')!r}")
        p["guards"][patch["name"]] = True
    elif op == "set_param":
        try:
            value = float(patch.get("value"))
        except (TypeError, ValueError):
            raise PatchError("param value must be a number") from None
        if patch.get("key") != "temperature" or not 0.0 <= value <= 1.0:
            raise PatchError("only temperature in [0,1] may be set")
        p["params"]["temperature"] = value
    else:
        raise PatchError(f"op {op!r} cannot be applied automatically")
    p["version"] = policy["version"] + 1
    p["parent"] = policy["version"]
    p["patch"] = patch
    return p


def diff(old: dict, new: dict) -> str:
    a = json.dumps(old, indent=2).splitlines()
    b = json.dumps(new, indent=2).splitlines()
    return "\n".join(difflib.unified_diff(a, b, f"v{old['version']}", f"v{new['version']}", lineterm=""))
