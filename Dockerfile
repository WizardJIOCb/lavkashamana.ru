FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
COPY certs/russian_trusted_sub_ca.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
