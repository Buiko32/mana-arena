FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && groupadd --gid 10001 mana-arena \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mana-arena \
    && mkdir -p /data/db /data/photos \
    && chown -R 10001:10001 /data

COPY --chown=10001:10001 app ./app

USER 10001:10001
EXPOSE 3388

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3388", "--workers", "1"]
