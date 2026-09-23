FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/evidence-runtime

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt \
    && groupadd --gid 65532 evidence-runtime \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /nonexistent evidence-runtime \
    && mkdir -p /var/lib/evidence-gateway \
    && chown 65532:65532 /var/lib/evidence-gateway

COPY --chown=65532:65532 evidence_gateway ./evidence_gateway

USER 65532:65532

ENTRYPOINT ["python", "-m", "evidence_gateway.runtime"]
