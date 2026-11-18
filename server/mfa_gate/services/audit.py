"""Журнал безопасности. Значения ключей, похожих на секреты, не записываются никогда."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent

_SECRET_MARKERS = ("password", "secret", "token", "code", "otp", "signature", "key", "challenge")


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if any(m in k.lower() for m in _SECRET_MARKERS) else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def audit(session: AsyncSession, event: str, *, actor: str = "system", target: str | None = None, ip: str | None = None,
          result: str = "ok", severity: str = "info", **details: Any) -> None:
    """Добавляет запись в текущую транзакцию: событие фиксируется вместе с изменением, которое оно описывает."""
    session.add(AuditEvent(event=event, actor=actor, target=target, ip=ip, result=result, severity=severity,
                           details=scrub({k: (str(v) if not isinstance(v, (int, float, bool, list, dict, type(None))) else v)
                                          for k, v in details.items()})))
