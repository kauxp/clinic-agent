"""OpenTelemetry tracing. Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (the Helm chart sets it to Jaeger)."""
from __future__ import annotations

import os

from opentelemetry import trace

tracer = trace.get_tracer("clinic-agent")


def setup_tracing(app, version: str) -> bool:
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "clinic-agent", "service.version": version}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint comes from the env var
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    return True
