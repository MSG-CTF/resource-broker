FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /srv/broker

ARG BOOTSTRAP_VERSION=0.4.2

RUN apt-get update \
    && apt-get install --yes --no-install-recommends bash coreutils openssl tar \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY deploy/bootstrap ./deploy/bootstrap
COPY node-agent/k8s ./node-agent/k8s

RUN pip install --no-cache-dir .
RUN bash deploy/bootstrap/build-bundle.sh \
      "${BOOTSTRAP_VERSION}" /srv/broker/bootstrap-artifacts
RUN groupadd --gid 10001 broker \
    && useradd --create-home --uid 10001 --gid 10001 broker

COPY alembic.ini ./
COPY alembic ./alembic

USER broker

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
