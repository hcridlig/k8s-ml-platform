FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# 1) Les dépendances d'abord : cette couche reste en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
