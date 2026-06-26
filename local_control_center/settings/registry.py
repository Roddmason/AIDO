"""Setting descriptor registry for the AIDO settings plane: keys, types, defaults, and validation.

Defines the ``SettingDescriptor`` dataclass, the Phase-1 ``REGISTRY`` of three wired settings
(autonomy level, sandbox profile, budget cap), and ``validate_value`` / ``descriptor_for``
helpers that enforce type constraints at trust boundaries before any persistence occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SettingDescriptor:
    """Immutable descriptor for a single platform setting key."""

    key: str
    section: str
    project_section: str | None
    type: str  # "enum" | "number" | "string"
    default: Any
    enum: tuple[str, ...] | None = None
    label_key: str = ""


REGISTRY: list[SettingDescriptor] = [
    SettingDescriptor(
        key="autonomy.level",
        section="autonomy",
        project_section="security",
        type="enum",
        default="guided",
        enum=("guided", "recommended", "autonomous"),
        label_key="app.settings.autonomy.level",
    ),
    SettingDescriptor(
        key="security.sandboxProfileId",
        section="security",
        project_section="security",
        type="string",
        default="",
        label_key="app.settings.security.sandboxProfile",
    ),
    SettingDescriptor(
        key="budget.maxCostUsd",
        section="costs",
        project_section="budget",
        type="number",
        default=0,
        label_key="app.settings.budget.maxCostUsd",
    ),
]

_REGISTRY_BY_KEY: dict[str, SettingDescriptor] = {d.key: d for d in REGISTRY}


def descriptor_for(key: str) -> SettingDescriptor | None:
    """Return the descriptor for ``key``, or ``None`` if the key is not registered."""
    return _REGISTRY_BY_KEY.get(key)


def validate_value(descriptor: SettingDescriptor, value: Any) -> Any:
    """Validate and coerce ``value`` against ``descriptor``; raise ``ValueError`` on failure.

    - enum: value must be one of the declared members (string match).
    - number: value is coerced via ``float()``; raises ``ValueError`` if not numeric.
    - string: returned as-is (any string is valid).
    """
    if descriptor.type == "enum":
        if value not in (descriptor.enum or ()):
            raise ValueError(
                f"Invalid value {value!r} for {descriptor.key!r}; "
                f"must be one of {descriptor.enum!r}."
            )
        return value
    if descriptor.type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid numeric value {value!r} for {descriptor.key!r}."
            ) from exc
    # string: must be an actual str (reject dict/list/number)
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid value {value!r} for {descriptor.key!r}; expected a string."
        )
    return value
