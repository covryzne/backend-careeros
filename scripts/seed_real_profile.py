"""Idempotently seed Shendi Teuku Maulana Efendi through the local profile API.

Start the backend first and configure DATABASE_URL in backend-careeros/.env.
This script intentionally stores claims as self-reported unless evidence exists.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "http://127.0.0.1:8001/api/v1/profile"

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
    "Python", "Machine Learning", "Deep Learning", "Computer Vision",
    "Natural Language Processing", "Large Language Models (LLMs)", "Generative AI",
    "LLM fine-tuning: CPT, SFT, LoRA, and quantization", "Model evaluation",
    "OCR-related processing", "Retrieval-Augmented Generation (RAG)",
    "Embeddings and semantic search", "AI agent workflows", "Tool integration and orchestration",
    "Hugging Face", "FastAPI", "REST APIs", "PostgreSQL", "SQL",
    "Data processing and web crawling", "Docker and Docker Compose", "Linux VPS deployment",
    "MinIO", "PyTorch", "scikit-learn", "pandas", "Git", "React", "TypeScript",
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
            "quantization. The available schema does not yet provide structured project-skill relations."
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

FACTS = [
    ("Education", "Graduated from Universitas PGRI Madiun with a Bachelor's degree in Informatics Engineering, GPA 3.87/4.00, in 2025."),
    ("Experience", "Completed the AI Engineer internship at Komdigi in May 2026."),
    ("Experience", "Contributed to a crawling pipeline capable of extracting up to approximately 6,000 URLs per day under its tested configuration."),
    ("LLM", "Worked with an LLM continued-pretraining corpus containing approximately 92 million tokens."),
    ("Computer Vision", "Worked on a Vision Transformer image classifier evaluated on approximately 10,700 web screenshots."),
    ("Data Science", "Contributed to a healthcare predictive-modeling project with reported recall around 0.89 and ROC-AUC around 0.86."),
]


def request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    request_object = Request(
        f"{API_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request_object, timeout=10) as response:
        return json.loads(response.read()) if response.status != 204 else {}


def upsert_children(profile: dict, collection: str, records: list[dict], key_fields: tuple[str, ...]) -> None:
    existing = profile[collection]
    for record in records:
        if any(all(item.get(field) == record.get(field) for field in key_fields) for item in existing):
            continue
        created = request(f"/{collection}", "POST", record)
        existing.append(created)


def main() -> None:
    profile = request("", "PATCH", PROFILE)
    upsert_children(profile, "skills", [
        {"name": name, "proficiency": None, "years_experience": None, "is_verified": False}
        for name in SKILLS
    ], ("name",))
    upsert_children(profile, "experiences", EXPERIENCES, ("role", "company", "start_date"))
    upsert_children(profile, "projects", PROJECTS, ("name",))
    upsert_children(profile, "facts", [
        {
            "category": category,
            "claim": claim,
            "evidence_reference": None,
            "is_verified": False,
            "verified_at": None,
        }
        for category, claim in FACTS
    ], ("category", "claim"))
    print(json.dumps(request(""), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError) as error:
        raise SystemExit(
            "Profile seed failed. Start the backend and configure DATABASE_URL in .env: "
            f"{error}"
        ) from error
