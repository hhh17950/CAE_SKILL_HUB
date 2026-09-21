# Override with an approved internal registry/digest for production.
ARG PYTHON_IMAGE=python:3.10.11-slim-bullseye
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_CONFIG_FILE=/dev/null
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip --isolated install --no-cache-dir \
    --index-url https://pypi.org/simple -r requirements.txt \
    && groupadd --gid 10001 cae \
    && useradd --uid 10001 --gid cae --no-create-home --shell /usr/sbin/nologin cae \
    && mkdir -p /app/workspace /app/static/logs \
    && chown -R cae:cae /app/workspace /app/static

COPY main.py worker.py alembic.ini ./
COPY api/ api/
COPY core/ core/
COPY services/ services/
COPY storage/ storage/
COPY migrations/ migrations/
COPY static/docs/ static/docs/

USER 10001:10001
CMD ["python", "-m", "uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
