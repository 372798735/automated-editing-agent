"""日志与运行上下文。

设计目标：
1. 每条日志带 trace_id（= project_id），方便按项目排查流水线问题；
2. 纯标准库实现，不引入 loguru 依赖；
3. 控制台输出精简、文件输出完整（workspace/logs/*.log）。
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("autocut_trace_id", default="-")

_LOGGER_NAME = "autocut"
_configured = False


class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get()
        return True


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"[{ts}] {record.levelname:<5} ({record.trace_id}) {record.getMessage()}"


class _JsonFileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "-"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: Path | None = None, verbose: bool = False) -> logging.Logger:
    """初始化日志。重复调用是安全的（幂等）。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(_ConsoleFormatter())
    console.addFilter(_TraceFilter())
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "autocut.log", encoding="utf-8")
        file_handler.setFormatter(_JsonFileFormatter())
        file_handler.addFilter(_TraceFilter())
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    _configured = True
    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{suffix}" if suffix else _LOGGER_NAME)


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)
