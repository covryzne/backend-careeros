"""Minimize personal data before any agent context reaches a provider."""

import json
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[ .()-]*|0)(?:\d[ .()-]*){8,14}(?![\w-])")
_ADDRESS = re.compile(
    r"(?i)\b(?:alamat(?:\s+(?:rumah|domisili))?|home address|address|domisili)\s*[:=-]\s*[^\n;|]{1,240}"
)
_BIRTH = re.compile(
    r"(?i)\b(?:tanggal\s+lahir|tgl\.?\s+lahir|date\s+of\s+birth|d\.?o\.?b|born)\s*[:=-]?\s*[^;\n|]{1,80}"
)
_AREA = re.compile(r"(?i)\b(?:rt\s*\d{1,3}\s*[/,-]?\s*rw\s*\d{1,3}|rw\s*\d{1,3})[^\n;|]{0,120}")
_STREET = re.compile(r"(?i)\b(?:jl\.?|jalan|jln\.?|rt\s*[/.-]?\s*rw)\s+[^\n;|]{1,200}")


def private_values(owner):
    if owner is None:
        return []
    values = [owner.full_name, owner.email, owner.phone, owner.place_of_birth]
    if owner.location and (
        re.search(r"(?i)\b(jl|jalan|jln|rt|rw|no)\b", owner.location)
        or re.search(r"\d", owner.location)
    ):
        values.append(owner.location)
    if owner.date_of_birth:
        date = owner.date_of_birth
        values += [
            date.isoformat(),
            date.strftime("%d/%m/%Y"),
            date.strftime("%d-%m-%Y"),
            date.strftime("%d %B %Y"),
            date.strftime("%B %d, %Y"),
        ]
        months = [
            "Januari",
            "Februari",
            "Maret",
            "April",
            "Mei",
            "Juni",
            "Juli",
            "Agustus",
            "September",
            "Oktober",
            "November",
            "Desember",
        ]
        values += [f"{date.day} {months[date.month - 1]} {date.year}", date.strftime("%m/%d/%Y")]
    return [str(value) for value in values if value and len(str(value).strip()) >= 3]


def sanitize_text(text, private=()):
    text = str(text)
    for value in sorted(set(private), key=len, reverse=True):
        text = re.sub(
            r"(?<!\w)" + re.escape(value) + r"(?!\w)",
            "[data pribadi disembunyikan]",
            text,
            flags=re.IGNORECASE,
        )
    text = _EMAIL.sub("[email disembunyikan]", text)
    text = _PHONE.sub("[telepon disembunyikan]", text)
    text = _ADDRESS.sub("[alamat disembunyikan]", text)
    text = _BIRTH.sub("[tanggal lahir disembunyikan]", text)
    text = _AREA.sub("[alamat disembunyikan]", text)
    return _STREET.sub("[alamat disembunyikan]", text)


def sanitize_messages(messages, private=()):
    result = []
    for message in messages:
        item = dict(message)
        # Resource IDs/tool protocol remain byte-for-byte intact. Context JSON
        # has already been minimized at its service boundary.
        if item.get("content") and item.get("role") in {"user", "assistant"}:
            item["content"] = sanitize_text(item["content"], private)
        if item.get("content") and item.get("role") == "tool":
            try:
                item["content"] = json.dumps(sanitize_payload(json.loads(item["content"]), private))
            except (ValueError, TypeError):
                item["content"] = sanitize_text(item["content"], private)
        result.append(item)
    return result


_PROTOCOL_KEYS = {
    "id",
    "job_id",
    "application_id",
    "source_id",
    "profile_id",
    "entity_id",
    "tool_call_id",
    "generated_at",
    "confirmation_status",
    "evidence_status",
    "status",
    "kind",
}


def sanitize_payload(value, private=(), key=None):
    if key in _PROTOCOL_KEYS:
        return value
    if isinstance(value, str):
        return sanitize_text(value, private)
    if isinstance(value, list):
        return [sanitize_payload(item, private) for item in value]
    if isinstance(value, dict):
        return {name: sanitize_payload(item, private, name) for name, item in value.items()}
    return value
