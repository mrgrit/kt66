# kt66 고장 주입기 — IT 계통 38종.
#
# 랩을 실제로 조작하는 유일한 서비스라 의존성을 최소로 둔다. docker.sock 만 있으면
# 되고, 컨테이너 안에서 실행할 명령은 전부 대상 컨테이너의 도구를 쓴다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dk.py catalog.py app.py ./

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
