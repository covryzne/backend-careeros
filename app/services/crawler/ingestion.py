"""Transactional worker event ingestion; no browser calls in this module."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from app.core.security import ActorContext
from app.models.crawler import CrawlRunItem, CrawlRunSource
from app.models.workflow import Job, JobSource
from app.services.crawler.dates import evaluate
from app.services.crawler.events import append as append_event
from app.services.jobs.identity import normalize_url, url_hash
from app.services.shared.audit import audit
from app.services.shared.idempotency import fingerprint, lock_owner

HOSTS = {
    "linkedin": {"www.linkedin.com", "id.linkedin.com", "linkedin.com"},
    "jobstreet": {"id.jobstreet.com"},
    "glints": {"glints.com", "www.glints.com"},
    "indeed": {"id.indeed.com"},
    "dealls": {"dealls.com", "www.dealls.com"},
    "kalibrr": {"www.kalibrr.com", "kalibrr.com"},
    "techinasia": {"www.techinasia.com", "techinasia.com"},
}
NAMES = {
    "linkedin": "LinkedIn",
    "jobstreet": "JobStreet",
    "glints": "Glints",
    "indeed": "Indeed",
    "dealls": "Dealls",
    "kalibrr": "Kalibrr",
    "techinasia": "Tech in Asia",
}


def identity(platform, external_id):
    return fingerprint([platform, external_id])


def find_existing(db, owner_id, canonical, *, source_id=None, external_id=None):
    digest = url_hash(canonical)
    conditions = [
        Job.normalized_url_hash == digest,
        Job.canonical_url == canonical,
        Job.original_url == canonical,
    ]
    if source_id is not None and external_id:
        conditions.append((Job.source_id == source_id) & (Job.external_job_id == external_id))
    matches = list(db.scalars(select(Job).where(Job.owner_id == owner_id, or_(*conditions))).all())
    # Old rows intentionally have NULL hashes. Reconcile without rewriting or deleting them.
    for job in db.scalars(
        select(Job).where(Job.owner_id == owner_id, Job.normalized_url_hash.is_(None))
    ):
        try:
            if (
                (job.canonical_url or job.original_url)
                and normalize_url(job.canonical_url or job.original_url) == canonical
                and all(row.id != job.id for row in matches)
            ):
                matches.append(job)
        except ValueError:
            continue
    if len(matches) > 1:
        raise ValueError("Multiple existing jobs share this identity; manual review required")
    return matches[0] if matches else None


def persist_job(db, run, source, item, data):
    if item.status in {"new", "duplicate", "rejected", "filtered_date", "date_unknown"}:
        return
    try:
        title, company = data.get("title"), data.get("company")
        description = data.get("description")
        if not isinstance(title, str) or not title.strip() or len(title) > 240:
            raise ValueError("Missing/invalid title")
        if not isinstance(company, str) or not company.strip() or len(company) > 240:
            raise ValueError("Missing/invalid company; no placeholder company was created")
        if not isinstance(description, str) or not description.strip() or len(description) > 200000:
            raise ValueError("Missing/invalid full description")
        url = data.get("job_url", "")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in HOSTS[source.platform] or parts.port:
            raise ValueError("Unexpected portal URL")
        canonical = normalize_url(url)
        external = str(data.get("external_id", ""))
        if external != item.external_id or data.get("portal") != source.platform:
            raise ValueError("Detail identity does not match the discovered card")
        item.title = title.strip()
        for field, limit in (("location", 240), ("salary", 200), ("requirements", 50000)):
            if data.get(field) is not None and (
                not isinstance(data[field], str) or len(data[field]) > limit
            ):
                raise ValueError(f"Invalid {field} length/type")
        check, posted = evaluate(run, source.platform, data.get("posted_at_raw"))
        item.provenance = {
            **item.provenance,
            "date_check": check,
            "posting_date": posted.isoformat() if posted else None,
        }
        if check == "unknown":
            source.jobs_date_unknown += 1
        if check in {"outside_range", "unknown"}:
            item.status = "filtered_date" if check == "outside_range" else "date_unknown"
            item.error = (
                f"Tanggal posting {posted.isoformat()} di luar rentang {run.config['days']} hari."
                if posted
                else "Tanggal posting tidak bisa dipastikan; dilewati sesuai pengaturan."
            )
            source.jobs_filtered += 1
            return
        actor = ActorContext(run.owner_id, "system")
        lock_owner(db, actor)
        sources = db.scalars(
            select(JobSource).where(
                JobSource.owner_id == run.owner_id, JobSource.name.ilike(NAMES[source.platform])
            )
        ).all()
        if len(sources) > 1:
            raise ValueError("Ambiguous existing source names require review")
        job_source = sources[0] if sources else None
        if job_source is None:
            job_source = JobSource(
                owner_id=run.owner_id,
                name=NAMES[source.platform],
                source_type="crawler",
                base_url=f"https://{parts.hostname}",
            )
            db.add(job_source)
            db.flush()
        existing = find_existing(
            db,
            run.owner_id,
            canonical,
            source_id=job_source.id,
            external_id=external if len(external) <= 255 else None,
        )
        if existing:
            item.status, item.job_id = "duplicate", existing.id
            source.jobs_duplicate += 1
            # Preserve tombstones, personal statuses, source content, notes and applications.
            return
        job = Job(
            owner_id=run.owner_id,
            source_id=job_source.id,
            external_job_id=external if len(external) <= 255 else None,
            title=title.strip(),
            company=company.strip(),
            canonical_url=canonical,
            normalized_url_hash=url_hash(canonical),
            original_url=url,
            description=description,
            requirements=data.get("requirements"),
            location=data.get("location"),
            salary_text=data.get("salary"),
            posting_date=posted,
            status="new",
            discovered_at=datetime.now(UTC),
        )
        db.add(job)
        db.flush()
        item.job_id, item.status = job.id, "new"
        source.jobs_new += 1
        audit(db, actor, "crawler_job_created", "job", job.id)
    except (ValueError, TypeError) as exc:
        item.status, item.error = "rejected", str(exc)
        source.jobs_rejected += 1


def apply_event(db, run, platform, keyword, event):
    source = db.scalar(
        select(CrawlRunSource).where(
            CrawlRunSource.run_id == run.id, CrawlRunSource.platform == platform
        )
    )
    if event.get("platform", platform) != platform:
        raise RuntimeError("Unexpected worker event platform")
    kind = event.get("type")
    if kind == "browser_ready":
        append_event(db, run, "Browser siap; memulai pencarian.", platform=platform)
    elif kind == "listing_started":
        append_event(db, run, f"Membuka halaman/batch {event['page']}.", platform=platform)
    elif kind == "source_started":
        source.status = "running"
        source.started_at = source.started_at or datetime.now(UTC)
        append_event(db, run, f"Mencari lowongan untuk keyword: {keyword}.", platform=platform)
    elif kind == "page":
        source.pages_completed += 1
        for ref in event["references"]:
            key = identity(platform, str(ref["external_id"]))
            existing = db.scalar(
                select(CrawlRunItem).where(
                    CrawlRunItem.run_id == run.id,
                    CrawlRunItem.platform == platform,
                    CrawlRunItem.identity_key == key,
                )
            )
            if existing is None:
                if source.jobs_found >= run.config["max_jobs_per_platform"]:
                    raise RuntimeError("Crawler exceeded per-platform discovery cap")
                db.add(
                    CrawlRunItem(
                        run_id=run.id,
                        platform=platform,
                        identity_key=key,
                        external_id=str(ref["external_id"]),
                        keyword=keyword,
                        url=ref["url"],
                        title=ref["title"],
                        provenance={
                            "search_page": ref["search_page"],
                            "discovered_from": ref["discovered_from"],
                        },
                    )
                )
                source.jobs_found += 1
                label = ref["title"] or f"ID {ref['external_id']}"
                append_event(db, run, f"Ditemukan: {label[:240]}.", platform=platform)
        db.flush()
        append_event(
            db,
            run,
            f"Halaman/batch {event.get('page', source.pages_completed)}: "
            f"{len(event['references'])} kartu baru ditemukan.",
            platform=platform,
        )
    elif kind in {"detail_started", "detail_error", "job"}:
        data = event.get("job", {})
        external = str(data.get("external_id") if kind == "job" else event["external_id"])
        item = db.scalar(
            select(CrawlRunItem).where(
                CrawlRunItem.run_id == run.id,
                CrawlRunItem.platform == platform,
                CrawlRunItem.identity_key == identity(platform, external),
            )
        )
        if item is None:
            raise RuntimeError("Detail event without a discovered card")
        if kind == "detail_started" and not item.provenance.get("attempted"):
            source.detail_attempts += 1
            item.provenance = {**item.provenance, "attempted": True}
            label = item.title or f"ID {external}"
            append_event(db, run, f"Mengambil detail: {label[:240]}.", platform=platform)
        elif kind == "detail_error":
            if item.status == "discovered":
                item.status, item.error = "failed", str(event["status"])[:100]
            source.error = str(event["status"])[:100]
            append_event(
                db,
                run,
                f"Detail tidak berhasil: {(item.title or f'ID {external}')[:200]} ({item.error}).",
                platform=platform,
                level="WARN",
            )
        elif kind == "job":
            item.provenance = {
                **item.provenance,
                "posted_at_raw": data.get("posted_at_raw"),
                "crawled_at": data.get("crawled_at"),
            }
            previous_status = item.status
            persist_job(db, run, source, item, data)
            if item.status != previous_status:
                outcome = {
                    "new": "Job baru tersimpan",
                    "duplicate": "Job sudah ada; duplikat dilewati",
                    "rejected": "Job ditolak validasi",
                    "filtered_date": "Dilewati filter tanggal",
                    "date_unknown": "Dilewati karena tanggal tidak diketahui",
                }.get(item.status, item.status)
                append_event(
                    db,
                    run,
                    f"{outcome}: {item.title[:200]}. " + (item.error or ""),
                    platform=platform,
                    level="WARN" if item.error else "INFO",
                )
    elif kind == "source_finished":
        if event["state"] != "completed":
            source.error = str(event.get("stop_reason") or "source_failed")[:100]
        has_error = source.error is not None or source.jobs_rejected > 0
        source.status = (
            ("partial" if source.jobs_new + source.jobs_duplicate else "failed")
            if has_error
            else "completed"
        )
        source.finished_at = datetime.now(UTC)
        append_event(
            db,
            run,
            f"Portal selesai: {source.status}. Baru: {source.jobs_new}; "
            f"duplikat: {source.jobs_duplicate}; filter tanggal: {source.jobs_filtered}. "
            + (f"Keterangan: {source.error}." if source.error else ""),
            platform=platform,
            level="WARN" if has_error else "INFO",
        )
    else:
        raise RuntimeError("Unknown crawler event")
    db.flush()
