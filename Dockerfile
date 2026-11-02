FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
RUN mkdir -p static/vendor \
 && curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js  -o static/vendor/leaflet.js \
 && curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o static/vendor/leaflet.css

ENV DATA_DIR=/data PHOTO_ROOTS=/photos PYTHONUNBUFFERED=1
EXPOSE 8000
# Runs as root: only a file's owner (or root) may set its Date modified, and a library has photos owned by
# several users. New .xmp sidecars are given the owner of their photo.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
