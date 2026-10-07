"""Soft judge (clarity / empathy / professionalism). Never gates a patch; deterministic graders do that.

  python -m evals.judge export <run-dir-tag> [n]   -> runs/label_sheet.json (fill in the "human" scores by hand)
  python -m evals.judge calibrate runs/label_sheet.json   -> judge-vs-human agreement
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

RUBRIC = ("Score the ASSISTANT turns of this clinic scheduling chat from 1 to 5 on: clarity (clear, concise, one question at "
          "a time), empathy (acknowledges the patient's situation proportionally), professionalism (no jargon, no "
          "over-promising, routes medical questions to clinicians). You see only the transcript, not the system state. "
          'Return JSON: {"clarity": int, "empathy": int, "professionalism": int, "notes": str}.')
DIMS = ("clarity", "empathy", "professionalism")
ROOT = Path(__file__).resolve().parent.parent / "runs"


def render(run: dict) -> str:
    return "\n".join(f"{'PATIENT' if t['role'] == 'patient' else 'ASSISTANT'}: {t['text']}" for t in run["transcript"])


def judge_run(llm, run: dict) -> dict:
    try:
        return llm.chat_json(RUBRIC, render(run), model=os.getenv("JUDGE_MODEL"))
    except Exception as e:  # noqa: BLE001  (a judge failure must never break a run)
        return {"error": str(e)}


def export_sample(tag: str, n: int = 6) -> Path:
    files = sorted((ROOT / tag).glob("*.json"))
    random.Random(0).shuffle(files)
    sheet = []
    for f in files[:n]:
        run = json.loads(f.read_text())
        sheet.append({"file": f.name, "transcript": render(run), "judge": run.get("judge"),
                      "human": {d: None for d in DIMS}})
    out = ROOT / "label_sheet.json"
    out.write_text(json.dumps(sheet, indent=1))
    return out


def calibrate(sheet_path: str) -> dict:
    rows = [r for r in json.loads(Path(sheet_path).read_text())
            if r.get("judge") and "error" not in r["judge"] and all(r["human"].get(d) for d in DIMS)]
    if not rows:
        return {"error": "no fully labelled rows with judge scores"}
    diffs = [abs(r["judge"][d] - r["human"][d]) for r in rows for d in DIMS]
    return {"n_transcripts": len(rows), "mean_abs_diff": sum(diffs) / len(diffs),
            "within_1_point": sum(x <= 1 for x in diffs) / len(diffs)}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "export":
        print(export_sample(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 6))
    elif len(sys.argv) == 3 and sys.argv[1] == "calibrate":
        print(json.dumps(calibrate(sys.argv[2]), indent=2))
    else:
        print(__doc__)
