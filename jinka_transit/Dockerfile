FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Paris
RUN apk add --no-cache tzdata
WORKDIR /app
COPY app/ /app/
EXPOSE 8099
CMD ["python3", "/app/main.py"]
