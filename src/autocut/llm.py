"""OpenAI 兼容大模型客户端（仅标准库 urllib）。

为什么不用 openai SDK：MVP 要求零必装依赖。接口层面完全兼容
`POST {base_url}/chat/completions`，因此 DeepSeek / 通义 / 智谱 / 私有化 vLLM 都可直接接。

关键工程点：
- 结构化输出：要求模型返回 JSON，并用 `extract_json` 做容错解析（处理 ```json 包裹等）；
- 失败可控：任何网络/解析错误都降级为 None，由上层回退到模板或启发式通道；
- 可观测：记录 token 用量与耗时，便于后续接入监控。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import LLMConfig
from .logging_setup import get_logger

logger = get_logger("llm")


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMUnavailable(Exception):
    """未配置 API Key 或模型不可用。"""


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取第一个 JSON 对象（容错 ```json 代码块与前后废话）。"""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = [line for line in candidate.splitlines() if not line.strip().startswith("```")]
        candidate = "\n".join(lines).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


class OpenAICompatibleClient:
    """极简 chat/completions 客户端。"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.api_key = config.resolve_api_key()
        if not self.api_key:
            raise LLMUnavailable(f"环境变量 {config.api_key_env} 未配置 API Key")

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        json_mode: bool = True,
        timeout: int | None = None,
    ) -> LLMResponse | None:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 2):
            started = time.time()
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.config.timeout_sec) as response:
                    payload_text = response.read().decode("utf-8", errors="replace")
                data = json.loads(payload_text)
                choices = data.get("choices") or []
                if not choices:
                    raise ValueError(f"响应缺少 choices: {payload_text[:200]}")
                content = choices[0].get("message", {}).get("content", "")
                return LLMResponse(
                    text=content,
                    model=data.get("model", payload["model"]),
                    latency_ms=int((time.time() - started) * 1000),
                    usage=data.get("usage", {}),
                    raw=data,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning("LLM 调用失败（第 %s 次）: %s", attempt, exc)
                if attempt <= self.config.max_retries:
                    time.sleep(min(2 ** attempt, 8))
        logger.error("LLM 调用最终失败: %s", last_error)
        return None

    # ------------------------------------------------------------------
    def chat_json(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs) -> dict[str, Any] | None:
        response = self.chat(messages, model=model, **kwargs)
        if response is None:
            return None
        parsed = extract_json(response.text)
        if parsed is None:
            logger.warning("模型输出无法解析为 JSON: %s", response.text[:200])
        return parsed


def build_client(config: LLMConfig) -> OpenAICompatibleClient | None:
    """安全构造：未配置 Key 时返回 None，而不是抛异常。"""
    try:
        return OpenAICompatibleClient(config)
    except LLMUnavailable as exc:
        logger.info("LLM 未启用：%s", exc)
        return None
