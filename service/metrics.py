"""Prometheus metrics. Labels stay low-cardinality: no session ids, names or message text."""
from prometheus_client import Counter, Gauge, Histogram, Info

HTTP_REQUESTS = Counter("clinic_http_requests_total", "HTTP requests", ["route", "method", "status"])
HTTP_LATENCY = Histogram("clinic_http_request_duration_seconds", "HTTP request latency", ["route"],
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60))

LLM_CALLS = Counter("clinic_llm_calls_total", "Calls to the model", ["provider", "outcome"])
LLM_LATENCY = Histogram("clinic_llm_call_duration_seconds", "Latency of one model call", ["provider"],
                        buckets=(0.01, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30))

TOOL_CALLS = Counter("clinic_tool_calls_total", "Tool calls made by the agent", ["tool", "ok"])
WRITES_COMMITTED = Counter("clinic_writes_committed_total", "Bookings, reschedules and cancellations that committed",
                           ["tool"])
ESCALATIONS = Counter("clinic_escalations_total", "Handoffs to clinic staff", ["urgency"])

ACTIVE_SESSIONS = Gauge("clinic_active_sessions", "Chat sessions held in this pod")
BUILD = Info("clinic_build", "Running version")
