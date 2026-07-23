FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY app ./app
COPY bin/query /usr/local/bin/query

RUN chmod +x /usr/local/bin/query

CMD ["python", "-m", "app.ingestor"]
