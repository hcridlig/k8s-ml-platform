FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Correctifs de sécurité de l'OS publiés depuis la construction de l'image de base : sans ça, le scan Trivy échoue dès qu'un
# paquet Debian a une CVE corrigée plus récente que l'image python:3.13-slim.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# pip, setuptools et wheel ne servent qu'à installer : on les retire après. Ils embarquent leurs propres copies de bibliothèques
# (urllib3, msgpack, jaraco...) que Trivy signale, et qu'aucun requirements.txt ne peut corriger.
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip setuptools wheel

COPY main.py index.html ./

USER 10001
EXPOSE 8000
# Les connexions SSE restent ouvertes : sans ce délai, un rolling update attendrait la fin du grace period (30 s)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "3"]
