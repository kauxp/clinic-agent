"""Talk to the agent in the terminal. Synthetic data only (try: Priya Shah, 9 May 2001)."""
import argparse

from .agent import Agent
from .llm import get_llm
from .policy import latest_version, load
from .world import World


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=int, default=None, help="policy version (default: latest)")
    ap.add_argument("--debug", action="store_true", help="print tool calls")
    args = ap.parse_args()
    policy = load(args.policy if args.policy is not None else latest_version())
    world, seen = World(), 0
    agent = Agent(world, policy, get_llm())
    print(f"Clinic scheduling assistant (policy v{policy['version']}, synthetic data). Ctrl-C to quit.")
    print("Test identities: Priya Shah 2001-05-09 | Asha Verma 1988-03-14 | John D'Souza 1979-01-30\n")
    try:
        while True:
            msg = input("you> ").strip()
            if not msg:
                continue
            reply = agent.respond(msg)
            if args.debug:
                for a in world.snapshot()["audit"][seen:]:
                    print(f"   [tool] {a['tool']}({a['args']}) -> {a['seen']}")
                seen = len(world.snapshot()["audit"])
            print(f"bot> {reply}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nbye")


if __name__ == "__main__":
    main()
