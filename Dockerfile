FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy all source first (needed for -e install)
COPY . .

# Create data directory
RUN mkdir -p data

RUN pip install --no-cache-dir -e .
RUN python -m spacy download en_core_web_sm

CMD ["python", "-m", "src.main"]
