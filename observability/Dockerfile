FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul
WORKDIR /app
RUN pip install --no-cache-dir pyyaml==6.0.2
COPY observability/ /app/
COPY agents/activity_audit.py /app/
CMD ["python", "collector.py"]
