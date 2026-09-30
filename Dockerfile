# Override with an approved internal registry/digest for production.
FROM centos-conda:cos7-24.9.2-x86_64

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_CONFIG_FILE=/dev/null

WORKDIR /opt/cae_service

COPY . /opt/cae_service
RUN python -m pip --isolated install --no-cache-dir \
    --index-url https://pypi.org/simple -r requirements.txt \
    && mkdir -p /opt/cae_service/workspace /opt/cae_service/static

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
