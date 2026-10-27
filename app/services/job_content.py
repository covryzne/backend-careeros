"""Conservative display extraction for legacy job descriptions; no external calls."""

import re
from html import unescape
from html.parser import HTMLParser


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value or "")
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", unescape("".join(parser.parts))).strip()


HEADINGS = {
    "responsibilities": r"(?:job\s*(?:description|desk|desc|responsibilities)|responsibilities|key responsibilities|what you(?:'|’)ll do|tugas(?: dan tanggung jawab)?|tanggung jawab|uraian pekerjaan|deskripsi pekerjaan)",
    "requirements": r"(?:requirements|job requirements|qualifications|minimum qualifications|persyaratan|kualifikasi|syarat(?: pelamar)?)",
    "preferred_qualifications": r"(?:preferred qualifications|nice to have|nilai tambah|kualifikasi tambahan)",
    "contact": r"(?:contact(?: hr)?|kontak(?: hr)?|cara melamar|how to apply)",
    "other": r"(?:benefits|benefit|tentang perusahaan|about us|about the role)",
}
HEADER = re.compile(
    r"^\s*(?:#{1,4}\s*)?("
    + "|".join(f"(?P<{key}>{value})" for key, value in HEADINGS.items())
    + r")\s*(?::\s*(.*))?$",
    re.IGNORECASE,
)
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ .()-]*|0)(?:\d[ .()-]*){7,15}(?!\w)")
CONTACT = re.compile(
    r"(?i)\b(?:hr|hrd|recruit(?:er|ment|ing)?|rekrutmen|personalia|talent acquisition|send (?:your )?(?:cv|resume)|kirim(?:kan)? (?:cv|lamaran)|apply|melamar|kontak|contact)\b"
)


def details(job):
    def value(key):
        return job.get(key) if isinstance(job, dict) else getattr(job, key, None)

    description = plain(value("description"))
    lines = description.splitlines()
    sections = {}
    active = None
    for line in lines:
        heading = HEADER.match(line)
        if heading:
            active = next(key for key in HEADINGS if heading.group(key))
            tail = heading.group(len(HEADINGS) + 2)
            if tail:
                sections.setdefault(active, []).append(tail)
        elif active and line.strip():
            sections.setdefault(active, []).append(line.strip())
    email = None
    phone = None
    # Only contact-labelled text and contact sections qualify as HR contact hints.
    contact_text = "\n".join(sections.get("contact", []))
    for index, line in enumerate(lines):
        if CONTACT.search(line):
            contact_text += "\n" + "\n".join(lines[index : index + 2])
    match = EMAIL.search(contact_text)
    if match:
        email = match.group().rstrip(".,;")
    for match in PHONE.finditer(contact_text):
        candidate = match.group().strip(" .,-()")
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 16 and not re.fullmatch(r"\d{4}[- /]\d{2}[- /]\d{2}", candidate):
            phone = candidate
            break
    return {
        "description": description or None,
        "responsibilities": plain(value("responsibilities"))
        or "\n".join(sections.get("responsibilities", []))
        or None,
        "requirements": plain(value("requirements"))
        or "\n".join(sections.get("requirements", []))
        or None,
        "experience_requirements": plain(value("experience_requirements")) or None,
        "preferred_qualifications": plain(value("preferred_qualifications"))
        or "\n".join(sections.get("preferred_qualifications", []))
        or None,
        "hr_contact_name": value("hr_contact_name"),
        "hr_contact_email": value("hr_contact_email") or email,
        "hr_contact_phone": value("hr_contact_phone") or phone,
    }


def snapshot(job):
    content = details(job)
    return {key: job.get(key) for key in ("id", "title", "company", "skills")} | {
        key: (value[:12000] if isinstance(value, str) else value)
        for key, value in content.items()
        if not key.startswith("hr_contact_")
    }
