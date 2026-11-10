"""Closest-offering matching for natural-language shape requests.

The caller may ask for a shape such as ``2 vCPU / 4 GiB`` without naming a
CloudStack offering. CloudStack offerings are discrete, so the agent chooses
the closest available shape and explains whether it actually satisfies the
request instead of asking the user to translate the request into an internal
offering name.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class OfferingMatch:
    """A selected offering plus the requested shape."""

    offering: dict[str, Any]
    requested_cpu: int | None
    requested_memory: Decimal | None

    @property
    def cpu(self) -> int:
        return int(self.offering.get("cpu_count") or 0)

    @property
    def memory(self) -> Decimal:
        try:
            return Decimal(str(self.offering.get("memory_gib") or "0"))
        except (InvalidOperation, ValueError):
            return Decimal(0)

    @property
    def exact(self) -> bool:
        return (
            self.requested_cpu is not None
            and self.requested_memory is not None
            and self.cpu == self.requested_cpu
            and self.memory == self.requested_memory
        )

    @property
    def satisfies(self) -> bool:
        if self.requested_cpu is None and self.requested_memory is None:
            return True
        if self.requested_cpu is not None and self.cpu < self.requested_cpu:
            return False
        return not (self.requested_memory is not None and self.memory < self.requested_memory)

    def describe(self, *, locale: str = "zh-CN") -> str:
        if self.requested_cpu is None and self.requested_memory is None:
            return ""
        name = str(self.offering.get("name") or self.offering.get("id") or "")
        requested = _shape(self.requested_cpu, self.requested_memory)
        actual = _shape(self.cpu, self.memory)
        if locale == "en":
            if self.exact:
                return f"Requested {requested}; using {name} ({actual}) — exact match."
            verdict = "meets or exceeds the request" if self.satisfies else "does not fully meet the request"
            return f"Requested {requested}; closest available is {name} ({actual}) — {verdict}."
        if self.exact:
            return f"配置匹配：请求 {requested}，选用 {name}（{actual}），完全满足。"
        verdict = "满足或高于请求" if self.satisfies else "低于请求"
        return f"配置匹配：请求 {requested}，最接近的可用配置是 {name}（{actual}），{verdict}。"


def _shape(cpu: int | None, memory: Decimal | None) -> str:
    parts: list[str] = []
    if cpu is not None:
        parts.append(f"{cpu} vCPU")
    if memory is not None:
        parts.append(f"{_decimal(memory)} GiB")
    return " / ".join(parts) if parts else "any"


def _decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _score(
    offering: dict[str, Any], cpu: int | None, memory: Decimal | None
) -> tuple[float, int, int, Decimal, str]:
    actual_cpu = int(offering.get("cpu_count") or 0)
    try:
        actual_memory = Decimal(str(offering.get("memory_gib") or "0"))
    except (InvalidOperation, ValueError):
        actual_memory = Decimal(0)
    score = 0.0
    if cpu is not None:
        score += abs(actual_cpu - cpu) / max(cpu, 1)
    if memory is not None and memory > 0:
        score += float(abs(actual_memory - memory)) / float(memory)
    # On a tie prefer a shape that does not under-provision; then prefer the
    # smaller shape and a stable name order.
    meets = (cpu is None or actual_cpu >= cpu) and (memory is None or actual_memory >= memory)
    return (score, 0 if meets else 1, actual_cpu, actual_memory, str(offering.get("name") or ""))


def select_offering(
    offerings: list[dict[str, Any]],
    *,
    hint: str | None = None,
    text: str | None = None,
    cpu: int | None = None,
    memory: Decimal | None = None,
) -> OfferingMatch | None:
    """Select an explicit offering or the closest shape match.

    An explicit name/id hint wins when it resolves. Otherwise a requested CPU
    and/or memory shape selects the closest offering; no random or arbitrary
    offering is chosen when the caller gave no usable hint.
    """

    candidates = [item for item in offerings if int(item.get("cpu_count") or 0) > 0]
    if not candidates:
        return None
    selected: dict[str, Any] | None = None
    lowered_hint = (hint or "").strip().lower()
    if lowered_hint:
        exact = [
            item
            for item in candidates
            if lowered_hint == str(item.get("id", "")).lower()
            or lowered_hint == str(item.get("name", "")).strip().lower()
        ]
        if exact:
            selected = exact[0]
        else:
            matches = [item for item in candidates if lowered_hint in str(item.get("name", "")).lower()]
            if len(matches) == 1:
                selected = matches[0]
    if selected is None and not lowered_hint and text:
        lowered_text = text.lower()
        by_text = [
            item
            for item in candidates
            if str(item.get("name", "")).strip() and str(item["name"]).lower() in lowered_text
        ]
        if len(by_text) == 1:
            selected = by_text[0]
    if selected is None and (cpu is not None or memory is not None):
        selected = min(candidates, key=lambda item: _score(item, cpu, memory))
    if selected is None and len(candidates) == 1:
        selected = candidates[0]
    if selected is None:
        return None
    return OfferingMatch(offering=selected, requested_cpu=cpu, requested_memory=memory)
