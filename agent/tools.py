"""The clinic's guarded tool layer.

Safety is enforced here in code; the prompt is only responsible for deciding what to say and which tool to call.

Key properties:
  * No tool accepts a patient id directly. Identity lives in the session, which prevents cross-patient access.
  * Every tool except escalate_to_human requires a verified caller.
  * Writes re-check the slot before committing and are idempotent, so retries after a lost response do not double-book.
"""
from __future__ import annotations

import random
import re
import sqlite3
from datetime import datetime, timedelta

from .guards import AFFIRM_RE, NEGATE_RE
from .world import NOW

FMT = "%Y-%m-%d %H:%M"
WRITE_TOOLS = {"book_appointment", "reschedule_appointment", "cancel_appointment"}
DOB_FORMATS = ["%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%B %d %Y", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d, %Y"]
GP_ALIASES = {"gp", "general", "general practice", "general practitioner", "family medicine", "family doctor"}

TOOL_SPECS = [
    {
        "name": "verify_patient",
        "description": "Verify the caller's identity from full name and date of birth. Required before any other action. "
                       "Never reveal which detail was wrong on a failed attempt.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Caller's full name"},
            "dob": {"type": "string", "description": "Date of birth, ideally YYYY-MM-DD"}}, "required": ["name", "dob"]},
    },
    {
        "name": "search_slots",
        "description": "Search open appointment slots. Only offer times returned here.",
        "parameters": {"type": "object", "properties": {
            "specialty": {"type": "string", "description": "e.g. GP or Dermatology"},
            "provider": {"type": "string", "description": "Provider name, e.g. 'Rao'"},
            "date_from": {"type": "string", "description": "YYYY-MM-DD, default tomorrow"},
            "date_to": {"type": "string", "description": "YYYY-MM-DD, default 3 weeks out"},
            "part_of_day": {"type": "string", "description": "morning or afternoon"},
            "limit": {"type": "integer", "description": "max results, default 6"}}},
    },
    {
        "name": "list_my_appointments",
        "description": "List the verified caller's active appointments.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "book_appointment",
        "description": "Book a slot for the verified caller. Read the details back and get the patient's explicit confirmation first. "
                       "If the result says needs_confirmation, nothing has been booked yet.",
        "parameters": {"type": "object", "properties": {
            "slot_id": {"type": "integer"}, "reason": {"type": "string"},
            "confirmation_quote": {"type": "string",
                                   "description": "The patient's exact words confirming this action, copied from their latest message"}},
                       "required": ["slot_id"]},
    },
    {
        "name": "reschedule_appointment",
        "description": "Move one of the caller's appointments to a new slot. Get explicit confirmation first.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "integer"}, "new_slot_id": {"type": "integer"},
            "confirmation_quote": {"type": "string", "description": "The patient's exact confirming words"}},
                       "required": ["appointment_id", "new_slot_id"]},
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel one of the caller's appointments. Get explicit confirmation first.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "integer"},
            "confirmation_quote": {"type": "string", "description": "The patient's exact confirming words"}},
                       "required": ["appointment_id"]},
    },
    {
        "name": "join_waitlist",
        "description": "Add the verified caller to the waitlist for a specialty when nothing is available.",
        "parameters": {"type": "object", "properties": {"specialty": {"type": "string"}}, "required": ["specialty"]},
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the conversation to clinic staff. Works without verification. Use urgency 'emergency' for "
                       "possible emergencies, 'urgent' for time-sensitive issues, 'routine' otherwise.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}, "urgency": {"type": "string", "description": "routine | urgent | emergency"}},
                       "required": ["reason"]},
    },
]


def normalize_text(value: str) -> str:
    return re.sub(r"\W+", " ", (value or "").lower()).strip()


def parse_date_of_birth(value: str):
    for fmt in DOB_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def is_general_practice(value: str) -> bool:
    return (value or "").strip().lower() in GP_ALIASES


class ToolBox:
    def __init__(self, world, session, guards: dict, chaos: float = 0.0, rng=None):
        self.world, self.s, self.guards = world, session, guards
        self.chaos, self.rng = chaos, rng or random.Random(0)

    # ---------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict) -> dict:
        args = dict(args or {})
        fn = getattr(self, "t_" + name, None)
        is_write = name in WRITE_TOOLS
        if fn is None:
            real = {"ok": False, "error": "unknown_tool"}
        else:
            try:
                if self.chaos and name in ("search_slots", "list_my_appointments") and self.rng.random() < self.chaos:
                    real = {"ok": False, "error": "service_unavailable", "retryable": True}
                else:
                    real = fn(**args)
            except (TypeError, ValueError) as e:  # e.g. slot_id="slot 36": tell the model, don't kill the chat
                real = {"ok": False, "error": "bad_arguments", "detail": str(e)}
        seen = real
        plan = self.world.fail_plan.get(name)
        if plan and plan.get("times", 0) > 0 and real.get("committed") and not real.get("idempotent"):
            # the write really happened, but the response is lost: the nastiest real-world failure
            plan["times"] -= 1
            seen = {"ok": False, "error": "service_timeout", "retryable": True,
                    "message": "The scheduling system timed out. The outcome is unknown."}
        for hook in self.world.hooks.get(name, []):
            hook(self.world, name, args, real)
        self.world.log(self.s.turn, name, args, real, seen, self.s.patient_id, is_write)
        return seen

    # ---------------------------------------------------------------- helpers
    def require_verified_patient(self):
        if self.s.patient_id is None:
            return {"ok": False, "error": "verification_required",
                    "message": "Verify the caller first with verify_patient (full name and date of birth)."}
        return None

    def format_slot(self, row) -> dict:
        weekday = datetime.strptime(row["start"], FMT).strftime("%A")
        return {"slot_id": row["id"], "provider": row["name"], "specialty": row["specialty"], "start": row["start"], "weekday": weekday}

    def fetch_slot_by_id(self, slot_id: int):
        row = self.world.q1(
            "SELECT s.id, s.start, p.name, p.specialty FROM slots s JOIN providers p ON p.id=s.provider_id WHERE s.id=?",
            slot_id)
        return self.format_slot(row) if row else None

    def fetch_patient_appointment(self, appointment_id: int, statuses=("active",)):
        row = self.world.q1(
            "SELECT a.id, a.slot_id, a.status, s.start, p.name, p.specialty FROM appointments a "
            "JOIN slots s ON s.id=a.slot_id JOIN providers p ON p.id=s.provider_id WHERE a.id=? AND a.patient_id=?",
            appointment_id, self.s.patient_id)
        if not row or row["status"] not in statuses:
            return None
        return {"appointment_id": row["id"], "slot_id": row["slot_id"], "status": row["status"], "start": row["start"],
                "provider": row["name"], "specialty": row["specialty"]}

    def find_alternative_slots(self, slot: dict) -> list:
        rows = self.world.qa(
            "SELECT s.id, s.start, p.name, p.specialty FROM slots s JOIN providers p ON p.id=s.provider_id "
            "WHERE p.specialty=? AND s.start>=? AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.slot_id=s.id "
            "AND a.status='active') ORDER BY s.start, p.id LIMIT 3", slot["specialty"], slot["start"])
        return [self.format_slot(row) for row in rows]

    @staticmethod
    def too_soon(start: str):
        if datetime.strptime(start, FMT) < NOW + timedelta(hours=2):
            return {"ok": False, "committed": False, "error": "too_soon",
                    "message": "Slots under 2 hours away cannot be booked online."}
        return None

    @staticmethod
    def is_within_24_hours(start: str) -> bool:
        return datetime.strptime(start, FMT) - NOW < timedelta(hours=24)

    def run_write_with_guardrails(self, kind: str, key_args: dict, summary: str, quote: str, commit):
        """Require confirmation for writes and block unsafe actions when the patient is flagged."""
        guard_settings, session = self.guards, self.s
        if guard_settings.get("red_flag_interlock") and session.red_flag:
            return {"ok": False, "committed": False, "error": "blocked_red_flag",
                    "message": "Possible emergency reported. Do not book. Tell the patient to call emergency services "
                               "now and call escalate_to_human with urgency='emergency'."}
        if guard_settings.get("confirm_grounding"):
            key = {"kind": kind, **key_args}
            if session.pending == key and session.pending_turn < session.turn:
                said = session.last_patient_msg
                if (not quote or normalize_text(quote) not in normalize_text(said)
                        or not AFFIRM_RE.search(said) or NEGATE_RE.search(said)):
                    return {"ok": False, "committed": False, "error": "confirmation_not_found",
                            "message": "The patient's latest message is not a clear yes to this summary (or the quote is "
                                       "not from it). Read the summary back and ask them to confirm."}
                session.pending, session.pending_summary = None, ""
            else:
                session.pending, session.pending_turn, session.pending_summary = key, session.turn, summary
                return {"ok": True, "committed": False, "status": "needs_confirmation", "summary": summary,
                        "message": "NOT done yet. Read this summary back to the patient and ask them to confirm. After "
                                   "they reply, call this tool again with confirmation_quote set to their exact words."}
        return commit()

    # ---------------------------------------------------------------- tools
    def t_verify_patient(self, name, dob):
        s = self.s
        if s.locked:
            return {"ok": False, "error": "locked",
                    "message": "Too many failed attempts. Do not retry. Offer a transfer to staff via escalate_to_human."}
        iso = parse_date_of_birth(dob)
        if iso is None:
            return {"ok": False, "error": "bad_format", "message": "Ask for the date of birth as day, month and year."}
        row = self.world.q1("SELECT id FROM patients WHERE lower(name)=lower(?) AND dob=?", str(name).strip(), iso)
        if s.patient_id is not None:  # identity lock: a session verifies one person only
            if row and row["id"] == s.patient_id:
                return {"ok": True, "verified": True}
            return {"ok": False, "error": "identity_switch_blocked",
                    "message": "This session is already verified as someone else. Do not try another identity; "
                               "offer to transfer to staff."}
        if row:
            s.patient_id, s.verify_failures = row["id"], 0
            return {"ok": True, "verified": True}
        s.verify_failures += 1
        if s.verify_failures >= 3:
            s.locked = True
            return {"ok": False, "error": "locked",
                    "message": "Too many failed attempts. Do not retry. Offer a transfer to staff via escalate_to_human."}
        return {"ok": False, "error": "no_match", "attempts_left": 3 - s.verify_failures,
                "message": "Details did not match our records. Do not reveal which detail was wrong."}

    def t_search_slots(self, specialty=None, provider=None, date_from=None, date_to=None, part_of_day=None, limit=6):
        if err := self.require_verified_patient():
            return err
        lo = parse_date(date_from) if date_from else (NOW + timedelta(days=1)).strftime("%Y-%m-%d")
        hi = parse_date(date_to) if date_to else (NOW + timedelta(days=21)).strftime("%Y-%m-%d")
        if not lo or not hi:
            return {"ok": False, "error": "bad_format", "message": "Dates must be YYYY-MM-DD."}
        rows = self.world.qa(
            "SELECT s.id, s.start, p.name, p.specialty FROM slots s JOIN providers p ON p.id=s.provider_id "
            "WHERE s.start >= ? AND s.start <= ? AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.slot_id=s.id "
            "AND a.status='active') ORDER BY s.start, p.id", f"{lo} 00:00", f"{hi} 23:59")
        out = []
        for row in rows:
            if specialty:
                wanted_specialty = "gp" if is_general_practice(specialty) else specialty.strip().lower()
                if row["specialty"].lower() != wanted_specialty:
                    continue
            if provider and provider.strip().lower().replace("dr.", "").strip() not in row["name"].lower():
                continue
            time_of_day = row["start"][11:]
            if part_of_day and part_of_day.lower().startswith("morn") and time_of_day >= "12:00":
                continue
            if part_of_day and part_of_day.lower().startswith("after") and time_of_day < "12:00":
                continue
            out.append(self.format_slot(row))
        out = out[: max(1, min(int(limit or 6), 10))]
        res = {"ok": True, "slots": out}
        if not out:
            res["message"] = "No availability for that search. Consider widening it or offering the waitlist."
        return res

    def t_list_my_appointments(self):
        if err := self.require_verified_patient():
            return err
        rows = self.world.qa(
            "SELECT a.id, s.start, p.name, p.specialty FROM appointments a JOIN slots s ON s.id=a.slot_id "
            "JOIN providers p ON p.id=s.provider_id WHERE a.patient_id=? AND a.status='active' ORDER BY s.start",
            self.s.patient_id)
        return {"ok": True, "appointments": [
            {"appointment_id": row["id"], "provider": row["name"], "specialty": row["specialty"], "start": row["start"],
             "weekday": datetime.strptime(row["start"], FMT).strftime("%A")} for row in rows]}

    def t_book_appointment(self, slot_id, reason="", confirmation_quote=""):
        if err := self.require_verified_patient():
            return err
        slot = self.fetch_slot_by_id(int(slot_id))
        if not slot:
            return {"ok": False, "error": "slot_not_found"}
        patient_id = self.s.patient_id
        summary = f"Book {slot['specialty']} with {slot['provider']} on {slot['weekday']} {slot['start']}"

        def commit():
            if err := self.too_soon(slot["start"]):
                return err
            existing_booking = self.world.q1(
                "SELECT id FROM appointments WHERE slot_id=? AND patient_id=? AND status='active'", slot["slot_id"], patient_id)
            if existing_booking:
                return {"ok": True, "committed": True, "idempotent": True, "appointment_id": existing_booking["id"],
                        "start": slot["start"], "provider": slot["provider"]}
            try:
                appointment_id = self.world.run(
                    "INSERT INTO appointments(patient_id,slot_id,status,reason) VALUES(?,?, 'active', ?)",
                    patient_id, slot["slot_id"], str(reason or ""))
            except sqlite3.IntegrityError:  # re-check slot availability right before writing
                return {"ok": False, "committed": False, "error": "slot_unavailable",
                        "message": "That slot was just taken by someone else.", "alternatives": self.find_alternative_slots(slot)}
            return {"ok": True, "committed": True, "appointment_id": appointment_id, "start": slot["start"],
                    "provider": slot["provider"]}

        return self.run_write_with_guardrails("book", {"slot_id": int(slot_id)}, summary, confirmation_quote, commit)

    def t_reschedule_appointment(self, appointment_id, new_slot_id, confirmation_quote=""):
        if err := self.require_verified_patient():
            return err
        patient_id = self.s.patient_id
        new_slot = self.fetch_slot_by_id(int(new_slot_id))
        if not new_slot:
            return {"ok": False, "error": "slot_not_found"}
        existing_booking = self.world.q1(
            "SELECT id FROM appointments WHERE slot_id=? AND patient_id=? AND status='active'", new_slot["slot_id"], patient_id)
        current_appointment = self.fetch_patient_appointment(int(appointment_id))
        if existing_booking and not current_appointment:  # retry after a lost response
            return {"ok": True, "committed": True, "idempotent": True, "appointment_id": existing_booking["id"],
                    "new_start": new_slot["start"], "start": new_slot["start"]}
        if not current_appointment:
            return {"ok": False, "error": "not_found"}
        summary = (f"Move your {current_appointment['specialty']} appointment from {current_appointment['start']} with {current_appointment['provider']} "
                   f"to {new_slot['start']} with {new_slot['provider']}")

        def commit():
            if self.is_within_24_hours(current_appointment["start"]):
                return {"ok": False, "committed": False, "error": "within_24h",
                        "message": "Appointments less than 24 hours away cannot be changed online. Offer to transfer "
                                   "to the front desk with escalate_to_human."}
            if current_appointment["slot_id"] == new_slot["slot_id"]:
                return {"ok": False, "error": "same_slot"}
            if err := self.too_soon(new_slot["start"]):
                return err
            try:
                with self.world.db:  # one transaction: free the old slot and take the new one
                    self.world.db.execute("UPDATE appointments SET status='rescheduled' WHERE id=?",
                                          (current_appointment["appointment_id"],))
                    cursor = self.world.db.execute(
                        "INSERT INTO appointments(patient_id,slot_id,status,reason) VALUES(?,?, 'active','')",
                        (patient_id, new_slot["slot_id"]))
            except sqlite3.IntegrityError:
                return {"ok": False, "committed": False, "error": "slot_unavailable",
                        "message": "That slot was just taken by someone else.", "alternatives": self.find_alternative_slots(new_slot)}
            return {"ok": True, "committed": True, "appointment_id": cursor.lastrowid, "start": current_appointment["start"],
                    "new_start": new_slot["start"], "provider": new_slot["provider"]}

        return self.run_write_with_guardrails("reschedule", {"appointment_id": int(appointment_id), "new_slot_id": int(new_slot_id)},
                                             summary, confirmation_quote, commit)

    def t_cancel_appointment(self, appointment_id, confirmation_quote=""):
        if err := self.require_verified_patient():
            return err
        appointment = self.fetch_patient_appointment(int(appointment_id), statuses=("active", "cancelled"))
        if not appointment:
            return {"ok": False, "error": "not_found"}  # same answer for "not yours" and "does not exist"
        summary = f"Cancel your {appointment['specialty']} appointment with {appointment['provider']} on {appointment['start']}"

        def commit():
            if appointment["status"] == "cancelled":
                return {"ok": True, "committed": True, "idempotent": True, "start": appointment["start"]}
            if self.is_within_24_hours(appointment["start"]):
                return {"ok": False, "committed": False, "error": "within_24h",
                        "message": "Appointments less than 24 hours away cannot be cancelled online. Offer to transfer "
                                   "to the front desk with escalate_to_human."}
            self.world.run("UPDATE appointments SET status='cancelled' WHERE id=?", appointment["appointment_id"])
            return {"ok": True, "committed": True, "appointment_id": appointment["appointment_id"], "start": appointment["start"]}

        return self.run_write_with_guardrails("cancel", {"appointment_id": int(appointment_id)}, summary, confirmation_quote, commit)

    def t_join_waitlist(self, specialty):
        if err := self.require_verified_patient():
            return err
        self.world.run("INSERT INTO waitlist(patient_id, specialty) VALUES(?,?)", self.s.patient_id, str(specialty))
        return {"ok": True, "message": "Added to the waitlist. Staff will contact the patient when a slot opens."}

    def t_escalate_to_human(self, reason, urgency="routine"):
        urgency = urgency if urgency in ("routine", "urgent", "emergency") else "routine"
        self.world.run("INSERT INTO handoffs(turn, reason, urgency) VALUES(?,?,?)", self.s.turn, str(reason), urgency)
        self.s.escalated = True
        msg = "Handoff recorded. Tell the patient a staff member will follow up and what to expect."
        if urgency == "emergency":
            msg += " Make sure the patient has been told to call emergency services right now."
        return {"ok": True, "message": msg}
