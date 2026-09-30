"""Generic reusable integration contract tests."""

from __future__ import annotations

from aperture import (
    Aperture,
    INTEGRATION_API_VERSION,
    IMPLEMENTATION_VERSION,
    Policy,
    Provenance,
    SourceClass,
    SourceRef,
    Submission,
    describe_component,
)


def _submission(
    source_id: str,
    content: str,
    *,
    restricted: bool = False,
    source_class: SourceClass = SourceClass.memory,
    role: str = "user",
) -> Submission:
    return Submission(
        source_class=source_class,
        content=content,
        provenance=Provenance(submitted_by="generic-test"),
        source_ref=SourceRef(source_system="external-memory", source_id=source_id),
        source_metadata={"opaque": source_id},
        render_restricted=restricted,
        application_key=f"app:{source_id}",
        role=role,
    )


def test_generic_source_ref_survives_admission_balance_and_render():
    kernel = Aperture(Policy(render_item_labels=False))
    submission = _submission("memory-1", "remember this")
    admission = kernel.submit(submission)
    kernel.balance()
    rendered = kernel.render()

    assert admission.accepted
    assert rendered.messages == [{"role": "user", "content": "remember this"}]
    assert rendered.manifest == [admission.item_id]
    entry = rendered.manifest_entries[0]
    assert entry.item_id == admission.item_id
    assert entry.source_ref == submission.source_ref
    assert entry.source_class == SourceClass.memory
    assert entry.role == "user"
    assert entry.token_count == 2
    assert entry.application_key == "app:memory-1"
    assert sum(item.token_count for item in rendered.manifest_entries) == rendered.total_tokens

    exported = kernel.end_session()
    item = next(row for row in exported.working_set if row["id"] == admission.item_id)
    assert item["source_ref"] == {"source_system": "external-memory", "source_id": "memory-1"}
    assert item["source_metadata"] == {"opaque": "memory-1"}


def test_first_class_render_restriction_is_admitted_but_not_rendered():
    kernel = Aperture(Policy(render_item_labels=False))
    visible = kernel.submit(_submission("visible", "visible memory"))
    restricted = kernel.submit(_submission("restricted", "private memory", restricted=True))
    kernel.balance()
    rendered = kernel.render()

    assert visible.accepted and restricted.accepted
    assert visible.item_id in rendered.manifest
    assert restricted.item_id not in rendered.manifest
    assert all(entry.source_ref != SourceRef("external-memory", "restricted") for entry in rendered.manifest_entries)
    assert all("private memory" not in message["content"] for message in rendered.messages)
    assert restricted.item_id in kernel._working_set
    assert kernel._working_set.get(restricted.item_id).render_restricted is True
    assert kernel.explain.absence(restricted.item_id) == {"reason": "render_restricted"}


def test_structured_manifest_follows_actual_message_order_and_roles():
    kernel = Aperture(Policy(render_item_labels=False))
    memory = kernel.submit(_submission("memory", "memory", source_class=SourceClass.memory))
    system = kernel.submit(_submission("system", "system", source_class=SourceClass.system, role="system"))
    conversation = kernel.submit(_submission("conversation", "conversation", source_class=SourceClass.conversation))
    kernel.balance()
    rendered = kernel.render()

    assert [message["content"] for message in rendered.messages] == ["system", "conversation", "memory"]
    assert [entry.item_id for entry in rendered.manifest_entries] == [system.item_id, conversation.item_id, memory.item_id]
    assert [entry.source_ref.source_id for entry in rendered.manifest_entries if entry.source_ref] == ["system", "conversation", "memory"]


def test_component_and_session_descriptors_are_public_and_generic():
    description = describe_component("whitespace")
    assert description.role == "context"
    assert description.implementation == "aperture"
    assert description.protocol_version == INTEGRATION_API_VERSION == "1.0"
    assert description.implementation_version == IMPLEMENTATION_VERSION == "0.2.0"
    assert description.tokenizer_id == "whitespace"
    assert "generic_source_refs" in description.capabilities
    assert "render_restriction" in description.capabilities

    kernel = Aperture(Policy(tokenizer_id="whitespace", policy_id="host-context", policy_version="1", render_item_labels=False))
    session = kernel.session_descriptor()
    assert session.implementation_version == "0.2.0"
    assert session.protocol_version == "1.0"
    assert session.tokenizer_id == "whitespace"
    assert session.policy_id == "host-context"
    assert session.policy_version == "1"


def test_generic_path_does_not_require_mneme_named_arguments():
    kernel = Aperture(Policy(render_item_labels=False))
    admission = kernel.submit(
        Submission(
            source_class=SourceClass.memory,
            content="generic memory",
            provenance=Provenance(submitted_by="generic-test"),
            source_ref=SourceRef(source_system="anything", source_id="record-7"),
            source_metadata={"kind": "fact"},
            render_restricted=False,
        )
    )
    kernel.balance()
    rendered = kernel.render()
    assert admission.accepted
    assert rendered.manifest_entries[0].source_ref == SourceRef("anything", "record-7")


def test_legacy_mneme_restriction_remains_compatible():
    kernel = Aperture(Policy())
    admission = kernel.submit(
        Submission(
            source_class=SourceClass.mneme_import,
            content="legacy restricted",
            provenance=Provenance(submitted_by="legacy-test"),
            mneme_meta={"render_restricted": True, "legacy": "opaque"},
        )
    )
    rendered = kernel.render()
    assert admission.accepted
    assert admission.item_id not in rendered.manifest
    assert kernel._working_set.get(admission.item_id).render_restricted is False
    assert kernel.explain.absence(admission.item_id) == {"reason": "render_restricted"}
