FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements-nicegui.txt
# Expose the NiceGUI port
EXPOSE 8080
# Run the Web UI version
CMD ["python", "main_webui.py"]
