"""Agent run worker.

The agent turns a user message into either an explanation, a read-only answer,
a quote, or a persisted plan. It cannot approve or execute changes. Prices,
identifiers, and permissions always come from the execution service.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.agent.graph import _from_dict, build_intent_graph
from conductor.agent.intents import ParsedIntent
from conductor.agent.llm import ModelGateway
from conductor.agent.offering_match import OfferingMatch, select_offering
from conductor.config import Settings
from conductor.db.conversations import ConversationRepository
from conductor.execution.client import ExecutionClient
from conductor.knowledge.search import KnowledgeSearch
from conductor.models.common import utcnow
from conductor.models.conversation import MessagePart
from conductor.models.enums import ActionType, RunState
from conductor.models.pricing import PriceQuote
from conductor.pricing.estimates import (
    half_year_reference,
    monthly_reference,
    quote_summary,
)

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        execution: ExecutionClient,
        model: ModelGateway | None = None,
        checkpointer=None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.execution = execution
        self.model = model or ModelGateway(settings)
        self.graph = build_intent_graph(execution, self.model, checkpointer=checkpointer)

    async def run(self, run_id: str) -> None:
        async with self.session_factory() as session:
            conversations = ConversationRepository(session)
            run = await conversations.get_run(run_id)
            if run is None or run.state is not RunState.QUEUED:
                return
            conversation = await conversations.get(run.conversation_id, run.principal_subject)
            if conversation is None:
                await conversations.update_run(run_id, state=RunState.FAILED, error="conversation missing")
                await session.commit()
                return
            messages = await conversations.messages(conversation.conversation_id, limit=20)
            user_messages = [item for item in messages if item.role == "user"]
            if not user_messages:
                await conversations.update_run(run_id, state=RunState.FAILED, error="no user message")
                await session.commit()
                return
            text = user_messages[-1].parts[0].text if user_messages[-1].parts else ""
            text = text or ""
            await conversations.update_run(run_id, state=RunState.RUNNING)
            await conversations.add_event(
                run_id,
                "status",
                {
                    "state": RunState.RUNNING.value,
                    "message_zh": "正在理解请求…",
                    "message_en": "Understanding the request…",
                },
            )
            await session.commit()
            try:
                await self._handle(
                    session,
                    conversations,
                    run_id=run_id,
                    conversation_id=conversation.conversation_id,
                    subject=run.principal_subject,
                    text=text,
                    locale=conversation.cloud_context.get("locale", "zh-CN"),
                    history=user_messages,
                )
            except Exception as exc:
                logger.exception("agent run %s failed", run_id)
                await self._fail(session, conversations, run_id, exc)
                return
            await session.commit()

    async def _handle(
        self,
        session: AsyncSession,
        conversations: ConversationRepository,
        *,
        run_id: str,
        conversation_id: str,
        subject: str,
        text: str,
        locale: str,
        history: list[Any],
    ) -> None:
        state = await self.graph.ainvoke(
            {"text": text, "locale": locale, "subject": subject},
            config={"configurable": {"thread_id": run_id}},
        )
        parsed = _from_dict(state.get("parsed") or {})
        if state.get("error"):
            logger.info("agent graph could not load context: %s", state["error"])
        if parsed.action is None:
            await self._general_answer(session, conversations, run_id, conversation_id, text, locale)
            return
        if parsed.action is ActionType.DOCS_SEARCH:
            await self._docs_answer(session, conversations, run_id, conversation_id, parsed.query or text, locale)
            return
        items = state.get("resources", {}) or {}
        if parsed.action is ActionType.LIST_RESOURCES:
            await self._list_answer(session, conversations, run_id, conversation_id, items, locale)
            return
        if parsed.action is ActionType.QUOTE:
            await self._quote_answer(session, conversations, run_id, conversation_id, subject, items, parsed, locale)
            return
        if parsed.action is ActionType.OPEN_CONSOLE:
            instance = self._pick_instance(items, parsed)
            if instance is None:
                await self._clarify(session, conversations, run_id, conversation_id, items, locale, "instance")
                return
            try:
                lease = await self.execution.console_lease(subject=subject, vm_id=instance["id"])
            except Exception as exc:
                await self._error_message(
                    session,
                    conversations,
                    run_id,
                    conversation_id,
                    "无法打开控制台。 / The console could not be opened.",
                    locale,
                    code=getattr(exc, "code", "console_error"),
                )
                return
            await self._assistant(
                session,
                conversations,
                run_id,
                conversation_id,
                [
                    MessagePart(
                        type="text",
                        text=(
                            f"已为 {instance['name']} 生成一次性控制台入口，60 秒内有效。"
                            if locale != "en"
                            else f"A one-time console link for {instance['name']} is valid for 60 seconds."
                        ),
                    ),
                    MessagePart(
                        type="resource",
                        data={"kind": "console", "launch": lease},
                    ),
                ],
            )
            await self._done(session, conversations, run_id)
            return

        plan_params, missing = await self._build_mutation_params(parsed, items, subject, locale)
        if missing:
            await self._clarify(session, conversations, run_id, conversation_id, items, locale, missing)
            return
        try:
            plan = await self.execution.create_plan(
                subject=subject,
                action=parsed.action.value,
                parameters=plan_params,
            )
        except Exception as exc:
            await self._error_message(
                session,
                conversations,
                run_id,
                conversation_id,
                self._plan_error_message(exc, locale),
                locale,
                code=getattr(exc, "code", "plan_error"),
            )
            return
        await conversations.update_run(
            run_id,
            state=RunState.AWAITING_APPROVAL,
            plan_id=plan["plan_id"],
        )
        await conversations.add_event(run_id, "plan", plan)
        quote = plan.get("quote") or {}
        approval_url = f"/plans/{plan['plan_id']}"
        explanation_zh = str(plan.get("explanation_zh") or plan["action"]).rstrip("。.!！ ")
        match_note = ""
        if parsed.action is ActionType.CREATE_INSTANCE:
            selected = next(
                (item for item in items.get("offerings", []) if item.get("id") == plan_params.get("offering_id")),
                None,
            )
            if selected is not None:
                match = self._offering_match([selected], parsed)
                if match is not None:
                    match_note = match.describe(locale=locale)
        prefix = match_note + "\n" if match_note else ""
        if locale != "en":
            summary = f"{prefix}已生成计划：{explanation_zh}。审批后才会执行，审批在 10 分钟内有效。"
        else:
            explanation_en = str(plan.get("explanation_en") or plan["action"]).rstrip(".")
            summary = (
                f"{prefix}A plan was prepared: {explanation_en}. "
                "Nothing runs until you approve it; approval is valid for 10 minutes."
            )
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=summary),
                MessagePart(
                    type="plan",
                    data={
                        "plan_id": plan["plan_id"],
                        "action": plan["action"],
                        "digest": plan["digest"],
                        "approval_url": approval_url,
                        "hourly": quote.get("total_hourly"),
                        "currency": quote.get("currency"),
                        "effects": plan.get("effects", []),
                        "expires_at": plan.get("expires_at"),
                    },
                ),
            ],
        )
        await self._done(session, conversations, run_id, state=RunState.AWAITING_APPROVAL)

    # -- slot resolution ---------------------------------------------------
    def _pick_instance(self, items: dict[str, list[dict]], parsed: ParsedIntent) -> dict | None:
        instances = items.get("instances", [])
        hint = (parsed.name_hint or "").strip().lower()
        if hint:
            matches = [
                item
                for item in instances
                if hint == str(item.get("id", "")).lower() or hint in str(item.get("name", "")).lower()
            ]
            if len(matches) == 1:
                return matches[0]
        if len(instances) == 1:
            return instances[0]
        return None

    async def _build_mutation_params(
        self, parsed: ParsedIntent, items: dict[str, list[dict]], subject: str, locale: str
    ) -> tuple[dict[str, Any], list[str]]:
        action = parsed.action
        if action is ActionType.CREATE_INSTANCE:
            offerings = items.get("offerings", [])
            templates = [item for item in items.get("templates", []) if item.get("ready")]
            networks = items.get("networks", [])
            if not offerings or not templates or not networks:
                return {}, ["offering", "template", "network"]
            offering = self._pick_offering(offerings, parsed)
            if offering is None:
                return {}, ["offering"]
            template = self._pick_named(templates, parsed.target_hint or parsed.name_hint)
            if template is None:
                if len(templates) == 1:
                    template = templates[0]
                else:
                    ubuntu = [item for item in templates if "ubuntu" in str(item.get("name", "")).lower()]
                    template = ubuntu[0] if len(ubuntu) == 1 else None
            if template is None:
                return {}, ["template"]
            network = self._pick_network(networks, parsed.target_hint)
            if network is None:
                return {}, ["network"]
            zone_id = network.get("zone_id") or template.get("zone_id")
            if not zone_id:
                return {}, ["zone"]
            name = parsed.name_hint or f"conductor-{secrets.token_hex(3)}"
            return {
                "name": name,
                "offering_id": offering["id"],
                "template_id": template["id"],
                "zone_id": zone_id,
                "network_ids": [network["id"]],
                "start": True,
            }, []
        if action in {
            ActionType.START_INSTANCE,
            ActionType.STOP_INSTANCE,
            ActionType.REBOOT_INSTANCE,
            ActionType.DELETE_INSTANCE,
            ActionType.OPEN_CONSOLE,
        }:
            instance = self._pick_instance(items, parsed)
            if instance is None:
                return {}, ["instance"]
            params: dict[str, Any] = {"instance_id": instance["id"]}
            if action is ActionType.DELETE_INSTANCE:
                params["expunge"] = False
            return params, []
        if action is ActionType.RESIZE_INSTANCE:
            instance = self._pick_instance(items, parsed)
            if instance is None:
                return {}, ["instance"]
            target = self._pick_offering(items.get("offerings", []), parsed)
            if target is None:
                return {}, ["offering"]
            return {"instance_id": instance["id"], "offering_id": target["id"]}, []
        if action is ActionType.DELETE_VOLUME:
            volume = self._pick_volume(items, parsed)
            if volume is None:
                return {}, ["volume"]
            return {"volume_id": volume["id"]}, []
        if action is ActionType.ATTACH_VOLUME:
            volume = self._pick_volume(items, parsed)
            instance = self._pick_instance(items, parsed)
            if volume is None:
                return {}, ["volume"]
            if instance is None:
                return {}, ["instance"]
            return {"volume_id": volume["id"], "instance_id": instance["id"]}, []
        if action is ActionType.DETACH_VOLUME:
            volume = self._pick_volume(items, parsed)
            if volume is None:
                return {}, ["volume"]
            return {"volume_id": volume["id"]}, []
        if action is ActionType.RESIZE_VOLUME:
            volume = self._pick_volume(items, parsed)
            if volume is None:
                return {}, ["volume"]
            if not parsed.size_gb:
                return {}, ["size_gb"]
            return {"volume_id": volume["id"], "size_gib": parsed.size_gb}, []
        return {}, ["unsupported"]

    def _pick_offering(self, offerings: list[dict], parsed: ParsedIntent) -> dict | None:
        match = select_offering(
            offerings,
            hint=parsed.offering_hint,
            text=parsed.text,
            cpu=parsed.cpu_hint,
            memory=parsed.memory_hint,
        )
        return match.offering if match is not None else None

    def _offering_match(self, offerings: list[dict], parsed: ParsedIntent) -> OfferingMatch | None:
        return select_offering(
            offerings,
            hint=parsed.offering_hint,
            text=parsed.text,
            cpu=parsed.cpu_hint,
            memory=parsed.memory_hint,
        )

    @staticmethod
    def _pick_named(candidates: list[dict], hint: str | None) -> dict | None:
        if not hint:
            return None
        lowered = hint.lower()
        matches = [
            item
            for item in candidates
            if lowered == str(item.get("id", "")).lower() or lowered in str(item.get("name", "")).lower()
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _pick_network(networks: list[dict], hint: str | None) -> dict | None:
        named = AgentRunner._pick_named(networks, hint)
        if named is not None:
            return named
        defaults = [item for item in networks if item.get("is_default")]
        if len(defaults) == 1:
            return defaults[0]
        if len(networks) == 1:
            return networks[0]
        return None

    def _pick_volume(self, items: dict[str, list[dict]], parsed: ParsedIntent) -> dict | None:
        volumes = items.get("volumes", [])
        hint = (parsed.name_hint or "").strip().lower()
        if hint:
            matches = [
                item
                for item in volumes
                if hint == str(item.get("id", "")).lower() or hint in str(item.get("name", "")).lower()
            ]
            if len(matches) == 1:
                return matches[0]
        if len(volumes) == 1:
            return volumes[0]
        return None

    # -- answers -----------------------------------------------------------
    async def _list_answer(self, session, conversations, run_id, conversation_id, items, locale):
        lines = []
        for instance in items.get("instances", [])[:20]:
            lines.append(
                f"- {instance['name']}：{instance['state']}，{instance.get('offering_name') or ''}"
                f"{'，IP ' + ', '.join(instance.get('ip_addresses') or []) if instance.get('ip_addresses') else ''}"
            )
        if not lines:
            text = "当前账户没有云主机。" if locale != "en" else "This account has no instances."
        else:
            header = "当前云主机：" if locale != "en" else "Current instances:"
            text = header + "\n" + "\n".join(lines)
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=text),
                MessagePart(
                    type="resource",
                    data={"kind": "instances", "items": items.get("instances", [])[:50]},
                ),
            ],
        )
        await self._done(session, conversations, run_id)

    async def _quote_answer(self, session, conversations, run_id, conversation_id, subject, items, parsed, locale):
        offering = self._pick_offering(items.get("offerings", []), parsed)
        if offering is None:
            await self._clarify(session, conversations, run_id, conversation_id, items, locale, "offering")
            return
        try:
            quote_dict = await self.execution.quote(subject=subject, parameters={"offering_id": offering["id"]})
        except Exception as exc:
            await self._error_message(
                session,
                conversations,
                run_id,
                conversation_id,
                "暂时无法报价。 / The quote is unavailable.",
                locale,
                code=getattr(exc, "code", "quote_error"),
            )
            return
        quote = PriceQuote.model_validate(quote_dict)
        now = utcnow()
        month = monthly_reference(quote, now, locale=locale)
        half = half_year_reference(quote, now, locale=locale)
        if locale != "en":
            text = (
                f"{quote.offering_name}（{quote.shape.describe() if quote.shape else '未知规格'}）\n"
                f"每小时：{quote.total_hourly} {quote.currency}\n"
                f"整月参考：{month.total} {quote.currency}\n"
                f"半年参考：{half.total} {quote.currency}\n"
                "费用预估，仅供参考，以最终账单为准。"
            )
        else:
            text = (
                f"{quote.offering_name} ({quote.shape.describe() if quote.shape else 'unknown shape'})\n"
                f"Per hour: {quote.total_hourly} {quote.currency}\n"
                f"Full month reference: {month.total} {quote.currency}\n"
                f"Half-year reference: {half.total} {quote.currency}\n"
                "This is an estimate for reference only; the final invoice prevails."
            )
        if not quote.priced:
            text += (
                "\n该规格尚未定价，以上为不完整信息。"
                if locale != "en"
                else "\nThis shape is unpriced; the figures are incomplete."
            )
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=text),
                MessagePart(
                    type="quote",
                    data={
                        "quote": quote_dict,
                        "summary": quote_summary(quote, now, locale=locale),
                        "disclaimer_zh": "费用预估，仅供参考，以最终账单为准。",
                    },
                ),
            ],
        )
        await self._done(session, conversations, run_id)

    async def _docs_answer(self, session, conversations, run_id, conversation_id, query, locale):
        search = KnowledgeSearch(session, self.settings)
        try:
            response = await search.search(query)
        except Exception:
            logger.exception("knowledge search failed")
            await self._general_answer(session, conversations, run_id, conversation_id, query, locale)
            return
        if not response.hits:
            await self._general_answer(session, conversations, run_id, conversation_id, query, locale)
            return
        lines = []
        for index, hit in enumerate(response.hits[:5], start=1):
            lines.append(f"[{index}] {hit.title} — {hit.snippet}\n    {hit.source_url} (v{hit.version})")
        header = "根据已索引的公开文档：" if locale != "en" else "From the indexed public documentation:"
        text = header + "\n\n" + "\n".join(lines)
        if self.model.configured:
            context = "\n\n".join(f"{hit.title}: {hit.snippet}" for hit in response.hits[:5])
            try:
                answer = await self.model.chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Answer using only the supplied documentation excerpts. "
                                "If the excerpts do not answer, say so. Answer in the user's language. "
                                "Never invent CloudStack commands, URLs, credentials, or prices."
                            ),
                        },
                        {"role": "user", "content": f"Question: {query}\n\nExcerpts:\n{context}"},
                    ]
                )
                text = answer.strip() + "\n\n" + text
            except Exception:
                pass
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=text),
                *[
                    MessagePart(
                        type="citation",
                        data={
                            "title": hit.title,
                            "source_url": hit.source_url,
                            "version": hit.version,
                        },
                    )
                    for hit in response.hits[:5]
                ],
            ],
        )
        await self._done(session, conversations, run_id)

    async def _general_answer(self, session, conversations, run_id, conversation_id, query, locale):
        if self.model.configured:
            try:
                answer = await self.model.chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are CloudStack Conductor's portal assistant. Help with the "
                                "supported operations: create/start/stop/reboot/resize/delete instances, "
                                "volumes, consoles, quotes, and documentation. You cannot approve or "
                                "execute changes; prepare a plan for the user to approve. Never invent "
                                "prices, credentials, identifiers, or commands. Ask a clarifying question "
                                "when information is missing. Answer in the user's language."
                            ),
                        },
                        {"role": "user", "content": query},
                    ]
                )
                await self._assistant(
                    session,
                    conversations,
                    run_id,
                    conversation_id,
                    [MessagePart(type="text", text=answer.strip())],
                )
                await self._done(session, conversations, run_id)
                return
            except Exception:
                logger.info("model answer unavailable; using help text")
        text = (
            "我可以帮你创建、启动、停止、重启、调整或删除云主机，查看资源与费用，打开控制台，"
            "或检索文档。请说明具体资源名称和目标，例如“创建一台 2C4G 的云主机”。"
            if locale != "en"
            else "I can create, start, stop, reboot, resize, or delete instances; show resources and "
            "costs; open consoles; and search documentation. Include a resource name and target, for "
            "example “create a 2 vCPU / 4 GiB instance”."
        )
        await self._assistant(session, conversations, run_id, conversation_id, [MessagePart(type="text", text=text)])
        await self._done(session, conversations, run_id)

    async def _clarify(self, session, conversations, run_id, conversation_id, items, locale, missing):
        if isinstance(missing, str):
            missing = [missing]
        options = {
            "offering": [item["name"] for item in items.get("offerings", [])[:12]],
            "template": [item["name"] for item in items.get("templates", [])[:12]],
            "network": [item["name"] for item in items.get("networks", [])[:12]],
            "instance": [item["name"] for item in items.get("instances", [])[:12]],
            "volume": [item["name"] for item in items.get("volumes", [])[:12]],
        }
        lines = []
        for field in missing:
            if field in options:
                if locale != "en":
                    lines.append(f"请选择{field}：" + "、".join(options[field]))
                else:
                    lines.append(f"Choose a {field}: " + ", ".join(options[field]))
            elif field == "size_gb":
                lines.append("请给出目标容量（GB）。" if locale != "en" else "Provide the target size in GB.")
            else:
                lines.append(f"缺少必要信息：{field}。" if locale != "en" else f"Missing information: {field}.")
        text = "\n".join(lines) or ("需要更多信息才能继续。" if locale != "en" else "More information is needed.")
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=text),
                MessagePart(type="progress", data={"missing": missing}),
            ],
        )
        await self._done(session, conversations, run_id)

    def _plan_error_message(self, exc: Exception, locale: str) -> str:
        code = getattr(exc, "code", "plan_error")
        messages = {
            "credentials_missing": (
                "该身份尚未配置 CloudStack API 凭据，请联系管理员。",
                "No CloudStack API credentials are provisioned for this identity; contact an administrator.",
            ),
            "credentials_disabled": (
                "该身份的 CloudStack API 访问已停用。",
                "CloudStack API access is disabled for this identity.",
            ),
            "unsupported": (
                "该操作或规格暂不支持，或规格尚未定价。",
                "The operation or shape is not supported, or the shape is not priced.",
            ),
            "not_found": (
                "未找到所需资源，请刷新后重试。",
                "A required resource was not found; refresh and try again.",
            ),
            "approval_required": (
                "该操作需要明确审批。",
                "This action requires explicit approval.",
            ),
        }
        zh, en = messages.get(code, ("无法生成计划。", "The plan could not be prepared."))
        return zh if locale != "en" else en

    # -- persistence helpers ----------------------------------------------
    async def _assistant(self, session, conversations, run_id, conversation_id, parts):
        message = await conversations.add_message(
            conversation_id=conversation_id,
            role="assistant",
            parts=parts,
            run_id=run_id,
            citations=[part.data for part in parts if part.type == "citation"],
        )
        await conversations.update_run(run_id, assistant_message_id=message.message_id)
        await conversations.add_event(run_id, "message", message.model_dump(mode="json"))
        await session.commit()
        return message

    async def _done(self, session, conversations, run_id, *, state: RunState = RunState.COMPLETED):
        await conversations.update_run(run_id, state=state)
        await conversations.add_event(run_id, "done", {"state": state.value})
        await session.commit()

    async def _error_message(self, session, conversations, run_id, conversation_id, text, locale, code):
        await self._assistant(
            session,
            conversations,
            run_id,
            conversation_id,
            [
                MessagePart(type="text", text=text),
                MessagePart(type="progress", data={"error": code}),
            ],
        )
        await conversations.update_run(run_id, state=RunState.COMPLETED, error=code)
        await conversations.add_event(run_id, "done", {"state": RunState.COMPLETED.value, "error": code})
        await session.commit()

    async def _fail(self, session, conversations, run_id, exc: Exception):
        code = getattr(exc, "code", "agent_error")
        await conversations.update_run(run_id, state=RunState.FAILED, error=code)
        await conversations.add_event(
            run_id,
            "error",
            {"code": code, "message": "The request could not be completed."},
        )
        await conversations.add_event(run_id, "done", {"state": RunState.FAILED.value})
        await session.commit()
