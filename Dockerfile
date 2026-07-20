FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /srv/broker

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 broker

COPY alembic.ini ./
COPY alembic ./alembic

USER broker

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
