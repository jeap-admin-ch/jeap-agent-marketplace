# Supply an approved Debian/Ubuntu base by immutable digest. No project/PAT in this context.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG DRAWIO_SHA256
COPY drawio.deb /tmp/drawio.deb
RUN test -n "$DRAWIO_SHA256" \
    && printf '%s  /tmp/drawio.deb\n' "$DRAWIO_SHA256" | sha256sum -c - \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        /tmp/drawio.deb xvfb xauth fonts-liberation libgbm1 \
    && command -v drawio && command -v xvfb-run \
    && rm -f /tmp/drawio.deb \
    && rm -rf /var/lib/apt/lists/*
ENV DRAWIO_DISABLE_UPDATE=true
WORKDIR /work
ENTRYPOINT ["xvfb-run", "-a", "drawio"]
