"""Score tables. Safety/privacy are reported separately from UX because a clinic cares that they are 100% even if UX is 80%."""
from __future__ import annotations

from .runner import CATEGORIES, Suite


def _fmt(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def table(before: Suite, after: Suite | None = None, title: str = "") -> str:
    ids = sorted({r["scenario"] for r in before.runs} | ({r["scenario"] for r in after.runs} if after else set()))
    b, a = before.stats(), after.stats() if after else {}
    lines = [f"### {title}" if title else "", "", "| scenario | before | after |" if after else "| scenario | pass |",
             "|---|---|---|" if after else "|---|---|"]

    def cell(s, i):
        return f"{s[i]['passed']}/{s[i]['n']}" if i in s else "-"

    for i in ids:
        mark = ""
        if after and i in a and i in b:
            mark = " ⬆" if a[i]["passed"] > b[i]["passed"] else (" ⬇" if a[i]["passed"] < b[i]["passed"] else "")
        lines.append(f"| {i} | {cell(b, i)} | {cell(a, i)}{mark} |" if after else f"| {i} | {cell(b, i)} |")
    lines += ["", "| category (runs where all checks pass) | before | after |" if after else "| category | pass |",
              "|---|---|---|" if after else "|---|---|"]
    for c in CATEGORIES:
        lines.append(f"| {c} | {_fmt(before.category_rate(c))} | {_fmt(after.category_rate(c))} |" if after
                     else f"| {c} | {_fmt(before.category_rate(c))} |")
    lines.append(f"| **overall** | {_fmt(before.overall())} | {_fmt(after.overall())} |" if after
                 else f"| **overall** | {_fmt(before.overall())} |")
    if before.infra_errors or (after and after.infra_errors):
        lines.append(f"\ninfra errors excluded from scores: before={before.infra_errors}, after={after.infra_errors if after else 0}")
    return "\n".join(lines)
