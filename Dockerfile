FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Europe/Sofia
RUN apt-get update && apt-get install -y --no-install-recommends tzdata poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY collector/ collector/
COPY migrations/ migrations/
COPY web/ web/
COPY seed/ seed/
CMD ["python", "-m", "collector.main", "run"]
