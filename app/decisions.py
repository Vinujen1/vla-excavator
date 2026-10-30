"""High-level decision providers for the autonomous supervisor.

* Cosmos3Provider: an NVIDIA Cosmos 3 reasoner served behind an OpenAI-compatible chat endpoint
  (`vllm serve nvidia/Cosmos3-Edge`, or the Cosmos 3 Reasoner NIM `nvidia/cosmos3-nano-reasoner`).
  LangChain (ChatOpenAI + PydanticOutputParser) handles the request and the structured output.
  It is reported as verified only after a real inference request has succeeded in this process.
* RulesProvider: deterministic keyword rules for development and simulation. Labelled as such;
  it is not Cosmos and not a language model.

Both only propose (intent, target id, next skill, explanation). validation.py decides.
"""
import base64
import json
import re
import threading
import time
from abc import ABC, abstractmethod

from pydantic import BaseModel

from .commands import parse_command
from .config import settings
from .schemas import GoalProposal, SkillDecision


class ProviderError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class DecisionProvider(ABC):
    id: str
    name: str
    kind: str

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def interpret(self, context: dict, image_jpeg: bytes | None) -> GoalProposal: ...

    @abstractmethod
    def next_skill(self, context: dict, image_jpeg: bytes | None) -> SkillDecision: ...


class RulesProvider(DecisionProvider):
    id, name, kind = "rules", "Rule-based development provider (deterministic, not Cosmos or an LLM)", "rules"

    def status(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "available": True, "verified": True,
                "message": "Deterministic keyword rules; follows the approved plan order"}

    def interpret(self, context, image_jpeg):
        cmd = parse_command(context["instruction"]["text"])
        target = (context.get("selected_target") or {}).get("id")
        if cmd.kind == "plan_task":
            return GoalProposal(intent="scoop_and_dump", target_id=target, cycles=cmd.cycles,
                                explanation="Rule-based: the instruction asks for scoop/dig/dump work"
                                            + (f" on the selected {context['selected_target']['label']}" if target else ""))
        if cmd.kind == "identify":
            return GoalProposal(intent="identify", explanation="Rule-based: the instruction asks to identify an object")
        return GoalProposal(intent="unsupported", explanation="Rule-based: no supported task keywords found")

    def next_skill(self, context, image_jpeg):
        expected = context["task"]["expected_next_skill"]
        return SkillDecision(next_skill=expected or "complete",
                             explanation="Rule-based: next skill in the approved plan order")


SYSTEM_PROMPT = """You are the high-level decision module of a small excavator working in a fenced sandbox.
A deterministic supervisor validates everything you say, and only it can command motion.
Rules:
- Never output motor commands, GPIO, code, speeds, distances or coordinates.
- Distances in the context were measured by a depth camera and calibration. Do not estimate your own.
- Choose targets only by the detection ids given in the context. If the right object is unclear, use intent "clarify".
- Supported goal: "scoop_and_dump" (stationary: dig in the approved dig region, dump in the approved dump region).
- During a task, propose the next skill from the allowed list, or "pause" if the image or state looks wrong.
Reply with one JSON object only, inside <answer></answer>."""


def _extract_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"<answer>(.*?)</answer>", text, flags=re.S)
    if m:
        text = m.group(1)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ProviderError("invalid_output", "reply contained no JSON object")
    return text[start:end + 1]


class Cosmos3Provider(DecisionProvider):
    id, kind = "cosmos3", "reasoning_model"

    def __init__(self, base_url=settings.cosmos_base_url, model=settings.cosmos_model,
                 api_key=settings.cosmos_api_key, timeout=settings.cosmos_timeout_s, chat=None):
        self.base_url, self.model, self._api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout
        self.name = f"Cosmos 3 reasoner ({model})"
        self._chat = chat                 # injectable for tests
        self._inflight = threading.Lock() # single flight: never overlap inference requests
        self.last_ok_at = None
        self.last_latency_ms = None
        self.last_error = None
        self.reported_model = None
        self.requests = 0

    @property
    def configured(self):
        return bool(self.base_url) or self._chat is not None

    def status(self):
        if not self.configured:
            msg = "Reasoning model not connected (VLA_COSMOS_BASE_URL unset)"
        elif self.last_ok_at is None:
            msg = "Endpoint configured; no successful inference yet in this session"
        else:
            msg = "Last inference succeeded"
        return {"id": self.id, "name": self.name, "kind": self.kind, "available": self.configured,
                "verified": self.last_ok_at is not None, "message": msg, "endpoint": self.base_url or None,
                "model": self.model, "reported_model": self.reported_model, "last_ok_at": self.last_ok_at,
                "last_latency_ms": self.last_latency_ms, "last_error": self.last_error,
                "busy": self._inflight.locked(), "requests": self.requests}

    def _client(self):
        if self._chat is None:
            from langchain_openai import ChatOpenAI
            extra = {}
            if settings.cosmos_enable_thinking in ("true", "false"):
                extra["chat_template_kwargs"] = {"enable_thinking": settings.cosmos_enable_thinking == "true"}
            self._chat = ChatOpenAI(model=self.model, base_url=self.base_url, api_key=self._api_key or "EMPTY",
                                    timeout=self.timeout, max_retries=0, temperature=0.2,
                                    max_tokens=settings.cosmos_max_tokens, extra_body=extra or None)
        return self._chat

    def _ask(self, schema: type[BaseModel], instruction: str, context: dict, image_jpeg: bytes | None):
        if not self.configured:
            raise ProviderError("not_connected", "Reasoning model not connected")
        if not self._inflight.acquire(blocking=False):
            raise ProviderError("busy", "A Cosmos request is already in flight")
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_core.output_parsers import PydanticOutputParser
        parser = PydanticOutputParser(pydantic_object=schema)
        content = [{"type": "text", "text": f"{instruction}\n\nContext (JSON):\n{json.dumps(context, default=str)}\n\n"
                                           f"{parser.get_format_instructions()}"}]
        if image_jpeg:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()}})
        started = time.perf_counter()
        self.requests += 1
        try:
            try:
                reply = self._client().invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(content=content)])
            except Exception as exc:
                # The failure type only: request details (and the key) stay out of logs.
                raise ProviderError("request_failed", f"Cosmos request failed ({type(exc).__name__})") from None
            self.last_latency_ms = round((time.perf_counter() - started) * 1000)
            self.reported_model = (getattr(reply, "response_metadata", {}) or {}).get("model_name") or self.reported_model
            text = reply.content if isinstance(reply.content, str) else json.dumps(reply.content)
            try:
                parsed = parser.parse(_extract_json(text))
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError("invalid_output", f"Cosmos reply did not match {schema.__name__}: "
                                                      f"{str(exc).splitlines()[0][:200]}") from None
            self.last_ok_at, self.last_error = time.time(), None
            return parsed
        except ProviderError as exc:
            self.last_error = f"{exc.code}: {exc.message}"
            raise
        finally:
            self._inflight.release()

    def interpret(self, context, image_jpeg):
        return self._ask(GoalProposal, "Interpret the operator instruction for this scene.", context, image_jpeg)

    def next_skill(self, context, image_jpeg):
        return self._ask(SkillDecision, "Choose the next skill for the running task, or pause.", context, image_jpeg)

    def probe(self):
        """A real round trip: list models, then one tiny inference. Returns a report; never raises."""
        report = {"endpoint": self.base_url or None, "model": self.model, "models_listed": None,
                  "model_available": None, "inference_ok": False, "latency_ms": None, "error": None}
        if not self.base_url:
            report["error"] = "VLA_COSMOS_BASE_URL is not set"
            return report
        try:
            import httpx
            r = httpx.get(f"{self.base_url}/models", timeout=5,
                          headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {})
            ids = [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else None
            report["models_listed"] = ids
            report["model_available"] = (self.model in ids) if ids is not None else None
        except Exception as exc:
            report["error"] = f"/models failed ({type(exc).__name__})"
            return report
        try:
            d = self.interpret({"instruction": {"text": "Plan a scoop-and-dump task.", "source": "typed"},
                                "scene": [], "selected_target": None, "note": "connectivity probe, no image"}, None)
            report.update(inference_ok=True, latency_ms=self.last_latency_ms, sample_intent=d.intent)
        except ProviderError as exc:
            report["error"] = f"{exc.code}: {exc.message}"
        return report
