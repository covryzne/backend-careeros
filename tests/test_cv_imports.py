import copy
import uuid
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.foundation import CandidateDocument, CVImport
from app.services.career import career_context, validate_report
from app.services.cv_parser import parse_text

CV_TEXT = """Ayu Candidate
Backend Engineer
ayu@example.com | 081234567890
Date of birth: 1995-02-03
Address: Jl. Contoh No. 9, Jakarta

PROFESSIONAL SUMMARY
Backend engineer building Python API services.

TECHNICAL SKILLS
Python, FastAPI, PostgreSQL, Docker

WORK EXPERIENCE
Backend Engineer | PT Example
Jan 2022 - Present
- Built reliable APIs for internal products.

PROJECTS
Career Platform | Backend Developer
- Built a job tracker with Python and FastAPI.

PREFERRED ROLES
Backend Engineer

PREFERRED LOCATIONS
Remote, Jakarta
"""


def make_pdf(text=CV_TEXT):
    def escaped(line):
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = (
        "BT /F1 10 Tf 50 900 Td 12 TL "
        + " ".join("(" + escaped(line) + ") Tj T*" for line in text.splitlines())
        + " ET"
    ).encode("latin1", errors="replace")
    objects = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Page /Parent 4 0 R /MediaBox [0 0 612 1000] /Resources << /Font << /F1 1 0 R >> >> /Contents 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Catalog /Pages 4 0 R >>",
    ]
    result = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        result += f"{offset:010d} 00000 n \n".encode()
    result += b"trailer\n<< /Size 6 /Root 5 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
    return result


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    return tmp_path


def stage(client, content=None):
    response = client.post(
        "/api/v1/profile/cv-imports",
        files={"file": ("cv.pdf", make_pdf() if content is None else content, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def fields(record):
    data = copy.deepcopy(record["fields"])
    data["profile"] = {key: value for key, value in data["profile"].items() if value is not None}
    data["expected_profile_version"] = record["source_profile_version"]
    return data


def test_parser_extracts_actual_text_and_bilingual_fields():
    assert "Backend Engineer" in PdfReader(BytesIO(make_pdf())).pages[0].extract_text()
    parsed = parse_text(CV_TEXT)["fields"]
    assert parsed["profile"]["full_name"] == "Ayu Candidate"
    assert parsed["profile"]["email"] == "ayu@example.com"
    assert parsed["profile"]["date_of_birth"] == "1995-02-03"
    assert parsed["skills"] == ["Python", "FastAPI", "PostgreSQL", "Docker"]
    assert parsed["experiences"][0]["company"] == "PT Example"
    assert parsed["experiences"][0]["role"] == "Backend Engineer"
    assert parsed["experiences"][0]["start_date"] == "2022-01-01"
    assert parsed["experiences"][0]["is_current"]
    assert parsed["projects"][0]["name"] == "Career Platform"
    indo = (
        CV_TEXT.replace("PROFESSIONAL SUMMARY", "RINGKASAN PROFESIONAL")
        .replace("TECHNICAL SKILLS", "KEAHLIAN")
        .replace("WORK EXPERIENCE", "PENGALAMAN KERJA")
        .replace("PROJECTS", "PROYEK")
        .replace("Jan 2022 - Present", "Januari 2022 - Sekarang")
    )
    assert parse_text(indo)["fields"]["experiences"][0]["is_current"]


def test_cv_import_creates_profile_atomically_and_matches_without_facts(jobs_client, storage):
    client, db = jobs_client
    record = stage(client)
    assert client.get("/api/v1/profile").status_code == 404
    assert db.scalar(select(func.count()).select_from(CandidateDocument)) == 0
    response = client.post(
        "/api/v1/profile/cv-imports/" + record["id"] + "/apply",
        json=fields(record),
        headers={"Idempotency-Key": "apply-cv"},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["profile_version"] == 1 and saved["full_name"] == "Ayu Candidate"
    assert (
        len(saved["skills"]) == 4 and len(saved["experiences"]) == 1 and len(saved["projects"]) == 1
    )
    assert not saved["facts"] and len(saved["documents"]) == 1
    assert (
        client.post(
            "/api/v1/profile/cv-imports/" + record["id"] + "/apply",
            json=fields(record),
            headers={"Idempotency-Key": "apply-cv"},
        ).json()
        == saved
    )
    assert client.get("/api/v1/profile").json()["profile_version"] == 1
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "Backend Engineer",
            "company": "Example",
            "description": "Build Python API services.",
        },
    ).json()
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    assert all(
        value not in str(context)
        for value in ["ayu@example.com", "081234567890", "1995-02-03", "Jl. Contoh"]
    )
    assert any(
        item["kind"] == "project" and "Career Platform" in item["text"]
        for item in context["sources"].values()
    )
    source = next(
        item
        for item in context["sources"].values()
        if item["kind"] == "skill" and item["text"] == "Python"
    )
    report = {
        "kind": "job_analysis",
        "title": "Prioritas",
        "summary": "Data profil yang diimpor.",
        "recommendations": [
            {
                "job_id": job["id"],
                "category": "prioritize",
                "reasons": [
                    {
                        "text": "Kebutuhan posisi dan skill tercatat.",
                        "citations": [
                            {"source_id": "job:" + job["id"], "quote": "Backend Engineer"},
                            {"source_id": source["id"], "quote": "Python"},
                        ],
                    }
                ],
            }
        ],
    }
    assert (
        validate_report(__import__("json").dumps(report), context)["report"]["recommendations"][0][
            "category"
        ]
        == "prioritize"
    )
    sources = client.get("/api/v1/draft-sources").json()["items"]
    assert any(item["kind"] == "experience" for item in sources)
    draft = client.post(
        "/api/v1/drafts", json={"job_id": job["id"], "source_profile_version": 1}
    ).json()
    assert "Career Platform" in draft["content"] and not draft["review_flags"]
    assert any(item["kind"] == "candidate_project" for item in draft["citations"])


def test_merge_preserves_existing_data_and_deduplicates(jobs_client, storage):
    client, _ = jobs_client
    client.patch(
        "/api/v1/profile", json={"full_name": "Existing Name", "preferred_locations": ["Bandung"]}
    )
    client.post("/api/v1/profile/skills", json={"name": "python"})
    before = client.get("/api/v1/profile").json()
    record = stage(client)
    payload = fields(record)
    payload["profile"] = {"headline": "Backend Engineer"}
    saved = client.post(
        "/api/v1/profile/cv-imports/" + record["id"] + "/apply", json=payload
    ).json()
    assert saved["full_name"] == "Existing Name"
    assert any(item["value"] == "Bandung" for item in saved["preferences"])
    assert saved["profile_version"] == before["profile_version"] + 1
    assert len([skill for skill in saved["skills"] if skill["name"].casefold() == "python"]) == 1
    record2 = stage(client)
    payload2 = fields(record2)
    payload2["profile"] = {"headline": "Backend Engineer"}
    second = client.post(
        "/api/v1/profile/cv-imports/" + record2["id"] + "/apply", json=payload2
    ).json()
    assert len(second["skills"]) == len(saved["skills"]) and len(second["experiences"]) == len(
        saved["experiences"]
    )
    assert len(second["projects"]) == len(saved["projects"]) and len(second["documents"]) == 1
    assert second["profile_version"] == saved["profile_version"]


def test_conflict_and_invalid_children_do_not_partially_write(jobs_client, storage):
    client, _ = jobs_client
    client.patch("/api/v1/profile", json={"full_name": "Existing Name"})
    record = stage(client)
    payload = fields(record)
    client.patch("/api/v1/profile", json={"headline": "Latest headline"})
    assert (
        client.post(
            "/api/v1/profile/cv-imports/" + record["id"] + "/apply", json=payload
        ).status_code
        == 409
    )
    current = client.get("/api/v1/profile").json()
    assert current["headline"] == "Latest headline" and not current["skills"]
    payload["expected_profile_version"] = current["profile_version"]
    payload["experiences"][0]["company"] = ""
    assert (
        client.post(
            "/api/v1/profile/cv-imports/" + record["id"] + "/apply", json=payload
        ).status_code
        == 422
    )
    unchanged = client.get("/api/v1/profile").json()
    assert (
        unchanged["headline"] == "Latest headline"
        and unchanged["full_name"] == "Existing Name"
        and not unchanged["skills"]
    )


def test_private_import_ownership_cancellation_and_pdf_errors(jobs_client, storage):
    client, db = jobs_client
    record = stage(client)
    row = db.get(CVImport, uuid.UUID(record["id"]))
    private_file = storage / row.storage_key
    assert private_file.is_file() and "storage_key" not in record
    app.dependency_overrides[get_actor] = lambda: ActorContext(uuid.uuid4())
    try:
        assert client.get("/api/v1/profile/cv-imports/" + record["id"]).status_code == 404
        assert (
            client.post(
                "/api/v1/profile/cv-imports/" + record["id"] + "/apply", json=fields(record)
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_actor, None)
    assert client.delete("/api/v1/profile/cv-imports/" + record["id"]).status_code == 204
    assert not private_file.exists()
    for content in [b"%PDF-not-real", make_pdf("")]:
        response = client.post(
            "/api/v1/profile/cv-imports", files={"file": ("cv.pdf", content, "application/pdf")}
        )
        assert response.status_code == 422, response.text
    writer = PdfWriter()
    writer.append(BytesIO(make_pdf()))
    writer.encrypt("secret")
    out = BytesIO()
    writer.write(out)
    encrypted = client.post(
        "/api/v1/profile/cv-imports", files={"file": ("cv.pdf", out.getvalue(), "application/pdf")}
    )
    assert encrypted.status_code == 422 and "password" in encrypted.text
    assert client.get("/api/v1/profile").status_code == 404


def test_retired_fact_tools_are_not_available(jobs_client, monkeypatch):
    client, _ = jobs_client
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "agent_approvals_enabled", True)
    tools = client.get("/api/v1/agent-capabilities").json()["tools"]
    assert "get_verified_facts" not in tools and "confirm_profile_fact" not in tools
    assert (
        client.post(
            "/api/v1/agent-runs",
            json={
                "task_type": "tracking_assistant",
                "instruction": "Check",
                "allowed_tools": ["get_verified_facts"],
            },
        ).status_code
        == 403
    )


def test_large_text_pdf_upload_uses_document_body_limit(jobs_client, storage):
    client, _ = jobs_client
    record = stage(client, make_pdf() + b" " * (1024 * 1024))
    assert record["fields"]["profile"]["full_name"] == "Ayu Candidate"


def test_page_limit_and_timeout_are_clear_and_leave_no_profile(jobs_client, storage, monkeypatch):
    import subprocess

    from app.services import cv_imports

    client, _ = jobs_client
    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    response = client.post(
        "/api/v1/profile/cv-imports", files={"file": ("cv.pdf", out.getvalue(), "application/pdf")}
    )
    assert response.status_code == 422 and "20 halaman" in response.text

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("local-parser", 15)

    monkeypatch.setattr(cv_imports.subprocess, "run", timeout)
    response = client.post(
        "/api/v1/profile/cv-imports", files={"file": ("cv.pdf", make_pdf(), "application/pdf")}
    )
    assert response.status_code == 422 and "batas waktu" in response.text
    assert client.get("/api/v1/profile").status_code == 404
    assert list(storage.iterdir()) == []


def test_pending_import_can_resume_and_cancel_clears_parsed_data(jobs_client, storage):
    client, _ = jobs_client
    record = stage(client)
    pending = client.get("/api/v1/profile/cv-imports").json()["items"]
    assert pending[0]["id"] == record["id"]
    assert client.get("/api/v1/profile/cv-imports/" + record["id"]).json()["fields"]["skills"]
    client.delete("/api/v1/profile/cv-imports/" + record["id"])
    assert client.get("/api/v1/profile/cv-imports").json()["items"] == []
    assert "ayu@example.com" not in client.get("/api/v1/profile/cv-imports/" + record["id"]).text


def test_parser_preserves_descriptions_for_inline_dates_and_multiple_projects():
    text = "Ayu Candidate\nBackend Engineer\nEXPERIENCE\nEngineer | PT One Jan 2020 - Dec 2021\nBuilt reliable APIs.\nEngineer | PT Two Jan 2022 - Present\nImproved delivery.\nPROJECTS\nFirst Platform | Developer\nBuilt APIs.\nSecond Platform | Developer\nBuilt UI."
    result = parse_text(text)["fields"]
    assert len(result["experiences"]) == 2
    assert result["experiences"][0]["description"] == "Built reliable APIs."
    assert len(result["projects"]) == 2
