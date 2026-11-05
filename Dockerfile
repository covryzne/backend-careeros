# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 careeros \
    && useradd --uid 10001 --gid careeros --create-home careeros \
    && mkdir -p /data/careeros/documents \
    && chown -R careeros:careeros /data/careeros
COPY --chown=careeros:careeros app ./app
COPY --chown=careeros:careeros alembic ./alembic
COPY --chown=careeros:careeros alembic.ini .
USER careeros
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
