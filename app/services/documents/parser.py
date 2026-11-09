"""Local-only text PDF extraction and conservative EN/ID CV section parsing.

The CLI runs in a bounded subprocess so a malformed PDF cannot hang the API.
No candidate content is sent to a network provider or written to logs.
"""

import json
import re
import sys
from datetime import date
from io import BytesIO

from pypdf import PdfReader

SECTIONS = {
    "summary": {
        "summary",
        "professional summary",
        "career summary",
        "profile",
        "profil",
        "profil profesional",
        "ringkasan",
        "ringkasan profesional",
        "tentang saya",
        "about me",
        "objective",
    },
    "skills": {
        "skills",
        "technical skills",
        "core skills",
        "keahlian",
        "kemampuan",
        "technical expertise",
        "tech stack",
        "kompetensi",
        "expertise",
    },
    "experiences": {
        "experience",
        "experiences",
        "work experience",
        "professional experience",
        "employment history",
        "pengalaman",
        "pengalaman kerja",
        "pengalaman profesional",
        "riwayat pekerjaan",
    },
    "projects": {
        "projects",
        "project",
        "selected projects",
        "personal projects",
        "project experience",
        "proyek",
        "proyek pilihan",
        "pengalaman proyek",
        "portfolio",
        "portofolio",
    },
    "education": {"education", "pendidikan", "riwayat pendidikan", "academic background"},
    "other": {
        "certifications",
        "certificates",
        "sertifikasi",
        "languages",
        "bahasa",
        "awards",
        "publications",
        "interests",
        "references",
        "referensi",
    },
    "titles": {"preferred roles", "desired roles", "target roles", "posisi yang diinginkan"},
    "locations": {"preferred locations", "work locations", "lokasi kerja yang diinginkan"},
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "januari": 1,
    "feb": 2,
    "february": 2,
    "februari": 2,
    "mar": 3,
    "march": 3,
    "maret": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "mei": 5,
    "jun": 6,
    "june": 6,
    "juni": 6,
    "jul": 7,
    "july": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "agu": 8,
    "agustus": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    "des": 12,
    "desember": 12,
}
MONTH_PATTERN = "|".join(re.escape(value) for value in sorted(MONTHS, key=len, reverse=True))
TOKEN = r"(?:\d{4}-\d{2}-\d{2}|(?:" + MONTH_PATTERN + r")\.?\s*\d{4}|(?:19|20)\d{2})"
RANGE = re.compile(
    r"("
    + TOKEN
    + r")\s*(?:[-–—]|to|sampai|hingga)\s*("
    + TOKEN
    + r"|present|current|sekarang|saat ini|saatini)",
    re.IGNORECASE,
)
CATALOG = (
    "Python",
    "TypeScript",
    "JavaScript",
    "React",
    "Next.js",
    "Node.js",
    "FastAPI",
    "Django",
    "Flask",
    "PostgreSQL",
    "MySQL",
    "SQL",
    "MongoDB",
    "Redis",
    "Docker",
    "Kubernetes",
    "Git",
    "AWS",
    "Azure",
    "GCP",
    "Figma",
    "HTML",
    "CSS",
    "Tailwind CSS",
    "Java",
    "Golang",
    "C++",
    "C#",
    "PHP",
    "Laravel",
    "Rust",
    "Linux",
    "GraphQL",
    "REST API",
    "Machine Learning",
    "TensorFlow",
    "PyTorch",
    "Excel",
    "Power BI",
    "Tableau",
    "CI/CD",
)


def clean(line):
    return re.sub(r"^[\s•●▪*\-]+", "", line).strip()


def section_of(line):
    key = re.sub(r"[^\w\s]", " ", line.casefold()).strip()
    key = " ".join(key.split())
    return next((name for name, aliases in SECTIONS.items() if key in aliases), None)


def parse_date(value):
    value = value.strip()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    match = re.fullmatch(r"([A-Za-z]+)\.?\s*(\d{4})", value)
    if match and match[1].casefold() in MONTHS:
        return date(int(match[2]), MONTHS[match[1].casefold()], 1).isoformat()
    # A year alone is not enough to invent a month; preserve raw period in description.
    return None


def split_title(header):
    parts = [
        part.strip()
        for part in re.split(r"\s*[|]\s*|\s+at\s+|\s+di\s+|\s+@\s+", header, flags=re.IGNORECASE)
        if part.strip()
    ]
    if len(parts) >= 2:
        if re.match(r"(?i)^(PT\.?|CV\.?|Ltd|Inc|Company)\s", parts[0]):
            return parts[1][:200], parts[0][:200]
        return parts[0][:200], parts[1][:200]
    return header[:200], ""


def parse_experiences(lines, warnings):
    entries = []
    buffer = []
    current = None

    def finish():
        nonlocal current
        if current:
            current["description"] = "\n".join(current.pop("_lines"))[:15000] or None
            entries.append(current)
            current = None

    for raw in lines:
        line = clean(raw)
        if not line:
            if current:
                current["_gap"] = True
            continue
        match = RANGE.search(line)
        if match:
            inline = clean(line[: match.start()]).strip(" |,–—-")
            headers = []
            if current and not inline:
                # Common CVs put the next role/company immediately above its period.
                tail = []
                while (
                    current["_lines"]
                    and len(tail) < 2
                    and not current["_lines"][-1].startswith(("-", "•"))
                    and len(current["_lines"][-1]) < 100
                ):
                    tail.insert(0, current["_lines"].pop())
                    if "|" in tail[0]:
                        break
                headers = tail
                finish()
            elif current:
                finish()
            else:
                headers = buffer[-2:]
            if inline:
                headers = [inline]
            if not headers:
                headers = [""]
            header = headers[0] if "|" in headers[0] or len(headers) == 1 else headers[-2]
            role, company = split_title(header)
            if not company and len(headers) > 1:
                if re.match(r"(?i)^(PT\.?|CV\.?|Ltd|Inc)\s", headers[-2]):
                    company = headers[-2]
                    role = headers[-1]
                else:
                    role = headers[-2]
                    company = headers[-1]
            current = {
                "role": role[:200],
                "company": company[:200],
                "start_date": parse_date(match[1]),
                "end_date": parse_date(match[2]),
                "is_current": bool(
                    re.fullmatch(
                        r"present|current|sekarang|saat ini|saatini", match[2], re.IGNORECASE
                    )
                ),
                "_lines": [],
            }
            if current["is_current"]:
                current["end_date"] = None
            if not parse_date(match[1]):
                current["_lines"].append("Periode dalam CV: " + match[0])
            trailing = clean(line[match.end() :]).strip(" |,–—-")
            if trailing:
                current["_lines"].append(trailing)
            buffer = []
        elif current:
            current["_lines"].append(
                ("- " if raw.lstrip().startswith(("•", "-", "*")) else "") + line
            )
        else:
            buffer.append(line)
    finish()
    for entry in entries:
        entry.pop("_gap", None)
    if lines and not entries:
        chunks = [chunk for chunk in re.split(r"\n\s*\n", "\n".join(lines)) if chunk.strip()]
        for chunk in chunks[:50]:
            values = [clean(value) for value in chunk.splitlines() if clean(value)]
            role, company = split_title(values[0])
            entries.append(
                {
                    "role": role,
                    "company": company or (values[1][:200] if len(values) > 1 else ""),
                    "start_date": None,
                    "end_date": None,
                    "is_current": False,
                    "description": "\n".join(values[2:] if not company else values[1:])[:15000]
                    or None,
                }
            )
    if any(not entry["role"] or not entry["company"] for entry in entries):
        warnings.append("Lengkapi role/perusahaan pada pengalaman yang belum terbaca jelas.")
    return entries[:50]


def parse_projects(lines):
    entries = []
    current = None
    for raw in lines:
        value = clean(raw)
        if not value:
            if current:
                current["_gap"] = True
            continue
        is_bullet = raw.lstrip().startswith(("•", "-", "*"))
        if (
            current is None
            or (current.get("_gap") and not is_bullet)
            or (
                not is_bullet
                and "|" in value
                and not re.match(r"(?i)^(tech|stack|tools|teknologi|https?://)", value)
            )
        ):
            if current:
                entries.append(current)
            parts = [part.strip() for part in value.split("|")]
            current = {
                "name": parts[0][:200],
                "role": parts[1][:160] if len(parts) > 1 else None,
                "description": "",
                "url": None,
            }
        else:
            url = re.search(r"https?://[^\s)]+", value)
            if url:
                current["url"] = url[0][:2000]
            current["description"] = (current["description"] + "\n" + value).strip()[:15000]
        if current:
            current.pop("_gap", None)
    if current:
        entries.append(current)
    return [{key: value for key, value in entry.items() if key != "_gap"} for entry in entries[:50]]


def parse_text(text):
    text = text.replace("\r", "").replace("\x00", "")
    sections = {key: [] for key in SECTIONS}
    sections["header"] = []
    active = "header"
    for line in text.splitlines():
        section = section_of(line)
        if section:
            active = section
            continue
        sections[active].append(line.rstrip())
    header = [clean(line) for line in sections["header"] if clean(line)]
    profile = {}
    warnings = []
    if header:
        name_index = None
        for i, line in enumerate(header[:8]):
            candidate = re.sub(r"(?i)^(name|nama)\s*:\s*", "", line).strip()
            if (
                1 < len(candidate.split()) <= 8
                and len(candidate) <= 200
                and not re.search(r"[@\d:/]|curriculum|resume|vitae", candidate, re.IGNORECASE)
            ):
                profile["full_name"] = candidate
                name_index = i
                break
        if name_index is not None:
            headline = next(
                (
                    line
                    for line in header[name_index + 1 : name_index + 5]
                    if len(line) < 180
                    and re.search(
                        r"(?i)engineer|developer|designer|analyst|manager|specialist|consultant|architect|scientist|teknisi|mahasiswa|staff|owner|penulis|programmer",
                        line,
                    )
                    and not re.search(r"@|https?://|\+?\d[\d ().-]{6}", line)
                ),
                None,
            )
            if headline:
                profile["headline"] = headline[:240]
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phone = re.search(r"(?<![\w-])(?:\+\d{1,3}[ .()-]*|0)(?:\d[ .()-]*){8,14}(?![\w-])", text)
    if email:
        profile["email"] = email[0][:320]
    if phone:
        profile["phone"] = phone[0].strip()[:50]
    dob = re.search(
        r"(?i)(?:tanggal lahir|date of birth|dob)\s*[:=-]\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
        text,
    )
    if dob:
        try:
            if "/" in dob[1]:
                day, month, year = map(int, dob[1].split("/"))
                profile["date_of_birth"] = date(year, month, day).isoformat()
            else:
                profile["date_of_birth"] = date.fromisoformat(dob[1]).isoformat()
        except ValueError:
            warnings.append(
                "Tanggal lahir tidak terbaca sebagai tanggal valid; isi manual jika diperlukan."
            )
    address = re.search(r"(?im)^(?:alamat|address|domisili)\s*[:=-]\s*(.+)$", text)
    if address:
        profile["location"] = address[1].strip()[:240]
    summary = "\n".join(line.strip() for line in sections["summary"]).strip()
    if summary:
        profile["professional_summary"] = summary[:15000]
    for kind, field in [("titles", "preferred_titles"), ("locations", "preferred_locations")]:
        if sections[kind]:
            profile[field] = list(
                dict.fromkeys(
                    clean(value)[:200]
                    for value in re.split(r"[,;|\n]", "\n".join(sections[kind]))
                    if clean(value)
                )
            )[:30]
    skills = []
    for line in sections["skills"]:
        line = re.sub(r"^[\w /&-]{1,35}:\s*", "", clean(line))
        for part in re.split(r"[,;|•·]", line):
            value = clean(part)
            if not value:
                continue
            recognized = [
                skill
                for skill in CATALOG
                if re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", value, re.IGNORECASE)
            ]
            if len(recognized) > 1:
                skills += recognized
            elif len(value) <= 120:
                skills.append(value)
    skills = list({skill.casefold(): skill for skill in skills}.values())[:100]
    experiences = parse_experiences(sections["experiences"], warnings)
    projects = parse_projects(sections["projects"])
    for kind, value in [("skills", skills), ("experience", experiences), ("project", projects)]:
        if not value:
            warnings.append("Bagian " + kind + " belum dikenali. Kamu dapat melengkapinya di form.")
    if sections["education"] or sections["other"]:
        warnings.append(
            "Bagian pendidikan/sertifikasi/bahasa belum memiliki form terpisah; periksa informasi yang relevan sebelum menyimpan."
        )
    if not profile.get("full_name"):
        warnings.append("Nama belum dikenali. Isi nama sebelum membuat profil baru.")
    if RANGE.search(text):
        warnings.append(
            "Tanggal bulan/tahun memakai tanggal 1; periode yang hanya menyebut tahun tetap dicantumkan dalam deskripsi tanpa menebak bulan."
        )
    return {
        "fields": {
            "profile": profile,
            "skills": skills,
            "experiences": experiences,
            "projects": projects,
        },
        "warnings": warnings,
        "parser_version": "local-pdf-v1",
    }


def extract(content):
    reader = PdfReader(BytesIO(content), strict=False)
    if reader.is_encrypted:
        raise ValueError("PDF_ENCRYPTED")
    if len(reader.pages) > 20:
        raise ValueError("PDF_PAGE_LIMIT")
    texts = []
    size = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        size += len(text)
        if size > 150000:
            raise ValueError("PDF_TEXT_LIMIT")
        texts.append(text)
    text = "\n".join(texts)
    if len(text.strip()) < 30:
        raise ValueError("PDF_NO_TEXT")
    return parse_text(text)


if __name__ == "__main__":
    try:
        result = extract(sys.stdin.buffer.read(10 * 1024 * 1024 + 1))
    except ValueError as exc:
        result = {"error": str(exc) if str(exc).startswith("PDF_") else "PDF_INVALID"}
    except Exception:  # noqa: BLE001 - do not expose parser exceptions or CV text
        result = {"error": "PDF_INVALID"}  # No parser trace or CV text in logs.
    sys.stdout.write(json.dumps(result))
