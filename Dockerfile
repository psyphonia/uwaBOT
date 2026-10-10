FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATABASE_PATH=/data/uwa_india.db HEALTH_FILE=/tmp/uwa-bot-health.json
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home bot && mkdir /data && chown bot:bot /data
COPY *.py ./
USER bot
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD python healthcheck.py
CMD ["python", "bot.py"]
