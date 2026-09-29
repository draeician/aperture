"""Public component and session description types."""

from __future__ import annotations

from dataclasses import dataclass

IMPLEMENTATION_VERSION = "0.2.0"
INTEGRATION_API_VERSION = "1.0"

_CAPABILITIES = (
    "admission",
    "balance",
    "generic_source_refs",
    "render",
    "render_restriction",
    "stable_item_keys",
    "structured_render_manifest",
)


@dataclass(frozen=True)
class ComponentDescription:
    role: str
    implementation: str
    protocol_version: str
    implementation_version: str
    tokenizer_id: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class SessionDescriptor:
    implementation_version: str
    protocol_version: str
    tokenizer_id: str
    policy_id: str | None
    policy_version: str | None


def describe_component(tokenizer_id: str = "whitespace") -> ComponentDescription:
    """Describe the reusable Aperture component without inspecting internals."""

    return ComponentDescription(
        role="context",
        implementation="aperture",
        protocol_version=INTEGRATION_API_VERSION,
        implementation_version=IMPLEMENTATION_VERSION,
        tokenizer_id=tokenizer_id,
        capabilities=_CAPABILITIES,
    )
