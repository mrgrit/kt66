FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/
# agents/ 는 볼륨으로 들어온다 — 학생이 웹으로 바꾼 것과 셸에서 바꾼 것이
# 같은 파일이어야 git diff 로 자기 변경을 볼 수 있다.
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]
