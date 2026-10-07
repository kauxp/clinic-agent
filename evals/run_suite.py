"""Run the suite once against a policy version and print the score table."""
import argparse

from agent.llm import get_llm
from agent.policy import latest_version, load

from .report import table
from .runner import run_suite
from .scenarios import BY_ID, DEV, HELDOUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=int, default=None, help="policy version (default: latest)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--heldout", action="store_true", help="run the held-out variants instead of the dev set")
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--judge", action="store_true", help="add soft LLM-judge scores (extra calls)")
    args = ap.parse_args()
    policy = load(args.policy if args.policy is not None else latest_version())
    scns = [BY_ID[i] for i in args.only.split(",") if i] or (HELDOUT if args.heldout else DEV)
    suite = run_suite(scns, policy, args.runs, get_llm(), judge=args.judge, label="_heldout" if args.heldout else "")
    print("\n" + table(suite, title=f"policy v{policy['version']}"))


if __name__ == "__main__":
    main()
