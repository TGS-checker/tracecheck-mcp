FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py agent-card.json llms.txt ./
COPY assets ./assets
COPY data/serving.json ./data/serving.json
ENV TRACECHECK_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    TRACECHECK_LOG_FILE=/data/calls.jsonl \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')" || exit 1
CMD ["python", "server.py"]
