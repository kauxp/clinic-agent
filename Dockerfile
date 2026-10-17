# Two stages: build the virtualenv with pip, then copy only the venv and app code into a clean image.
# Base image pinned by digest so a rebuild of the same commit gets the same OS layer.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
# The commit this image was built from; the app reports it at /version and in the clinic_build_info metric.
ARG APP_VERSION=dev
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.source="https://github.com/kauxp/clinic-agent" \
      org.opencontainers.image.revision="${APP_VERSION}"

# Fixed non-root user; Kubernetes also enforces runAsNonRoot and a read-only root filesystem.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY agent/ agent/
COPY service/ service/
COPY policies/ policies/
USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
