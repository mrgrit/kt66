# kt66 관제 화면(NOC).
#
# 상태를 만들지 않고 모으기만 한다 — envsim(전력·열), roster(근무자),
# docker.sock(컨테이너 생사)을 한 화면 분량으로 합성한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul

WORKDIR /app
COPY noc/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY noc/app.py ./
COPY noc/agent_control.py ./
COPY noc/risk_map.py ./
COPY noc/remote_hosts.py ./
COPY envsim/service_inventory.py ./envsim/service_inventory.py
COPY agents/activity_audit.py ./
COPY noc/static ./static

COPY ui/ /app/ui/

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
