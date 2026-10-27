"""Seed the existing real professional profile locally, without API/network calls.

Default: fill missing fields and merge professional children. Use --overwrite
only to replace populated seed-related fields with the values in this file.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.db.session import SessionLocal
from app.models.candidate import CandidateExperience, CandidateProject, CandidateSkill
from app.schemas.profile import (
    CandidateExperienceCreate,
    CandidateExperienceUpdate,
    CandidateProjectCreate,
    CandidateProjectUpdate,
    CandidateSkillCreate,
    CandidateSkillUpdate,
    ProfileUpdate,
)
from app.services import profile
from app.services.idempotency import lock_owner

PROFILE = {
    "full_name": "Shendi Teuku Maulana Efendi",
    "email": "shendyteuku2@gmail.com",
    "phone": "+62 895-1485-1100",
    "place_of_birth": None,
    "date_of_birth": None,
    "headline": "AI Engineer | LLM Engineer | Agentic AI Engineer",
    "location": "Madiun, Jawa Timur, Indonesia",
    "professional_summary": (
        "Informatics Engineering graduate from Universitas PGRI Madiun with a GPA of "
        "3.87/4.00 and practical experience in AI engineering, LLM development, "
        "computer vision, data processing, and production-oriented backend deployment.\n\n"
        "Completed an AI Engineer internship at Komdigi, contributing to digital-space "
        "monitoring systems involving web crawling, text and image classification, "
        "OCR-related processing, LLM workflows, FastAPI, PostgreSQL, MinIO, Docker, "
        "and Linux VPS deployment.\n\n"
        "Also works as an External Code Reviewer at Dicoding Indonesia, reviewing projects "
        "across Data Science, Machine Learning, Deep Learning, Generative AI, and MLOps.\n\n"
        "Interested in building reliable AI systems end to end, including LLM fine-tuning, "
        "RAG, AI agents, model evaluation, inference, and production deployment."
    ),
    "preferred_work_arrangement": "flexible",
    "preferred_salary_min": None,
    "preferred_salary_max": None,
    "salary_currency": None,
    "preferred_titles": [
        "AI Engineer",
        "LLM Engineer",
        "Agentic AI Engineer",
        "Machine Learning Engineer",
        "Data Scientist",
        "Data Engineer",
        "Data Analyst",
    ],
    "preferred_locations": ["Indonesia", "Remote"],
}

SKILLS = [
    "Python",
    "Machine Learning",
    "Deep Learning",
    "Computer Vision",
    "Natural Language Processing",
    "Large Language Models (LLMs)",
    "Generative AI",
    "LLM fine-tuning: CPT, SFT, LoRA, and quantization",
    "Model evaluation",
    "OCR-related processing",
    "Retrieval-Augmented Generation (RAG)",
    "Embeddings and semantic search",
    "AI agent workflows",
    "Tool integration and orchestration",
    "Hugging Face",
    "FastAPI",
    "REST APIs",
    "PostgreSQL",
    "SQL",
    "Data processing and web crawling",
    "Docker and Docker Compose",
    "Linux VPS deployment",
    "MinIO",
    "PyTorch",
    "scikit-learn",
    "pandas",
    "Git",
    "React",
    "TypeScript",
]

EXPERIENCES = [
    {
        "role": "AI Engineer Intern",
        "company": "Kementerian Komunikasi dan Digital (Komdigi)",
        "start_date": "2025-11-24",
        "end_date": "2026-05-24",
        "is_current": False,
        "description": (
            "Contributed to an AI-based digital-space monitoring system involving web "
            "crawling, preprocessing, deduplication, text and image classification, "
            "LLM integration, API development, and deployment. Developed a crawling "
            "pipeline capable of extracting up to approximately 6,000 URLs per day "
            "under the tested configuration; contributed to a continued-pretraining "
            "corpus containing approximately 92 million tokens; worked on a Vision "
            "Transformer image-classification model evaluated on approximately 10,700 "
            "web screenshots; and supported service integration across development and Linux VPS environments."
        ),
        "is_verified": False,
        "evidence_reference": None,
    },
    {
        "role": "External Code Reviewer",
        "company": "Dicoding Indonesia",
        "start_date": "2025-08-01",
        "end_date": None,
        "is_current": True,
        "description": (
            "Review and evaluate learner projects across Data Science, Machine Learning, "
            "Deep Learning, Generative AI, and MLOps. Provide technical feedback on "
            "implementation quality, evaluation, and project requirements."
        ),
        "is_verified": False,
        "evidence_reference": None,
    },
    {
        "role": "Data Scientist",
        "company": "RSUD Dr. Soetomo Surabaya",
        "start_date": "2024-08-01",
        "end_date": "2024-12-31",
        "is_current": False,
        "description": (
            "Worked on data science analysis involving more than 1,500 low-birth-weight "
            "and premature-birth records. Evaluated XGBoost, Random Forest, and Logistic "
            "Regression with class-imbalance handling and cross-validation. Reported "
            "metrics associated with the relevant modeling work included recall around "
            "0.89 and ROC-AUC around 0.86."
        ),
        "is_verified": False,
        "evidence_reference": None,
    },
]

PROJECTS = [
    {
        "name": "AI-Based Digital-Space Monitoring System",
        "role": "AI Engineer Intern / Contributor",
        "description": (
            "A monitoring pipeline combining web crawling, data preprocessing, "
            "deduplication, text and image classification, backend APIs, and persistence "
            "for digital-space monitoring. Technologies: Python, FastAPI, PostgreSQL, "
            "Docker, Linux, web crawling, LLMs, and computer vision."
        ),
        "url": None,
        "is_verified": False,
    },
    {
        "name": "LLM Continued Pretraining and Fine-Tuning",
        "role": "LLM Engineer / Project Contributor",
        "description": (
            "Worked with a continued-pretraining corpus of approximately 92 million "
            "tokens and LLM training workflows involving CPT, SFT, LoRA, and 4-bit "
            "quantization."
        ),
        "url": None,
        "is_verified": False,
    },
    {
        "name": "Vision Transformer Image Classifier",
        "role": "Machine Learning / Computer Vision Contributor",
        "description": (
            "Trained and evaluated a Vision Transformer-based image classifier on "
            "approximately 10,700 web screenshots, with reported accuracy and F1-score "
            "around 0.99."
        ),
        "url": None,
        "is_verified": False,
    },
    {
        "name": "Stunting / Low-Birth-Weight Risk Modeling",
        "role": "Data Scientist",
        "description": (
            "Evaluated predictive models using structured health records and techniques "
            "for handling class imbalance. Reported recall around 0.89 and ROC-AUC around 0.86."
        ),
        "url": None,
        "is_verified": False,
    },
]


def normalized(value):
    return str(value or "").strip().casefold()


def seed(db, actor=None, *, overwrite=False):
    actor = actor or ActorContext(LOCAL_OWNER_ID)
    lock_owner(db, actor)
    owner = profile.load_profile(db, actor, lock=True)
    values = {}
    for key, value in PROFILE.items():
        if value is None:
            continue
        if key in {"preferred_titles", "preferred_locations"}:
            category = "title" if key == "preferred_titles" else "location"
            empty = owner is None or not any(
                item.preference_type == category for item in owner.preferences
            )
        else:
            old = getattr(owner, key, None) if owner else None
            empty = old in (None, "") or key == "preferred_work_arrangement" and old == "unknown"
        if overwrite or empty:
            values[key] = value
    owner = profile.update_profile(
        db,
        actor,
        ProfileUpdate.model_validate(values),
        expected=owner.profile_version if owner else None,
    )
    created = {"skills": 0, "experiences": 0, "projects": 0}
    for collection, records, model, create_schema, update_schema, keys in (
        (
            "skills",
            [{"name": name} for name in SKILLS],
            CandidateSkill,
            CandidateSkillCreate,
            CandidateSkillUpdate,
            ("name",),
        ),
        (
            "experiences",
            EXPERIENCES,
            CandidateExperience,
            CandidateExperienceCreate,
            CandidateExperienceUpdate,
            ("role", "company", "start_date"),
        ),
        (
            "projects",
            PROJECTS,
            CandidateProject,
            CandidateProjectCreate,
            CandidateProjectUpdate,
            ("name",),
        ),
    ):
        for record in records:
            owner = profile.load_profile(db, actor, lock=True)
            row = next(
                (
                    item
                    for item in getattr(owner, collection)
                    if all(
                        normalized(getattr(item, key)) == normalized(record.get(key))
                        for key in keys
                    )
                ),
                None,
            )
            if row is None:
                profile.mutate_child(
                    db,
                    actor,
                    model,
                    "create",
                    payload=create_schema.model_validate(record),
                    expected=owner.profile_version,
                )
                created[collection] += 1
            else:
                updates = {
                    key: value
                    for key, value in record.items()
                    if (value is not None or overwrite)
                    and key not in {*keys, "is_verified", "evidence_reference"}
                    and (overwrite or getattr(row, key) in (None, ""))
                }
                if updates:
                    profile.mutate_child(
                        db,
                        actor,
                        model,
                        "update",
                        item_id=row.id,
                        payload=update_schema.model_validate(updates),
                        expected=owner.profile_version,
                    )
    owner = profile.load_profile(db, actor)
    return {
        "created": created,
        "profile_version": owner.profile_version,
        "totals": {key: len(getattr(owner, key)) for key in created},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace populated seed-related profile fields; retain unrelated records.",
    )
    args = parser.parse_args()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Seed profile hanya untuk database development/test.")
    try:
        with SessionLocal.begin() as db:
            result = seed(db, overwrite=args.overwrite)
    except SQLAlchemyError:
        raise SystemExit(
            "Seed profile gagal. Periksa DATABASE_URL dan jalankan python -m alembic upgrade head."
        ) from None
    print("Profil profesional siap:", result)
    print(
        "Data profil, skills, experience, dan project digunakan langsung. Tidak membuat Verified Facts atau PDF/CV palsu."
    )


if __name__ == "__main__":
    main()
