"""A small in-memory clinic backend used for tests and scenario runs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

NOW = datetime(2026, 10, 7, 13, 0)  # frozen clock so relative dates stay reproducible in tests
TIMES = ["09:00", "09:30", "10:00", "11:00", "14:00", "15:00", "16:00"]
OTHER_PATIENT_ID = 99  # a second patient used for pre-booked slots and race-condition injections

SCHEMA = """
CREATE TABLE patients(id INTEGER PRIMARY KEY, name TEXT, dob TEXT);
CREATE TABLE providers(id INTEGER PRIMARY KEY, name TEXT, specialty TEXT);
CREATE TABLE slots(id INTEGER PRIMARY KEY, provider_id INTEGER, start TEXT, UNIQUE(provider_id, start));
CREATE TABLE appointments(id INTEGER PRIMARY KEY, patient_id INTEGER, slot_id INTEGER, status TEXT, reason TEXT);
-- The database enforces the rule so one slot cannot have two active bookings at the same time.
CREATE UNIQUE INDEX one_active_per_slot ON appointments(slot_id) WHERE status = 'active';
CREATE TABLE waitlist(id INTEGER PRIMARY KEY, patient_id INTEGER, specialty TEXT);
CREATE TABLE handoffs(id INTEGER PRIMARY KEY, turn INTEGER, reason TEXT, urgency TEXT);
CREATE TABLE audit(id INTEGER PRIMARY KEY, turn INTEGER, tool TEXT, args TEXT, result TEXT, seen TEXT,
                   ok INTEGER, committed INTEGER, is_write INTEGER, patient INTEGER);
"""

PATIENTS = [
    (1, "Asha Verma", "1988-03-14"),
    (2, "Rohan Verma", "1985-07-22"),
    (3, "Meera Nair", "1992-11-02"),
    (4, "John D'Souza", "1979-01-30"),
    (5, "Priya Shah", "2001-05-09"),
]
PROVIDERS = [(1, "Dr. Rao", "GP"), (2, "Dr. Mehta", "GP"), (3, "Dr. Iyer", "Dermatology")]
# (patient, provider, start) -> appointment ids 1..4 are stable and referenced by scenario tests
INITIAL_APPOINTMENTS = [
    (1, 1, "2026-10-12 10:00"),
    (2, 2, "2026-10-13 09:30"),
    (3, 1, "2026-10-08 09:00"),  # less than 24 hours away: cannot be changed online
    (4, 2, "2026-10-15 14:00"),
]


class World:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.hooks: dict[str, list] = {}  # tool name -> [fn(world, tool, args, result)]
        self.fail_plan: dict[str, dict] = {}  # tool name -> {"times": n} (commit succeeds, response is lost)
        self.flags: dict = {}
        self._seed()

    # -- helpers ------------------------------------------------------
    def qa(self, sql, *params):
        return self.db.execute(sql, params).fetchall()

    def q1(self, sql, *params):
        return self.db.execute(sql, params).fetchone()

    def run(self, sql, *params):
        cur = self.db.execute(sql, params)
        self.db.commit()
        return cur.lastrowid

    def slot_id(self, provider_id, start):
        return self.q1("SELECT id FROM slots WHERE provider_id=? AND start=?", provider_id, start)["id"]

    # -- seed ---------------------------------------------------------
    def _seed(self):
        self.db.executemany("INSERT INTO patients VALUES(?,?,?)", PATIENTS)
        self.db.executemany("INSERT INTO providers VALUES(?,?,?)", PROVIDERS)
        d = NOW.date() + timedelta(days=1)
        end = datetime(2026, 10, 30).date()
        while d <= end:
            if d.weekday() < 5:
                for pid in (1, 2):  # Dr. Iyer (Dermatology) deliberately has no availability
                    for t in TIMES:
                        self.db.execute("INSERT INTO slots(provider_id,start) VALUES(?,?)", (pid, f"{d} {t}"))
            d += timedelta(days=1)
        for patient_id, provider_id, start in INITIAL_APPOINTMENTS:
            self.db.execute(
                "INSERT INTO appointments(patient_id,slot_id,status,reason) VALUES(?,?, 'active','')",
                (patient_id, self.slot_id(provider_id, start)),
            )
        for day in ("2026-10-12", "2026-10-13"):  # Dr. Rao is busy early on Monday and Tuesday
            for time in ("09:00", "09:30", "10:00"):
                slot_id = self.slot_id(1, f"{day} {time}")
                if not self.q1("SELECT 1 FROM appointments WHERE slot_id=? AND status='active'", slot_id):
                    self.db.execute(
                        "INSERT INTO appointments(patient_id,slot_id,status,reason) VALUES(?,?, 'active','busy')",
                        (OTHER_PATIENT_ID, slot_id),
                    )
        self.db.commit()

    # -- audit + snapshot ----------------------------------------------
    def log(self, turn, tool, args, real, seen, patient, is_write):
        self.run(
            "INSERT INTO audit(turn,tool,args,result,seen,ok,committed,is_write,patient) VALUES(?,?,?,?,?,?,?,?,?)",
            turn, tool, json.dumps(args, default=str), json.dumps(real, default=str),
            json.dumps(seen, default=str), int(bool(real.get("ok"))), int(bool(real.get("committed"))),
            int(is_write), patient,
        )

    def snapshot(self):
        appts = [dict(r) for r in self.qa(
            "SELECT a.id, a.patient_id, a.status, s.id slot_id, s.start, p.name provider, p.specialty "
            "FROM appointments a JOIN slots s ON s.id=a.slot_id JOIN providers p ON p.id=s.provider_id ORDER BY a.id")]
        audit = []
        for r in self.qa("SELECT * FROM audit ORDER BY id"):
            d = dict(r)
            for k in ("args", "result", "seen"):
                d[k] = json.loads(d[k])
            audit.append(d)
        return {
            "appointments": appts,
            "handoffs": [dict(r) for r in self.qa("SELECT * FROM handoffs ORDER BY id")],
            "waitlist": [dict(r) for r in self.qa("SELECT * FROM waitlist ORDER BY id")],
            "audit": audit,
            "flags": dict(self.flags),
        }


# -- chaos / race hooks, referenced by name from scenarios ---------------------
def steal_first_slot(world, tool, args, result):
    """Simulate a race condition by booking the first available slot immediately after a search."""
    if result.get("slots") and "stolen" not in world.flags:
        slot_id = result["slots"][0]["slot_id"]
        world.run("INSERT INTO appointments(patient_id,slot_id,status,reason) VALUES(?,?, 'active','race')",
                  OTHER_PATIENT_ID, slot_id)
        world.flags["stolen"] = slot_id


HOOKS = {"steal_first_slot": ("search_slots", steal_first_slot)}
