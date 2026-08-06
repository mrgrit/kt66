# kt66 환경 시뮬레이터 — 1F 시설 계통(전력·냉방·소방·물리보안).
#
# 시설은 가상이지만 부하는 실제다: docker.sock 으로 컨테이너 CPU 를,
# 터널 너머 DGX Spark 의 Ollama 로 GPU 상태를 읽는다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY model.py app.py assets.yaml ./

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
