# MaMoLab — container cô lập (network none)
# Chỉ dùng để triage mẫu trong môi trường tách host.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LAB_HOME=/lab \
    LAB_SAMPLES=/quarantine \
    LAB_REPORTS=/reports

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      python3 \
      file \
      binutils \
      xxd \
      strings \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin --uid 10001 labuser \
    && mkdir -p /lab /quarantine /reports \
    && chown -R labuser:labuser /lab /quarantine /reports

WORKDIR /lab
COPY --chown=labuser:labuser analyze-static.py /lab/analyze-static.py
COPY --chown=labuser:labuser lab_static_engine.py /lab/lab_static_engine.py
COPY --chown=labuser:labuser entrypoint.sh /lab/entrypoint.sh
RUN chmod 755 /lab/entrypoint.sh /lab/analyze-static.py

USER labuser
# Không mở port; không agent SSH.
ENTRYPOINT ["/lab/entrypoint.sh"]
CMD ["help"]
