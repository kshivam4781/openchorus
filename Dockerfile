FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY web ./web
ENV OPENCHORUS_HOST=0.0.0.0 OPENCHORUS_PORT=7777 OPENCHORUS_DATA=/data
VOLUME /data
EXPOSE 7777
CMD ["python", "-m", "server.main"]
