FROM python:3.10-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium xvfb x11vnc novnc fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements-nicegui.txt

ENV WEBUI_BROWSER_PATH=/usr/bin/chromium
EXPOSE 5800
CMD ["python", "main_webui.py"]
