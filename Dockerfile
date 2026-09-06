FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN groupadd --system traccia && useradd --system --gid traccia --create-home traccia
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[postgres]'
USER traccia
EXPOSE 8000
CMD ["uvicorn", "traccia_runtime.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
