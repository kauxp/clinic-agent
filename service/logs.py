"""JSON logs on stdout (Kubernetes collects stdout). Patient messages are never logged: they are health data."""
from __future__ import annotations

import json
import logging
import time

from opentelemetry import trace

FIELDS = ("request_id", "session_id", "route", "method", "status", "duration_ms", "event", "tool", "outcome",
          "version", "policy", "provider", "error")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for f in FIELDS:
            if hasattr(record, f):
                out[f] = getattr(record, f)
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:  # lets you jump from a log line to its trace
            out["trace_id"] = format(ctx.trace_id, "032x")
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our own request log line
