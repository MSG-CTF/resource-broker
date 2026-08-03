FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 node-agent \
    && useradd --uid 10001 --gid 10001 --no-create-home node-agent

WORKDIR /opt/node-agent

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

USER 10001:10001

ENTRYPOINT ["msg-broker-node-agent"]

