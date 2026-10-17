"""The HTTP service, offline: scripted model, no API key, no network."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from service.app import create_app
from service.config import Settings


def settings(**kw):
    base = dict(llm_provider="scripted", policy_version="latest", session_ttl_seconds=1800, max_sessions=10,
                app_version="test-sha", log_level="INFO")
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def client():
    return TestClient(create_app(settings()))


def book(client):
    sid = client.post("/v1/sessions").json()["session_id"]
    say = lambda t: client.post(f"/v1/sessions/{sid}/messages", json={"text": t})  # noqa: E731
    r1 = say("Hi, I need a GP appointment")
    r2 = say("I'm Priya Shah, born 9 May 2001")
    r3 = say("Yes, please book it")
    return sid, r1, r2, r3


def test_health_ready_version(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    v = client.get("/version").json()
    assert v["version"] == "test-sha" and v["llm_provider"] == "scripted"


def test_booking_journey_commits_a_write(client):
    _, r1, r2, r3 = book(client)
    assert "date of birth" in r1.json()["reply"]
    assert "Shall I book" in r2.json()["reply"]
    assert r3.status_code == 200 and r3.json()["writes_committed"] == 1
    assert "booked" in r3.json()["reply"]


def test_metrics_count_tools_and_writes(client):
    book(client)
    text = client.get("/metrics").text
    assert 'clinic_tool_calls_total{ok="true",tool="verify_patient"}' in text
    assert 'clinic_writes_committed_total{tool="book_appointment"}' in text
    assert 'clinic_build_info{policy="0",provider="scripted",version="test-sha"}' in text
    assert 'route="/v1/sessions/{session_id}/messages"' in text  # route template, not the raw id


def test_unknown_session_and_bad_input(client):
    assert client.post("/v1/sessions/nope/messages", json={"text": "hi"}).status_code == 404
    sid = client.post("/v1/sessions").json()["session_id"]
    assert client.post(f"/v1/sessions/{sid}/messages", json={"text": ""}).status_code == 422
    assert client.delete(f"/v1/sessions/{sid}").status_code == 204
    assert client.delete(f"/v1/sessions/{sid}").status_code == 404


def test_session_limit_returns_503():
    c = TestClient(create_app(settings(max_sessions=1)))
    assert c.post("/v1/sessions").status_code == 201
    r = c.post("/v1/sessions")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_missing_api_key_keeps_pod_unready(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    c = TestClient(create_app(settings(llm_provider="gemini")))
    assert c.get("/healthz").status_code == 200        # alive: don't restart it
    assert c.get("/readyz").status_code == 503         # not ready: don't send it traffic
    assert c.post("/v1/sessions").status_code == 503


def test_patient_text_never_reaches_the_logs(client, caplog):
    caplog.set_level(logging.INFO)
    book(client)
    from service.logs import JsonFormatter
    out = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    assert "Priya" not in out and "2001" not in out
    assert all(json.loads(line)["level"] for line in out.splitlines())
