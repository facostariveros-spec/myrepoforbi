FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["sh", "-c", "exec uvicorn adaptive_grid_manager.app:app --host 0.0.0.0 --port ${PORT}"]

