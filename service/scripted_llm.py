"""A deterministic stand-in for the model, so the service runs without an API key.

Used by CI (end-to-end test in a throwaway Kubernetes cluster) and for demos. It walks one booking:
ask for identity -> verify -> search -> offer a slot -> book after a "yes". It goes through the same
tools and guards as the real model, so the database, audit log and metrics behave the same way.
"""
from __future__ import annotations

import re

from agent.guards import AFFIRM_RE
from agent.llm import LLMResponse

IDENTITY_RE = re.compile(r"(?:i'?m|i am|name is)\s+(?P<name>[a-z][\w'.-]*(?:\s+[a-z][\w'.-]*)+?)\s*,?\s*"
                         r"(?:born|dob|date of birth)\s*(?:on|is|:)?\s*(?P<dob>.+?)\.?$", re.I)


def _results(messages, tool):
    return [m["result"] for m in messages if m["role"] == "tool" and m["name"] == tool]


class ScriptedLLM:
    def chat(self, system, messages, tools=None, model=None, temperature=0.2) -> LLMResponse:
        last = messages[-1]
        if last["role"] == "tool":
            return self._after_tool(last["name"], last["result"])

        text = last["text"]
        if not any(r.get("verified") for r in _results(messages, "verify_patient")):
            m = IDENTITY_RE.search(text)
            if not m:
                return LLMResponse(text="Happy to help. Could I have your full name and date of birth?")
            return LLMResponse(tool_calls=[{"name": "verify_patient",
                                            "args": {"name": m["name"], "dob": m["dob"]}}])

        if any(r.get("committed") for r in _results(messages, "book_appointment")):
            return LLMResponse(text="You're all set. Is there anything else I can help with?")

        searches = [r for r in _results(messages, "search_slots") if r.get("slots")]
        if searches and AFFIRM_RE.search(text):
            slot = searches[-1]["slots"][0]
            return LLMResponse(tool_calls=[{"name": "book_appointment",
                                            "args": {"slot_id": slot["slot_id"], "confirmation_quote": text}}])
        return LLMResponse(tool_calls=[{"name": "search_slots", "args": {"specialty": "GP", "limit": 3}}])

    @staticmethod
    def _after_tool(name: str, res: dict) -> LLMResponse:
        if name == "verify_patient":
            if not res.get("verified"):
                return LLMResponse(text="Sorry, those details didn't match. Could you check your name and date of birth?")
            return LLMResponse(tool_calls=[{"name": "search_slots", "args": {"specialty": "GP", "limit": 3}}])
        if name == "search_slots":
            if not res.get("slots"):
                return LLMResponse(text="I couldn't find any open GP slots. Would you like me to add you to the waitlist?")
            s = res["slots"][0]
            return LLMResponse(text=f"I can offer {s['weekday']} {s['start'][:10]} at {s['start'][11:]} "
                                    f"with {s['provider']}. Shall I book it?")
        if name == "book_appointment":
            if res.get("status") == "needs_confirmation":
                return LLMResponse(text=f"Just to confirm: {res['summary']}. Shall I go ahead?")
            if res.get("committed"):
                return LLMResponse(text=f"You're booked for {res['start']} with {res['provider']}.")
            return LLMResponse(text="Sorry, I couldn't book that slot. Shall I look for another time?")
        return LLMResponse(text="How else can I help?")
