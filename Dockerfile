FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY korailbot /usr/local/bin/korailbot
RUN chmod +x /usr/local/bin/korailbot

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    KORAIL_BOT_DATA_DIR=/data

VOLUME ["/data"]

# Usage: docker compose run --rm bot status
ENTRYPOINT ["python", "/app/src/cli.py"]
CMD ["--help"]
