FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot
RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app

VOLUME ["/app/data"]
CMD ["python", "-m", "bot"]
