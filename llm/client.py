"""AWS Bedrock client for single-call NL→SQL agent."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config as BotoConfig

from config.settings import settings

logger = logging.getLogger(__name__)

_client = None


def init_client() -> None:
    global _client
    cfg = settings.bedrock
    boto_cfg = BotoConfig(
        read_timeout=cfg.read_timeout_sec,
        connect_timeout=cfg.connect_timeout_sec,
        retries={"max_attempts": 0},
    )
    _client = boto3.client("bedrock-runtime", region_name=cfg.region, config=boto_cfg)
    logger.info("Bedrock client initialized (region=%s, model=%s)", cfg.region, cfg.model_id)


def invoke(
    messages: List[Dict[str, str]],
    system_prompt: str,
    model_id: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    if _client is None:
        raise RuntimeError("LLM client not initialized")
    mid = model_id or settings.bedrock.model_id
    mt = max_tokens or settings.bedrock.max_tokens
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": mt,
        "temperature": temperature,
        "system": system_prompt,
        "messages": messages,
    })
    response = _client.invoke_model(
        modelId=mid,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    usage = result.get("usage", {})
    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    return {
        "text": text,
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "model_id": mid,
    }
