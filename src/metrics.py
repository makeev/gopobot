import hashlib
import json
import logging
from time import perf_counter
from typing import Any

logger = logging.getLogger("gopobot.metrics")


def anonymous_id(value: str | int) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def record_event(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


class OperationTimer:
    def __init__(self, operation: str, **fields: Any):
        self.operation = operation
        self.fields = fields
        self.started = 0.0

    def __enter__(self) -> "OperationTimer":
        self.started = perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        record_event(
            "operation",
            operation=self.operation,
            status="error" if exc else "ok",
            duration_ms=round((perf_counter() - self.started) * 1000),
            error_type=type(exc).__name__ if exc else None,
            **self.fields,
        )
