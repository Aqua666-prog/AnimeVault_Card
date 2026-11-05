import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

from cardlegends.infrastructure.diagnostics import failure_details

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Не сериализуем args/traceback сторонних библиотек: они могут содержать DSN/токен.
        own_event = record.name.startswith("cardlegends")
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage() if own_event else "external_log",
            "correlation_id": correlation_id.get(),
        }
        error = record.exc_info[1] if record.exc_info else None
        if error is None and isinstance(record.args, tuple):
            error = next((arg for arg in record.args if isinstance(arg, BaseException)), None)
        if error is not None:
            details = failure_details(error)
            payload.update(details)
            if not own_event:
                # Включаем безопасную причину даже в панели, показывающей только event.
                payload["event"] = (
                    f"external_log error_type={details['error_type']} "
                    f"cause_type={details['cause_type']} reason={details['reason']}: "
                    f"{details['technical_reason']} Действие: {details['hint']}"
                )
        for key in (
            "error_type",
            "cause_type",
            "phase",
            "reason",
            "technical_reason",
            "hint",
            "sqlstate",
            "errno",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
