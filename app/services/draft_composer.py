"""Job-specific initial drafts from recorded sources; no invented qualifications."""

import re

from app.services.agent_privacy import private_values, sanitize_text

STOP = {
    "dan",
    "yang",
    "untuk",
    "dengan",
    "dari",
    "pada",
    "saya",
    "kami",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "you",
    "your",
    "are",
    "our",
    "will",
    "job",
    "role",
    "experience",
    "pengalaman",
    "requirements",
    "responsibilities",
}


def tokens(text):
    return {
        word
        for word in re.findall(r"[\w+#.]+", text.casefold())
        if len(word) > 1 and word not in STOP
    }


def relevant_sources(choices, job):
    from app.services.job_content import snapshot

    data = snapshot(job)
    job_tokens = tokens(" ".join(str(value or "") for value in data.values()))
    ranked = sorted(
        enumerate(choices),
        key=lambda entry: (-len(tokens(entry[1]["text"]) & job_tokens), entry[0]),
    )
    # Keep a professional summary and representative experience/project context,
    # while selecting relevant evidence before unrelated skills in large profiles.
    selected = []
    for kind, limit in (("profile", 1), ("experience", 3), ("project", 3), ("skill", 10)):
        selected.extend([item for _, item in ranked if item["kind"] == kind][:limit])
    return selected[:20]


def compose(job, selected, draft_type, language, owner):
    private = private_values(owner)

    def clean(value, limit=700):
        return sanitize_text(str(value or ""), private).strip()[:limit]

    title, company = clean(job["title"], 240), clean(job["company"], 240)
    english = language == "en"
    paragraphs = []
    if draft_type == "email":
        paragraphs.append(
            ("Subject: Application for " if english else "Subjek: Lamaran untuk ")
            + title
            + " — "
            + company
        )
    elif english:
        paragraphs.append("Application for " + title)
    else:
        paragraphs.append("Lamaran untuk posisi " + title)
    paragraphs.append(
        ("Dear Hiring Team at " + company + ",")
        if english
        else ("Yth. Tim Rekrutmen " + company + ",")
    )
    paragraphs.append(
        f"I am writing to apply for the {title} position at {company}."
        if english
        else f"Saya bermaksud melamar posisi {title} di {company}."
    )
    contexts = []
    for key, label_id, label_en in (
        ("responsibilities", "tanggung jawab posisi", "the role's responsibilities"),
        ("requirements", "persyaratan posisi", "the role's requirements"),
    ):
        if job.get(key):
            excerpt = clean(re.sub(r"\s+", " ", job[key]), 260)
            contexts.append(
                f"Dari lowongan yang dipublikasikan, {label_id} mencakup: “{excerpt}”."
                if not english
                else f"The published vacancy describes {label_en} as: “{excerpt}”."
            )
    if not contexts and job.get("description"):
        excerpt = clean(re.sub(r"\s+", " ", job["description"]), 320)
        contexts.append(
            f"Saya memahami fokus lowongan ini dari deskripsi: “{excerpt}”."
            if not english
            else f"I understand the vacancy's focus from its description: “{excerpt}”."
        )
    paragraphs.extend(contexts)
    skills = [item["text"] for item in selected if item["kind"] == "skill"]
    requested = tokens(
        " ".join(
            str(job.get(key) or "")
            for key in (
                "title",
                "description",
                "requirements",
                "responsibilities",
                "experience_requirements",
                "preferred_qualifications",
            )
        )
        + " "
        + " ".join(job.get("skills", []))
    )
    relevant = [value for value in skills if tokens(value) & requested]
    if relevant:
        paragraphs.append(
            (
                "Keahlian yang tercatat pada profil saya dan berkaitan dengan lowongan ini meliputi "
                if not english
                else "My recorded skills relevant to this vacancy include "
            )
            + ", ".join(relevant[:8])
            + "."
        )
    limit = 3 if draft_type == "email" else 6
    professional = [item for item in selected if item["kind"] != "skill"][:limit]
    for item in professional:
        label = {
            "experience": (
                "Dalam pengalaman profesional saya, berikut pekerjaan yang saya catat: ",
                "My recorded professional experience includes: ",
            ),
            "project": (
                "Salah satu project yang telah saya kerjakan adalah ",
                "One of my recorded projects is ",
            ),
            "profile": ("Latar belakang profesional saya: ", "My professional background: "),
            "fact": ("Catatan pada profil saya: ", "A recorded detail from my profile: "),
        }.get(item["kind"], ("", ""))[int(english)]
        paragraphs.append(label + clean(item["text"], 800 if draft_type == "cover_letter" else 450))
    if not professional and skills and not relevant:
        paragraphs.append(
            (
                "Keahlian yang tercatat pada profil saya meliputi "
                if not english
                else "My recorded skills include "
            )
            + ", ".join(skills[:8])
            + "."
        )
    paragraphs.append(
        "Saya terbuka untuk mendiskusikan pengalaman dan project tersebut lebih lanjut serta memahami kebutuhan tim. Terima kasih atas waktu dan pertimbangannya."
        if not english
        else "I would welcome a discussion of my recorded experience and projects and a better understanding of your team's needs. Thank you for your time and consideration."
    )
    paragraphs.append(
        "Hormat saya,\n[Nama pelamar]" if not english else "Kind regards,\n[Applicant name]"
    )
    return "\n\n".join(paragraphs)
