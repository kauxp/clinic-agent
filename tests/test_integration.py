"""End-to-end through the real runner with a deterministic fake LLM: agent loop + reactive patient + graders."""
from agent.llm import LLMResponse
from agent.policy import load
from evals.runner import run_one
from evals.scenarios import BY_ID


class FakeAgentLLM:
    """A tiny scripted 'model' that books the first morning slot next week, with a proper read-back."""

    def __init__(self, liar=False):
        self.liar = liar

    def chat(self, system, messages, tools=None, model=None, temperature=0.2):
        last = messages[-1]
        n_user = sum(1 for m in messages if m["role"] == "user")
        if last["role"] == "tool":
            res = last["result"]
            if last["name"] == "verify_patient":
                return LLMResponse(tool_calls=[{"name": "search_slots", "args": {
                    "specialty": "GP", "date_from": "2026-10-12", "date_to": "2026-10-16", "part_of_day": "morning", "limit": 3}}])
            if last["name"] == "search_slots":
                s = res["slots"][0]
                return LLMResponse(text=f"I can offer {s['weekday']} {s['start'][:10]} at {s['start'][11:]} with {s['provider']}. Does that work?")
            if last["name"] == "book_appointment":
                return LLMResponse(text="You're booked for the slot we discussed.")
        if n_user == 1:
            return LLMResponse(text="Happy to help. Could I have your full name and date of birth to verify you?")
        if n_user == 2:
            return LLMResponse(tool_calls=[{"name": "verify_patient", "args": {"name": "Priya Shah", "dob": "9 May 2001"}}])
        slot = next(m["result"]["slots"][0] for m in messages if m["role"] == "tool" and m["name"] == "search_slots")
        if n_user == 3:
            if self.liar:
                return LLMResponse(text="Done, you're booked!")  # claims success, never calls a tool
            return LLMResponse(text=f"Shall I book {slot['weekday']} {slot['start'][:10]} at {slot['start'][11:]} with {slot['provider']}? Please confirm.")
        return LLMResponse(tool_calls=[{"name": "book_appointment", "args": {"slot_id": slot["slot_id"]}}])


def test_happy_path_passes_end_to_end():
    run = run_one(BY_ID["book_basic"], load(0), FakeAgentLLM())
    failed = [(c["name"], c["detail"]) for c in run["checks"] if not c["passed"]]
    assert run["passed"], failed
    assert any(a["tool"] == "book_appointment" and a["committed"] for a in run["audit"])


def test_liar_agent_is_caught_by_state_not_transcript():
    run = run_one(BY_ID["book_basic"], load(0), FakeAgentLLM(liar=True))
    failed = {c["name"] for c in run["checks"] if not c["passed"]}
    assert not run["passed"]
    assert "no_false_success_claim" in failed and any(n.startswith("appt_exists") for n in failed)


class BlockedLLM:
    """The provider's safety filter refuses every reply."""

    def chat(self, *a, **k):
        return LLMResponse(blocked=True)


def test_blocked_reply_really_hands_off():
    run = run_one(BY_ID["emergency_midflow"], load(0), BlockedLLM())
    assert run["handoffs"], "the fallback promises a handoff, so one must be recorded"


def test_simulated_patient_does_not_resend_identity_after_success():
    from evals.simulator import ReactivePatient
    p = ReactivePatient({"opening": "hi", "name": "Priya Shah", "dob": "9 May 2001", "on_offer": "The first one works."})
    assert "Priya" in p.next("Could I have your full name and date of birth?", 2)[0]
    assert p.next("Thanks for verifying, Priya! I have Monday at 9:00 AM.", 3)[0] == "The first one works."
    assert "Priya" in p.next("Sorry, those details didn't match. Your date of birth again?", 4)[0]
