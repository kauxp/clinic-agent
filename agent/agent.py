"""The main patient-facing agent loop."""
from __future__ import annotations

import random

from .policy import render_prompt
from .session import Session
from .tools import TOOL_SPECS, ToolBox
from .world import NOW

FALLBACK = "I'm sorry, I'm having trouble completing this. I'm passing you to a member of our team who will follow up."


class Agent:
    def __init__(self, world, policy: dict, llm, chaos: float = 0.0, seed: int = 0, max_steps: int = 6):
        self.world = world
        self.policy = policy
        self.llm = llm
        self.max_steps = max_steps

        self.session = Session()
        self.tools = ToolBox(world, self.session, policy["guards"], chaos, random.Random(seed))
        self.messages: list = []
        self.transcript: list = []  # [{role, text, turn, writes_ok}] for graders

    def _writes_ok(self) -> int:
        return self.world.q1("SELECT COUNT(*) c FROM audit WHERE committed=1")["c"]

    def _record(self, role: str, text: str) -> None:
        self.transcript.append(
            {"role": role, "text": text, "turn": self.session.turn, "writes_ok": self._writes_ok()}
        )

    def respond(self, patient_text: str) -> str:
        session = self.session
        guard_rules = self.policy["guards"]

        session.on_patient_message(patient_text, guard_rules)
        self.messages.append({"role": "user", "text": patient_text})
        self._record("patient", patient_text)

        final_text = ""
        for _ in range(self.max_steps):
            system_prompt = render_prompt(self.policy, NOW) + "\n\n" + session.render(guard_rules)
            response = self.llm.chat(
                system_prompt,
                self.messages,
                TOOL_SPECS,
                temperature=self.policy["params"]["temperature"],
            )

            if response.blocked and not response.text and not response.tool_calls:
                # The provider's safety filter fires hardest on self-harm and emergencies: the handoff must be real.
                self.tools.call("escalate_to_human", {"reason": "model reply blocked by provider safety filter",
                                                      "urgency": "urgent"})
                final_text = FALLBACK
                self.messages.append({"role": "assistant", "text": final_text})
                break

            self.messages.append({"role": "assistant", "text": response.text, "tool_calls": response.tool_calls})

            if not response.tool_calls:
                final_text = response.text or "Sorry, could you say that again?"
                break

            for tool_call in response.tool_calls:
                tool_result = self.tools.call(tool_call["name"], tool_call["args"])
                self.messages.append({"role": "tool", "name": tool_call["name"], "result": tool_result})
        else:
            # If the model keeps looping, hand off instead of leaving the patient waiting.
            self.tools.call("escalate_to_human", {"reason": "agent exceeded step limit", "urgency": "routine"})
            final_text = FALLBACK

        self._record("assistant", final_text)
        return final_text

    def note_final_patient_message(self, text: str) -> None:
        """Closing line from the patient; recorded for the transcript but not answered."""
        self.transcript.append(
            {"role": "patient", "text": text, "turn": self.session.turn + 1, "writes_ok": self._writes_ok()}
        )
