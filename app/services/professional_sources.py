from app.services.agent_privacy import private_values, sanitize_text


def draft_sources(owner, *, max_chars=2000):
    if owner is None:
        return []
    private = private_values(owner)
    items = []

    def add(kind, identity, label, text):
        text = sanitize_text(text, private).strip()
        if text:
            items.append(
                {
                    "id": str(identity),
                    "kind": kind,
                    "label": sanitize_text(label, private),
                    "text": text[:max_chars],
                }
            )

    add(
        "profile",
        owner.id,
        "Ringkasan profesional",
        "\n".join(value for value in (owner.headline, owner.professional_summary) if value),
    )
    for item in owner.experiences:
        add(
            "experience",
            item.id,
            item.role + " · " + item.company,
            item.role + " at " + item.company + ": " + (item.description or ""),
        )
    for item in owner.projects:
        add(
            "project",
            item.id,
            item.name,
            item.name + (": " + item.role if item.role else "") + ": " + (item.description or ""),
        )
    for skill in owner.skills:
        add("skill", skill.id, skill.name, skill.name)
    return items
