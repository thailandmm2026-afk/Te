FROM python:3.11-slim

# Install ffmpeg (required for merging streams + thumbnails)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY cookies.txt* ./

RUN mkdir -p downloads

CMD ["python", "bot.py"]
