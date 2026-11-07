"""Agent intent-resolution graph.

LangGraph provides durable checkpoints for the agent workflow. The graph only
classifies intent and loads authorized context; all authorization, pricing, and
approval remain deterministic services. A local model may refine the intent, but
the graph never accepts identifiers, prices, or permissions from it.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from conductor.agent.intents import INTENT_PROMPT, ParsedIntent, parse, parse_with_model
from conductor.agent.llm import ModelGateway
from conductor.execution.client import ExecutionClient
from conductor.models.enums import ActionType


class AgentState(TypedDict, total=False):
    text: str
    locale: str
    subject: str
    parsed: dict[str, Any]
    resources: dict[str, Any]
    route: str
    error: str


def _to_dict(parsed: ParsedIntent) -> dict[str, Any]:
    return {
        "action": parsed.action.value if parsed.action else None,
        "confidence": parsed.confidence,
        "language": parsed.language,
        "text": parsed.text,
        "name_hint": parsed.name_hint,
        "offering_hint": parsed.offering_hint,
        "cpu_hint": parsed.cpu_hint,
        "memory_hint": parsed.memory_hint,
        "size_gb": parsed.size_gb,
        "target_hint": parsed.target_hint,
        "query": parsed.query,
        "source": parsed.source,
        "notes": parsed.notes,
    }


def _from_dict(value: dict[str, Any]) -> ParsedIntent:
    action = value.get("action")
    return ParsedIntent(
        action=ActionType(action) if action else None,
        confidence=float(value.get("confidence") or 0.0),
        language=str(value.get("language") or "zh-CN"),
        text=str(value.get("text") or ""),
        name_hint=value.get("name_hint"),
        offering_hint=value.get("offering_hint"),
        cpu_hint=value.get("cpu_hint"),
        memory_hint=value.get("memory_hint"),
        size_gb=value.get("size_gb"),
        target_hint=value.get("target_hint"),
        query=value.get("query"),
        source=str(value.get("source") or "rules"),
        notes=list(value.get("notes") or []),
    )


def build_intent_graph(
    execution: ExecutionClient,
    model: ModelGateway,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
):
    async def parse_node(state: AgentState) -> AgentState:
        text = state.get("text", "")
        locale = state.get("locale", "zh-CN")
        parsed = parse(text, default_locale=locale)
        if parsed.action is None and parsed.confidence < 0.3 and model.configured:
            payload = await model.json_chat(
                [
                    {"role": "system", "content": INTENT_PROMPT},
                    {"role": "user", "content": text},
                ]
            )
            refined = parse_with_model(payload, text)
            if refined is not None:
                parsed = refined
        route = _route(parsed)
        return {"parsed": _to_dict(parsed), "route": route, "error": ""}

    async def context_node(state: AgentState) -> AgentState:
        if state.get("route") not in {"read", "quote", "plan", "console"}:
            return {}
        try:
            resources = await execution.resources(subject=state["subject"], kind="all")
        except Exception:
            return {"error": "context_unavailable"}
        return {"resources": resources.get("items", {})}

    graph = StateGraph(AgentState)
    graph.add_node("parse", parse_node)
    graph.add_node("context", context_node)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "context")
    graph.add_edge("context", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def _route(parsed: ParsedIntent) -> str:
    if parsed.action is None:
        return "general"
    if parsed.action is ActionType.DOCS_SEARCH:
        return "docs"
    if parsed.action is ActionType.LIST_RESOURCES:
        return "read"
    if parsed.action is ActionType.QUOTE:
        return "quote"
    if parsed.action is ActionType.OPEN_CONSOLE:
        return "console"
    return "plan"


__all__ = ["AgentState", "_from_dict", "_to_dict", "build_intent_graph"]
