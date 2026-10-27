from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.models.foundation import Draft, DraftVersion, ProfileProposal
from app.schemas.profile import ProfileUpdate
from app.services.audit import audit
from app.services.job_workflows import get_job
from app.services.profile import check_version, load_profile


def eligible_facts(profile):
    return [
        fact
        for fact in profile.facts
        if fact.confirmation_status == "user_confirmed" and fact.evidence_status != "unsupported"
    ]


def create_draft(db, actor, args, draft_type):
    actor.require("drafts:write")
    profile = load_profile(db, actor, lock=True)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    check_version(profile, args.source_profile_version, actor)
    job = get_job(db, actor, args.job_id)
    from app.services.professional_sources import draft_sources

    choices = draft_sources(profile)
    sources = {item["id"]: item for item in choices}
    selected_ids = list(dict.fromkeys(str(value) for value in args.source_ids))
    legacy = False
    if not selected_ids:
        from app.services.draft_composer import relevant_sources

        selected_ids = [item["id"] for item in relevant_sources(choices, job)]
    # Compatibility only for historical API callers; facts are not offered by the UI/tools.
    if selected_ids and any(identity not in sources for identity in selected_ids):
        old = {
            str(fact.id): {
                "id": str(fact.id),
                "kind": "fact",
                "text": fact.claim,
                "evidence_status": fact.evidence_status,
            }
            for fact in eligible_facts(profile)
        }
        sources.update(old)
        legacy = any(identity in old for identity in selected_ids)
    if not selected_ids or any(identity not in sources for identity in selected_ids):
        raise HTTPException(
            422,
            "Lengkapi profil profesional, skill, project, atau experience sebagai sumber draft.",
        )
    selected = [sources[identity] for identity in selected_ids]
    from app.services.draft_composer import compose
    from app.services.job_content import snapshot

    content = compose(job, selected, draft_type, args.language, profile)
    draft = Draft(
        owner_id=actor.owner_id,
        job_id=args.job_id,
        source_profile_version=profile.profile_version,
        draft_type=draft_type,
        content=content,
        job_snapshot=snapshot(job),
        citations=[
            {
                "source_id": item["id"],
                "kind": "candidate_" + item["kind"],
                "profile_version": profile.profile_version,
            }
            for item in selected
        ],
        review_flags=["evidence_review_required"]
        if legacy and any(item.get("evidence_status") != "supported" for item in selected)
        else [],
    )
    db.add(draft)
    db.flush()
    db.add(DraftVersion(draft_id=draft.id, version=1, content=content))
    audit(db, actor, "draft_created", "draft", draft.id, version=1)
    db.flush()
    return draft


def owned_draft(db, actor, draft_id, lock=False):
    actor.require("drafts:read")
    query = select(Draft).where(
        Draft.id == draft_id, Draft.owner_id == actor.owner_id, Draft.deleted_at.is_(None)
    )
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "Draft not found")
    return item


def draft_detail(db, actor, draft):
    from app.services.job_content import snapshot

    try:
        job = get_job(db, actor, draft.job_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        job = None
    profile = load_profile(db, actor)
    versions = db.scalars(
        select(DraftVersion).where(DraftVersion.draft_id == draft.id).order_by(DraftVersion.version)
    ).all()
    return {
        "id": str(draft.id),
        "job_id": str(draft.job_id),
        "draft_type": draft.draft_type,
        "content": draft.content,
        "version": draft.version,
        "source_profile_version": draft.source_profile_version,
        "citations": draft.citations,
        "review_flags": draft.review_flags,
        "is_stale": profile is None or profile.profile_version != draft.source_profile_version,
        "versions": [{"version": v.version, "content": v.content} for v in versions],
        "job": {key: job[key] for key in ("id", "title", "company")} if job else None,
        "job_snapshot": draft.job_snapshot,
        "is_job_stale": bool(
            draft.job_snapshot and (job is None or draft.job_snapshot != snapshot(job))
        ),
        "composition_method": "local_grounded_template",
    }


def edit_draft(db, actor, draft_id, content, expected):
    actor.require("drafts:write")
    if not content.strip():
        raise HTTPException(422, "Draft content cannot be blank")
    draft = owned_draft(db, actor, draft_id, lock=True)
    if draft.version != expected:
        raise HTTPException(409, "Draft changed; reload before editing")
    if draft.content != content:
        draft.content = content
        draft.version += 1
        draft.review_flags = list(set(draft.review_flags + ["manual_content_review_required"]))
        db.add(DraftVersion(draft_id=draft.id, version=draft.version, content=content))
        audit(db, actor, "draft_edited", "draft", draft.id, version=draft.version)
    db.flush()
    return draft


def delete_draft(db, actor, draft_id, expected):
    actor.require("drafts:write", approval=True)
    draft = db.scalar(
        select(Draft)
        .where(Draft.id == draft_id, Draft.owner_id == actor.owner_id)
        .with_for_update()
    )
    if draft is None:
        raise HTTPException(404, "Draft not found")
    if draft.deleted_at is not None:
        if expected == draft.version - 1:
            return
        raise HTTPException(409, "Draft changed; reload before deleting")
    if draft.version != expected:
        raise HTTPException(409, "Draft changed; reload before deleting")
    draft.deleted_at = datetime.now(UTC)
    draft.version += 1
    audit(db, actor, "draft_deleted", "draft", draft.id, version=draft.version)
    db.flush()


def propose_profile(db, actor, args):
    actor.require("profile:propose")
    profile = load_profile(db, actor)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    check_version(profile, args.source_profile_version, actor)
    if not set(args.changes) <= set(ProfileUpdate.model_fields):
        raise HTTPException(422, "Unknown proposal fields")
    values = ProfileUpdate.model_validate(args.changes).model_dump(mode="json", exclude_unset=True)
    proposal = ProfileProposal(
        owner_id=actor.owner_id, source_profile_version=profile.profile_version, changes=values
    )
    db.add(proposal)
    db.flush()
    audit(db, actor, "profile_update_proposed", "proposal", proposal.id)
    return {
        "id": str(proposal.id),
        "status": "pending",
        "source_profile_version": profile.profile_version,
        "changes": values,
    }
