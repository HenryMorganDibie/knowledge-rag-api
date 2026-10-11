FROM python:3.11-slim

WORKDIR /app

# System deps for pdfplumber and sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-rerank.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-rerank.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
