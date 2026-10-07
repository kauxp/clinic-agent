"""State that stays outside the chat transcript and is re-injected every turn.

This lets the model work from a trusted, machine-readable view of the conversation instead of
trying to remember verification status, pending actions, or safety flags on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .guards import is_red_flag


@dataclass
class Session:
    patient_id: Optional[int] = None
    verify_failures: int = 0
    locked: bool = False
    turn: int = 0
    last_patient_msg: str = ""
    pending: Optional[dict] = None
    pending_turn: int = -1
    pending_summary: str = ""
    red_flag: bool = False
    escalated: bool = False

    def on_patient_message(self, text: str, guards: dict) -> None:
        self.turn += 1
        self.last_patient_msg = text
        if guards.get("red_flag_interlock") and is_red_flag(text):
            self.red_flag = True

    def render(self, guards: dict) -> str:
        session_lines = [
            "[SESSION STATE - maintained by the system; trust it over your own memory]",
            f"verified: {'yes' if self.patient_id else 'no'}",
            f"identity_locked: {'yes' if self.locked else 'no'}",
        ]
        if guards.get("confirm_grounding"):
            session_lines.append(f"pending_confirmation: {self.pending_summary or 'none'}")
        if self.red_flag:
            session_lines.append(
                "RED FLAG: the patient reported possible emergency symptoms. Do not continue booking. Tell them to "
                "call emergency services now, then call escalate_to_human with urgency='emergency'. Give no medical advice."
            )
        return "\n".join(session_lines)
