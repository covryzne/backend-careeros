import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import BACKEND_ROOT, settings
from app.models.foundation import CandidateDocument, CVImport
from app.schemas.cv_import import CVFields
from app.services import documents, profile
from app.services.audit import audit
from app.services.idempotency import fingerprint

ERRORS = {
    "PDF_ENCRYPTED": "PDF dilindungi password. Gunakan salinan PDF tanpa password.",
    "PDF_PAGE_LIMIT": "CV maksimal 20 halaman.",
    "PDF_TEXT_LIMIT": "Teks PDF terlalu panjang untuk diproses sebagai CV.",
    "PDF_NO_TEXT": "PDF tidak memiliki teks yang dapat dibaca. Gunakan PDF berbasis teks; OCR untuk hasil scan belum tersedia.",
    "PDF_INVALID": "PDF tidak dapat dibaca. Coba ekspor ulang dari dokumen asli.",
}


def parse_pdf(content):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.services.cv_parser"],
            check=False,
            input=content,
            capture_output=True,
            timeout=15,
            cwd=BACKEND_ROOT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            422, "Parsing PDF melewati batas waktu. Coba CV yang lebih sederhana."
        ) from None
    if result.returncode and not result.stdout.strip():
        raise HTTPException(503, "Parser PDF belum tersedia. Periksa dependensi backend.")
    try:
        data = json.loads(result.stdout)
        if result.returncode or data.get("error"):
            raise HTTPException(422, ERRORS.get(data.get("error"), "PDF tidak dapat diproses."))
        data["fields"] = CVFields.model_validate(data["fields"]).model_dump(
            mode="json", exclude_unset=True
        )
        return data
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            422, "Hasil PDF belum dapat diubah menjadi form. Coba ekspor ulang CV."
        ) from None


def owned_import(db, actor, import_id, *, lock=False):
    actor.require("profile:read")
    query = select(CVImport).where(CVImport.id == import_id, CVImport.owner_id == actor.owner_id)
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "CV import not found")
    return item


def detail(item):
    return {
        "id": str(item.id),
        "file_name": item.file_name,
        "status": item.status,
        "source_profile_version": item.source_profile_version,
        "document_id": str(item.document_id) if item.document_id else None,
        **item.preview,
    }


def parse_document(db, actor, document_id):
    doc = documents.owned_document(db, actor, document_id)
    path = documents.document_path(doc)
    if not path.is_file():
        raise HTTPException(404, "Berkas CV tidak tersedia.")
    item = stage(db, actor, doc.original_file_name, doc.mime_type, path.read_bytes())
    item.document_id = doc.id
    preview = item.preview
    if doc.matching_snapshot is not None:
        saved = doc.matching_snapshot
        preview = {
            **preview,
            "fields": {
                **saved,
                "profile": {**preview["fields"]["profile"], **saved["profile"]},
            },
            "warnings": [
                *preview["warnings"],
                "Data profesional menggunakan revisi CV yang terakhir disimpan; kolom pribadi dibaca dari PDF.",
            ],
        }
    item.preview = {**preview, "document_revision": doc.snapshot_revision}
    db.flush()
    return item


def review(db, actor, import_id, payload):
    actor.require("profile:write", approval=True)
    item = owned_import(db, actor, import_id, lock=True)
    if item.status != "pending" or item.document_id is None:
        raise HTTPException(409, "Import tidak dapat disimpan sebagai versi CV.")
    doc = documents.owned_document(db, actor, item.document_id)
    db.refresh(doc, with_for_update=True)
    if doc.snapshot_revision != payload.expected_revision:
        raise HTTPException(
            409, "Data CV sudah berubah. Buka kembali hasil parsing sebelum menyimpan."
        )
    from app.services.cv_matching import snapshot

    doc.matching_snapshot = snapshot(payload)
    doc.snapshot_revision += 1
    item.preview = {**item.preview, "document_revision": doc.snapshot_revision}
    audit(db, actor, "cv_matching_reviewed", "document", doc.id, version=doc.snapshot_revision)
    db.flush()
    return doc


def stage(db, actor, name, mime, content):
    actor.require("profile:write", approval=True)
    if (
        mime != "application/pdf"
        or not name.lower().endswith(".pdf")
        or not content.startswith(b"%PDF-")
    ):
        raise HTTPException(422, "Pilih file PDF yang valid.")
    if len(content) > min(settings.document_max_bytes, 10 * 1024 * 1024):
        raise HTTPException(413, "CV maksimal 10 MiB.")
    count = (
        db.scalar(
            select(func.count())
            .select_from(CVImport)
            .where(
                CVImport.owner_id == actor.owner_id,
                CVImport.created_at >= datetime.now(UTC) - timedelta(hours=1),
            )
        )
        or 0
    )
    if count >= 20:
        raise HTTPException(429, "Batas impor CV tercapai. Coba lagi nanti.")
    parsed = parse_pdf(content)
    current = profile.load_profile(db, actor)
    item = CVImport(
        owner_id=actor.owner_id,
        storage_key=uuid.uuid4().hex + ".pdf",
        file_name=re.split(r"[/\\]", name)[-1][:255],
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        file_size_bytes=len(content),
        source_profile_version=current.profile_version if current else None,
        preview=parsed,
    )
    root = settings.document_storage_dir.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = documents.document_path(item)
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        db.add(item)
        db.flush()
        audit(db, actor, "cv_import_parsed", "cv_import", item.id)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return item


def apply(db, actor, import_id, payload):
    actor.require("profile:write", approval=True)
    version_fields = documents.next_version(db, actor, None)
    item = owned_import(db, actor, import_id, lock=True)
    digest = fingerprint(payload.model_dump(mode="json"))
    if item.status == "applied":
        if item.applied_hash != digest:
            raise HTTPException(409, "Import sudah diterapkan dengan data berbeda.")
        return profile.load_profile(db, actor)
    if item.status != "pending":
        raise HTTPException(409, "Import tidak lagi aktif.")
    if not documents.document_path(item).is_file():
        raise HTTPException(404, "Berkas CV tidak tersedia. Unggah ulang PDF.")
    current = profile.load_profile(db, actor, lock=True)
    doc = (
        documents.owned_document(db, actor, item.document_id)
        if item.document_id
        else (
            db.scalar(
                select(CandidateDocument).where(
                    CandidateDocument.profile_id == current.id,
                    CandidateDocument.checksum_sha256 == item.checksum_sha256,
                    CandidateDocument.deleted_at.is_(None),
                )
            )
            if current
            else None
        )
    )
    current = profile.merge_cv_fields(db, actor, payload, document_added=doc is None)
    if doc is None:
        doc = CandidateDocument(
            **version_fields,
            profile_id=current.id,
            name=item.file_name[:200],
            original_file_name=item.file_name,
            storage_key=item.storage_key,
            file_size_bytes=item.file_size_bytes,
            checksum_sha256=item.checksum_sha256,
        )
        db.add(doc)
        db.flush()
    item.status = "applied"
    item.applied_hash = digest
    item.document_id = doc.id
    if doc.matching_snapshot is None:
        from app.services.cv_matching import snapshot

        doc.matching_snapshot = snapshot(payload, require_professional=False)
        if doc.matching_snapshot is not None:
            doc.snapshot_revision = 1
    audit(db, actor, "cv_import_applied", "cv_import", item.id, version=current.profile_version)
    db.flush()
    return profile.load_profile(db, actor)


def cancel(db, actor, import_id):
    actor.require("profile:write", approval=True)
    item = owned_import(db, actor, import_id, lock=True)
    if item.status == "applied":
        raise HTTPException(409, "CV sudah disimpan ke profil.")
    if item.status != "cancelled":
        item.status = "cancelled"
        item.preview = {
            "fields": {"profile": {}, "skills": [], "experiences": [], "projects": []},
            "warnings": [],
            "parser_version": "local-pdf-v1",
        }
        audit(db, actor, "cv_import_cancelled", "cv_import", item.id)
    db.flush()
    return item


def cleanup_redundant_file(db, actor, import_id):
    item = owned_import(db, actor, import_id)
    if item.status != "applied" or not item.document_id:
        return
    key = db.scalar(
        select(CandidateDocument.storage_key).where(CandidateDocument.id == item.document_id)
    )
    if key and key != item.storage_key:
        try:
            documents.document_path(item).unlink(missing_ok=True)
        except OSError:
            import logging

            logging.getLogger(__name__).warning("Private redundant CV file cleanup will need retry")
