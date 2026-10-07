"""Thin provider adapter. The rest of the codebase only sees LLM.chat / LLM.chat_json.

Swapping providers means writing one more class with the same two methods. Model names come from env vars.
Neutral message format:
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "tool_calls": [{"name": str, "args": dict}]}
  {"role": "tool", "name": str, "result": dict}
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


class InfraError(Exception):
    """Provider/network failure after retries. Logged and retried on resume, never scored as an agent failure."""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    blocked: bool = False


_last_call = 0.0


def _throttle() -> None:
    """Client-side rate limit so a free tier does not turn into a wall of 429s."""
    global _last_call
    gap = 60.0 / float(os.getenv("LLM_RPM", "10"))
    wait = _last_call + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _retryable(e: Exception) -> bool:
    import httpx

    if isinstance(e, httpx.TransportError):  # DNS failure, dropped connection, read timeout
        return True
    code = getattr(e, "code", None)
    s = str(e)
    return code in (408, 429, 500, 502, 503, 504) or any(
        t in s for t in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED")
    ) or "timed out" in s.lower()


def _to_contents(messages: list):
    from google.genai import types

    contents = []
    for m in messages:
        if m["role"] == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
        elif m["role"] == "assistant":
            parts = []
            if m.get("text"):
                parts.append(types.Part(text=m["text"]))
            for tc in m.get("tool_calls") or []:
                # Gemini 3 rejects a replayed function call unless its thought signature is echoed back
                parts.append(types.Part(function_call=types.FunctionCall(name=tc["name"], args=tc["args"]),
                                        thought_signature=tc.get("thought_signature")))
            if parts:
                contents.append(types.Content(role="model", parts=parts))
        elif m["role"] == "tool":
            part = types.Part(function_response=types.FunctionResponse(name=m["name"], response=m["result"]))
            # parallel tool results must share one Content
            if contents and contents[-1].role == "user" and contents[-1].parts[0].function_response is not None:
                contents[-1].parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return contents


def _parse(resp) -> LLMResponse:
    out = LLMResponse()
    cands = getattr(resp, "candidates", None) or []
    if not cands or not cands[0].content or not cands[0].content.parts:
        out.blocked = True
        return out
    for p in cands[0].content.parts:
        if getattr(p, "text", None):
            out.text += p.text
        fc = getattr(p, "function_call", None)
        if fc:
            tc = {"name": fc.name, "args": dict(fc.args or {})}
            if getattr(p, "thought_signature", None):
                tc["thought_signature"] = p.thought_signature
            out.tool_calls.append(tc)
    return out


class GeminiLLM:
    def __init__(self):
        from google import genai

        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def _call(self, model, contents, config):
        delay = 2.0
        for attempt in range(6):
            _throttle()
            try:
                return self.client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as e:  # noqa: BLE001
                if not _retryable(e):
                    raise  # our bug (bad request etc.): fail loudly
                if attempt == 5:
                    raise InfraError(f"{type(e).__name__}: {e}") from e
                time.sleep(delay + random.random())
                delay = min(delay * 2, 60)

    def chat(self, system, messages, tools=None, model=None, temperature=0.2) -> LLMResponse:
        from google.genai import types

        decls = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
            for t in (tools or [])
        ]
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        model = model or os.getenv("AGENT_MODEL", "gemini-3.5-flash-lite")
        return _parse(self._call(model, _to_contents(messages), cfg))

    def chat_json(self, system, prompt, model=None, temperature=0.0) -> dict:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=temperature, response_mime_type="application/json"
        )
        model = model or os.getenv("DIAGNOSER_MODEL", "gemini-3.8-flash")
        resp = self._call(model, prompt, cfg)
        try:
            return json.loads(resp.text)
        except (TypeError, json.JSONDecodeError) as e:
            raise InfraError(f"unparseable JSON from model: {e}") from e


def get_llm():
    return GeminiLLM()
