import json
import os
import random
from pathlib import Path

import joblib
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
MODEL = joblib.load(os.getenv("MODEL_PATH", HERE / "model.joblib"))
SAMPLES = json.loads((HERE / "samples.json").read_text())  # {"0": [[64 entiers], ...], ..., "9": [...]}
INDEX = (HERE / "index.html").read_text()  # l'interface : une page unique, servie par l'API elle-même
VERSION = os.getenv("MODEL_VERSION", "dev")
POD = os.getenv("HOSTNAME", "local")  # dans K8s, HOSTNAME = nom du pod -> on voit qui répond
NODE = os.getenv("NODE_NAME", "local")  # injecté par le downward API (spec.nodeName) : sur quel nœud tourne le pod
CHAOS = os.getenv("ENABLE_CHAOS") == "1"  # endpoints de panne volontaire : lab uniquement
sick = False  # passe à True via /sick pour simuler un pod qui déraille (démo de liveness)

app = FastAPI(title="ml-api")
# Les probes et /metrics sont exclus : sinon leurs requêtes (rapides) diluent RPS et latence réels.
# Buckets fins autour de 10-500 ms : c'est ce qui rend histogram_quantile (p95) lisible.
(
    Instrumentator(excluded_handlers=["/metrics", "/healthz", "/readyz"])
    .add(metrics.requests())
    .add(metrics.latency(buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)))
    .instrument(app)
    .expose(app)  # expose /metrics pour Prometheus
)


class Req(BaseModel):
    pixels: list[float] = Field(min_length=64, max_length=64)  # image 8x8 aplatie


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return INDEX


@app.post("/predict")
def predict(r: Req):
    proba = MODEL.predict_proba([r.pixels])[0]  # même coût CPU que predict() : predict en est l'argmax
    return {
        "digit": int(MODEL.classes_[proba.argmax()]),
        "proba": [round(float(p), 3) for p in proba],
        "model_version": VERSION,
        "pod": POD,
        "node": NODE,
    }


@app.get("/samples")
def samples():
    """Un vrai chiffre tiré au hasard pour chaque classe 0-9 (l'interface s'en sert pour ses démos)."""
    return [{"label": int(d), "pixels": random.choice(imgs)} for d, imgs in SAMPLES.items()]


@app.get("/healthz")  # liveness : "le process est-il vivant ?"
def healthz():
    return JSONResponse({"status": "sick" if sick else "ok"}, status_code=500 if sick else 200)


@app.get("/readyz")  # readiness : "peut-il recevoir du trafic ?" (modèle chargé = oui)
def readyz():
    return {"status": "ready", "pod": POD}


# --- Endpoints de panne volontaire : n'existent que si ENABLE_CHAOS=1 (jamais en prod) ---
if CHAOS:
    @app.post("/sick")
    def make_sick():
        global sick
        sick = True
        return {"pod": POD, "status": "sick"}


    @app.post("/crash")
    def crash():
        os._exit(1)  # tue le process brutalement : K8s doit redémarrer le conteneur
        raise HTTPException(500)  # jamais atteint
