FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 ca-certificates \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/browsers

RUN useradd --create-home --uid 10001 reader
COPY --chown=reader:reader . .
RUN mkdir -p /app/data && chown reader:reader /app/data

USER reader
CMD ["python", "server.py"]
