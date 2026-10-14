"""Validate Anthropic authentication and model access without logging credentials."""
import asyncio
import json
import logging
import re

from anthropic import APIStatusError, AsyncAnthropic

from .config import settings
from .db import Audit, Session

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
phase = "configuration"


async def main():
    global phase
    cfg = settings()
    if not cfg.anthropic_api_key.get_secret_value() or not cfg.claude_model:
        print(json.dumps({"event": "claude_credentials_verified", "status": "NOT_CONFIGURED"}), flush=True)
        return
    if cfg.claude_model != "claude-sonnet-4-6":
        raise RuntimeError("Probe pricing has not been configured for this model")
    async with AsyncAnthropic(api_key=cfg.anthropic_api_key.get_secret_value(), max_retries=0, timeout=30) as client:
        phase = "model_access"
        await client.models.retrieve(cfg.claude_model)
        # A tiny real request also verifies billing access, unlike listing models alone.
        phase = "inference"
        response = await client.messages.create(model=cfg.claude_model, max_tokens=32,
                      messages=[{"role": "user", "content": "Responda somente OK. Verificação de conectividade da API."}])
    usage = response.usage
    # The operator verifies these rates against official docs before running this probe.
    # Probe is limited to Sonnet 4.6, with at most 32 generated tokens and no retries.
    cost = (usage.input_tokens*3+usage.output_tokens*15)/1_000_000
    async with Session() as session:
        session.add(Audit(action="CLAUDE_HEALTHCHECK", actor="deployment", details={"model": cfg.claude_model,
                    "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": cost}))
        await session.commit()
    print(json.dumps({"event": "claude_credentials_verified", "status": "PASS", "model": cfg.claude_model,
                      "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": cost}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except APIStatusError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error", body)
        error = error if isinstance(error, dict) else {}
        message = str(error.get("message", ""))
        secret = settings().anthropic_api_key.get_secret_value()
        if secret:
            message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"sk-ant-[A-Za-z0-9_-]+", "[REDACTED]", message)[:600]
        print(json.dumps({"event": "claude_credentials_verified", "status": "FAIL", "http_status": exc.status_code,
                          "error_type": type(exc).__name__, "phase": phase,
                          "provider_error": error.get("type"), "provider_message": message}), flush=True)
        raise SystemExit(1) from None
    except Exception as exc:
        print(json.dumps({"event": "claude_credentials_verified", "status": "FAIL", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
