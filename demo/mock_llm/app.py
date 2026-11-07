"""Synthetic OpenAI-compatible model gateway for the public demo.

Returns deterministic, generic text. It is not a real model and must never be
used in production (the API configuration rejects mock mode there).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODEL_ALIAS = os.environ.get("DEMO_MODEL_ALIAS", "deepseek-flash")
API_KEY = os.environ.get("DEMO_LLM_API_KEY")

app = FastAPI(title="CloudStack Conductor demo model gateway", docs_url=None, openapi_url=None)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None


@app.post("/v1/chat/completions")
async def chat(body: ChatRequest) -> dict[str, Any]:
    if API_KEY and body.model != MODEL_ALIAS:
        raise HTTPException(status_code=400, detail="model alias is not approved")
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages are required")
    system = " ".join(message.content for message in body.messages if message.role == "system")
    user = next((message.content for message in reversed(body.messages) if message.role == "user"), "")
    if "classify" in system.lower() or "return only json" in system.lower():
        content = '{"action":"unknown","confidence":0.0}'
    else:
        chinese = any("\u4e00" <= character <= "\u9fff" for character in user)
        if chinese:
            content = (
                "这是演示环境的本地模型回复。我可以帮助解释 CloudStack 资源、费用估算和受控操作。"
                "任何变更都会先生成计划，必须由你在门户中审批后才会执行。"
            )
        else:
            content = (
                "This is a demo local-model response. I can explain CloudStack resources, "
                "estimates, and supported operations. Any change is prepared as a plan and "
                "must be approved by you in the portal before it runs."
            )
    return {
        "id": "chatcmpl-demo",
        "object": "chat.completion",
        "model": MODEL_ALIAS,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "demo-llm"}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
