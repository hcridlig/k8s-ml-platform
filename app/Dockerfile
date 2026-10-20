FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Correctifs de sécurité de l'OS publiés depuis la construction de l'image de base : sans ça, le scan Trivy échoue dès qu'un
# paquet Debian a une CVE corrigée plus récente que l'image python:3.13-slim.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

# 1) Les dépendances d'abord : cette couche reste en cache tant que requirements.txt ne change pas
COPY requirements.txt .
# pip, setuptools et wheel ne servent qu'à installer : on les retire après. Ils embarquent leurs propres copies de bibliothèques
# (urllib3, msgpack, jaraco...) que Trivy signale, et qu'aucun requirements.txt ne peut corriger.
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip setuptools wheel

# 2) Le code, puis l'entraînement du modèle (exécuté au build, pas au démarrage du pod)
COPY train.py main.py index.html ./
RUN python train.py

# 3) UID numérique non-root : requis pour que `runAsNonRoot: true` soit vérifiable par Kubernetes
USER 10001
# La CI passe le SHA du commit (--build-arg) : l'interface affiche ainsi quelle version du code répond.
ARG MODEL_VERSION=1.2.0
ENV MODEL_VERSION=$MODEL_VERSION
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
