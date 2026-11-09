"""Intent parsing.

Deterministic rules run first. A local model may refine an ambiguous request,
but every identifier it returns is treated as a name hint and resolved against
the caller's actual resources. The model never supplies raw CloudStack IDs,
prices, approvals, or permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from conductor.models.enums import ActionType

_CREATE = (
    "创建",
    "新建",
    "开通",
    "部署",
    "开一台",
    "开个",
    "建一台",
    "建个",
    "create",
    "deploy",
    "provision",
    "launch",
)
_DELETE = ("删除", "销毁", "delete", "destroy", "remove")
_START = ("启动", "开机", "start", "power on")
_STOP = ("停止", "关机", "stop", "shut down", "shutdown")
_REBOOT = ("重启", "reboot", "restart")
_RESIZE = ("调整规格", "变更规格", "升级配置", "降配", "resize", "upgrade instance")
_CONSOLE = ("控制台", "console", "vnc")
_LIST = ("列出", "查看", "有哪些", "show", "list")
_PRICE = ("价格", "费用", "多少钱", "报价", "估算", "price", "cost", "quote", "estimate")
_DOCS = ("文档", "怎么", "如何", "支持", "documentation", "docs", "how to", "explain")
_VOLUME = ("磁盘", "数据盘", "云盘", "卷", "volume", "disk")
_DETACH = ("卸载", "detach")
_ATTACH = ("挂载", "attach")
_EXPAND = ("扩容", "扩大", "expand")
_SNAPSHOT = ("快照", "snapshot")
_NETWORK = ("网络", "network", "防火墙", "firewall")
_SSH = ("ssh", "公钥", "key pair", "keypair")
_GUEST = ("ansible", "安装 nginx", "安装nginx", "docker", "文件系统", "filesystem")
_CLARIFY = ("?", "？")

_NUMBER_CPU = re.compile(r"(\d+)\s*(?:vcpu|cpu|core|核|c(?=\d|\s|$))", re.IGNORECASE)
_NUMBER_MEM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g(?:b)?\b|gb|gib|g内存|内存)", re.IGNORECASE)
_NUMBER_GB = re.compile(r"(\d+)\s*(?:g(?:b)?\b|gib)", re.IGNORECASE)
_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{1,100})[\"'“”‘’]")


@dataclass
class ParsedIntent:
    action: ActionType | None
    confidence: float
    language: str
    text: str
    name_hint: str | None = None
    offering_hint: str | None = None
    cpu_hint: int | None = None
    memory_hint: Decimal | None = None
    size_gb: int | None = None
    target_hint: str | None = None
    query: str | None = None
    source: str = "rules"
    notes: list[str] = field(default_factory=list)


def detect_language(text: str) -> str:
    return "zh-CN" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def parse(text: str, *, default_locale: str = "zh-CN") -> ParsedIntent:
    language = detect_language(text) if text.strip() else default_locale
    lowered = text.lower()
    quoted = _QUOTED.findall(text)
    name_hint = quoted[0].strip() if quoted else _name_phrase(text)
    cpu_hint = _first_int(_NUMBER_CPU, text)
    memory_hint = _first_decimal(_NUMBER_MEM, text)
    size_gb = _first_int(_NUMBER_GB, text) if any(word in lowered for word in _VOLUME) else None

    if any(word in lowered for word in _DOCS) and any(
        word in lowered for word in ("怎么", "如何", "文档", "docs", "how to", "explain", "documentation")
    ):
        return ParsedIntent(ActionType.DOCS_SEARCH, 0.6, language, text, query=text)
    if any(word in lowered for word in _DELETE):
        if any(word in lowered for word in _VOLUME):
            return ParsedIntent(ActionType.DELETE_VOLUME, 0.7, language, text, name_hint=name_hint)
        return ParsedIntent(ActionType.DELETE_INSTANCE, 0.8, language, text, name_hint=name_hint)
    if any(word in lowered for word in _SNAPSHOT):
        if any(word in lowered for word in ("删除", "delete")):
            return ParsedIntent(ActionType.RESTORE_SNAPSHOT, 0.5, language, text, name_hint=name_hint)
        return ParsedIntent(ActionType.CREATE_SNAPSHOT, 0.6, language, text, name_hint=name_hint)
    if any(word in lowered for word in _REBOOT):
        return ParsedIntent(ActionType.REBOOT_INSTANCE, 0.85, language, text, name_hint=name_hint)
    if any(word in lowered for word in _STOP):
        return ParsedIntent(ActionType.STOP_INSTANCE, 0.85, language, text, name_hint=name_hint)
    if any(word in lowered for word in _START):
        return ParsedIntent(ActionType.START_INSTANCE, 0.85, language, text, name_hint=name_hint)
    if any(word in lowered for word in _RESIZE):
        return ParsedIntent(
            ActionType.RESIZE_INSTANCE,
            0.75,
            language,
            text,
            name_hint=name_hint,
            offering_hint=_offering_phrase(text),
        )
    if any(word in lowered for word in _CONSOLE):
        return ParsedIntent(ActionType.OPEN_CONSOLE, 0.9, language, text, name_hint=name_hint)
    if any(word in lowered for word in _DETACH) and any(word in lowered for word in _VOLUME):
        return ParsedIntent(ActionType.DETACH_VOLUME, 0.7, language, text, name_hint=name_hint)
    if any(word in lowered for word in _ATTACH) and any(word in lowered for word in _VOLUME):
        return ParsedIntent(ActionType.ATTACH_VOLUME, 0.7, language, text, name_hint=name_hint)
    if any(word in lowered for word in _EXPAND) and any(word in lowered for word in _VOLUME):
        return ParsedIntent(
            ActionType.RESIZE_VOLUME,
            0.7,
            language,
            text,
            name_hint=name_hint,
            size_gb=size_gb,
        )
    if any(word in lowered for word in _CONSOLE):
        return ParsedIntent(ActionType.OPEN_CONSOLE, 0.9, language, text, name_hint=name_hint)
    if any(word in lowered for word in _SSH):
        return ParsedIntent(ActionType.REGISTER_SSH_KEY, 0.4, language, text)
    if any(word in lowered for word in _NETWORK):
        return ParsedIntent(ActionType.UPDATE_FIREWALL, 0.3, language, text)
    if any(word in lowered for word in _GUEST):
        return ParsedIntent(ActionType.GUEST_TASK, 0.4, language, text, name_hint=name_hint)
    if any(word in lowered for word in _PRICE):
        return ParsedIntent(
            ActionType.QUOTE,
            0.75,
            language,
            text,
            offering_hint=_offering_phrase(text),
            cpu_hint=cpu_hint,
            memory_hint=memory_hint,
        )
    if any(word in lowered for word in _CREATE):
        return ParsedIntent(
            ActionType.CREATE_INSTANCE,
            0.8,
            language,
            text,
            name_hint=name_hint,
            offering_hint=_offering_phrase(text),
            cpu_hint=cpu_hint,
            memory_hint=memory_hint,
        )
    if any(word in lowered for word in _VOLUME) and any(word in lowered for word in _LIST):
        return ParsedIntent(ActionType.LIST_RESOURCES, 0.7, language, text, target_hint="volume")
    if any(word in lowered for word in _LIST):
        return ParsedIntent(ActionType.LIST_RESOURCES, 0.7, language, text, target_hint="instance")
    if any(word in lowered for word in _DOCS):
        return ParsedIntent(ActionType.DOCS_SEARCH, 0.5, language, text, query=text)
    return ParsedIntent(None, 0.0, language, text, query=text)


def parse_with_model(payload: dict[str, object] | None, text: str) -> ParsedIntent | None:
    if not payload:
        return None
    action_text = str(payload.get("action") or "").strip()
    try:
        action = ActionType(action_text)
    except ValueError:
        return None
    if action not in {
        ActionType.CREATE_INSTANCE,
        ActionType.START_INSTANCE,
        ActionType.STOP_INSTANCE,
        ActionType.REBOOT_INSTANCE,
        ActionType.RESIZE_INSTANCE,
        ActionType.DELETE_INSTANCE,
        ActionType.OPEN_CONSOLE,
        ActionType.QUOTE,
        ActionType.LIST_RESOURCES,
        ActionType.DOCS_SEARCH,
    }:
        return None
    return ParsedIntent(
        action=action,
        confidence=(
            float(cast_value) if isinstance((cast_value := payload.get("confidence")), (int, float, str)) else 0.5
        ),
        language=detect_language(text),
        text=text,
        name_hint=_optional_str(payload.get("name")),
        offering_hint=_optional_str(payload.get("offering")),
        target_hint=_optional_str(payload.get("target")),
        query=_optional_str(payload.get("query")) or text,
        source="model",
    )


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def _first_decimal(pattern: re.Pattern[str], text: str) -> Decimal | None:
    match = pattern.search(text)
    return Decimal(match.group(1)) if match else None


def _name_phrase(text: str) -> str | None:
    match = re.search(
        r"(?:名称|名字|命名为|name(?:d)?)\s*(?:为|是|:)?\s*[\"'“”]?([A-Za-z0-9][A-Za-z0-9._-]{0,62})",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(
        r"(?:云主机|实例|虚拟机|instance|vm)\s*(?:名称|名字)?\s*[：:]?\s*([A-Za-z0-9][A-Za-z0-9._-]{0,62})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _offering_phrase(text: str) -> str | None:
    quoted = _QUOTED.findall(text)
    if quoted:
        return quoted[0]
    match = re.search(
        r"(?:规格|套餐|offering|配置)\s*(?:为|是|:)?\s*([A-Za-z0-9][A-Za-z0-9._ -]{1,59})", text, re.IGNORECASE
    )
    return match.group(1).strip() if match else None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:120]
    return None


INTENT_PROMPT = """You classify a CloudStack operations request for a tenant portal.
Return only JSON with keys: action, name, offering, target, query, confidence.
Allowed action values: create_instance, start_instance, stop_instance, reboot_instance,
resize_instance, delete_instance, open_console, quote, list_resources, docs_search, unknown.
Use names as written by the user; never invent identifiers or prices.
"""
