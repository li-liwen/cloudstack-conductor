from __future__ import annotations

from decimal import Decimal

from conductor.agent.intents import parse, parse_with_model
from conductor.models.enums import ActionType


def test_chinese_create_with_shape() -> None:
    intent = parse("创建一台 2C4G 的云主机")
    assert intent.action is ActionType.CREATE_INSTANCE
    assert intent.cpu_hint == 2
    assert intent.memory_hint == 4
    assert intent.language == "zh-CN"


def test_colloquial_chinese_create() -> None:
    assert parse("帮我开一台 demo.small").action is ActionType.CREATE_INSTANCE
    assert parse("建一台 demo.small 云主机").action is ActionType.CREATE_INSTANCE
    # Power-on wording remains a start intent.
    assert parse("开机 demo-web").action is ActionType.START_INSTANCE


def test_english_create_with_name() -> None:
    intent = parse('create an instance called "demo-app" with 4 vCPU and 8 GB')
    assert intent.action is ActionType.CREATE_INSTANCE
    assert intent.name_hint == "demo-app"
    assert intent.cpu_hint == 4
    assert intent.memory_hint == 8


def test_delete_volume_is_distinguished() -> None:
    assert parse("删除数据盘 demo-data").action is ActionType.DELETE_VOLUME
    assert parse("delete the volume demo-data").action is ActionType.DELETE_VOLUME
    assert parse("删除云主机 demo-web").action is ActionType.DELETE_INSTANCE


def test_power_and_console_intents() -> None:
    assert parse("启动 demo-web").action is ActionType.START_INSTANCE
    assert parse("stop demo-web").action is ActionType.STOP_INSTANCE
    assert parse("reboot demo-web").action is ActionType.REBOOT_INSTANCE
    assert parse("打开 demo-web 的控制台").action is ActionType.OPEN_CONSOLE


def test_quote_and_list_intents() -> None:
    assert parse("演示规格的价格是多少").action is ActionType.QUOTE
    assert parse("创建这个规格要多少钱").action is ActionType.QUOTE
    assert parse("列出云主机").action is ActionType.LIST_RESOURCES
    assert parse("show volumes").action is ActionType.LIST_RESOURCES


def test_docs_and_unknown() -> None:
    assert parse("CloudStack 快照怎么用").action is ActionType.DOCS_SEARCH
    assert parse("hello there").action is None


def test_model_refinement_only_accepts_allowlisted_actions() -> None:
    refined = parse_with_model(
        {"action": "start_instance", "name": "demo-web", "confidence": 0.9},
        "could you bring demo-web up",
    )
    assert refined is not None
    assert refined.action is ActionType.START_INSTANCE
    assert parse_with_model({"action": "run_shell", "name": "x"}, "do it") is None


def test_model_hints_are_names_not_ids() -> None:
    refined = parse_with_model(
        {
            "action": "delete_instance",
            "name": "11111111-1111-4111-8111-111111111111",
        },
        "delete it",
    )
    assert refined is not None
    assert refined.name_hint == "11111111-1111-4111-8111-111111111111"
    # The runner treats this as a name hint and resolves it against the caller's
    # resources; no model-supplied identifier is used directly.


def test_fractional_memory_and_offering_name_are_parsed() -> None:
    parsed = parse("创建一台 1C0.5G 的云主机", default_locale="zh-CN")
    assert parsed.cpu_hint == 1
    assert parsed.memory_hint == Decimal("0.5")
    parsed = parse(
        "创建一台规格为 Small Instance 的云主机，名称 pilot-created",
        default_locale="zh-CN",
    )
    assert parsed.offering_hint == "Small Instance"
    assert parsed.name_hint == "pilot-created"


def test_resource_phrase_supplies_instance_name() -> None:
    parsed = parse("删除云主机 agent-created", default_locale="zh-CN")
    assert parsed.action is ActionType.DELETE_INSTANCE
    assert parsed.name_hint == "agent-created"
