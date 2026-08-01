FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY solution.py .
COPY models ./models
COPY run.sh .
RUN chmod 755 /app/run.sh
ENTRYPOINT ["/app/run.sh"]
