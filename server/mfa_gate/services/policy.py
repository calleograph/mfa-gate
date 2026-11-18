"""Выбор политики доступа. Правила по возрастанию priority, первое совпавшее применяется. Нет совпадений — MFA обязателен."""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..models import Group, Policy

DEFAULT_METHODS = ["push", "totp"]


@dataclass
class Decision:
    action: str                          # mfa | allow | deny
    methods: list[str] = field(default_factory=lambda: list(DEFAULT_METHODS))
    on_unavailable: str = "deny"
    policy: Policy | None = None


def _norm(names: Iterable[str]) -> set[str]:
    return {n.strip().lower() for n in names if n and n.strip()}


def effective_groups(local: Iterable[Group], directory_groups: Iterable[str], mapped: Iterable[Group]) -> set[str]:
    """Имена групп пользователя: локальные, группы каталога как есть (CN и DN) и группы MFA Gate, сопоставленные группе каталога."""
    directory = _norm(directory_groups)
    names = _norm(g.name for g in local) | directory
    names |= _norm(g.name for g in mapped if g.directory_group and g.directory_group.strip().lower() in directory)
    return names


def evaluate(policies: Iterable[Policy], resource_id: uuid.UUID | None, groups: set[str]) -> Decision:
    for policy in sorted((p for p in policies if p.enabled), key=lambda p: (p.priority, str(p.id))):
        if policy.resource_ids and (resource_id is None or str(resource_id) not in {str(r) for r in policy.resource_ids}):
            continue
        if policy.groups and not (_norm(policy.groups) & groups):
            continue
        if policy.exclude_groups and (_norm(policy.exclude_groups) & groups):
            continue
        methods = [m for m in (policy.methods or DEFAULT_METHODS) if m in ("push", "totp")] or list(DEFAULT_METHODS)
        return Decision(policy.action, methods, policy.on_unavailable or "deny", policy)
    return Decision("mfa")
