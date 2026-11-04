"""Shared URL identity, without fetching a URL or importing a browser engine."""

import hashlib
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(value):
    parts = urlsplit(value.strip())
    if (
        parts.scheme.lower() not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Invalid job URL")
    host = parts.hostname.lower()
    path = parts.path or "/"
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if host in {"linkedin.com", "www.linkedin.com", "id.linkedin.com"}:
        match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d+)/?", path)
        job_id = match[1] if match else parse_qs(parts.query).get("currentJobId", [None])[0]
        if job_id and job_id.isdigit():
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
    if host == "id.jobstreet.com":
        match = re.fullmatch(r"/(?:id/)?job/(\d+)/?", path)
        job_id = match[1] if match else query.get("jobId")
        if job_id and job_id.isdigit():
            return f"https://id.jobstreet.com/id/job/{job_id}"
    if host in {"indeed.com", "www.indeed.com", "id.indeed.com"}:
        job_id = query.get("jk") or query.get("vjk")
        if re.fullmatch(r"[0-9a-f]{16}", job_id or "", re.IGNORECASE):
            return f"https://id.indeed.com/viewjob?jk={job_id.lower()}"
    if host in {"www.dealls.com", "dealls.com"}:
        host = "dealls.com"
        path = re.sub(r"^/en/loker/", "/loker/", path)
    if host in {"kalibrr.com", "www.kalibrr.com"}:
        host = "www.kalibrr.com"
        path = re.sub(r"^/[a-z]{2}-[A-Z]{2}/c/", "/c/", path)
    if host in {"www.glints.com", "glints.com"}:
        host = "glints.com"
    portal = host in {
        "glints.com",
        "dealls.com",
        "www.kalibrr.com",
        "www.techinasia.com",
        "techinasia.com",
    }
    tracking = {"ref", "source", "origin", "type", "traceInfo", "utm_referrer"} if portal else set()
    clean = [(k, v) for k, v in query.items() if not k.startswith("utm_") and k not in tracking]
    # Preserve ports for generic manual URLs; portal URLs must use ordinary HTTPS.
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit(
        (parts.scheme.lower(), netloc, path.rstrip("/") or "/", urlencode(sorted(clean)), "")
    )


def url_hash(value):
    return hashlib.sha256(normalize_url(value).encode()).hexdigest()
