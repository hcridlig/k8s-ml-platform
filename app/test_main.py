"""Tests de l'API. Lancer depuis app/ :  python train.py && pytest -q   (train.py produit model.joblib et samples.json)."""
import importlib

import pytest
from fastapi.testclient import TestClient


def load(monkeypatch, chaos=False):
    monkeypatch.setenv("HOSTNAME", "ml-api-test-abc12")
    monkeypatch.setenv("NODE_NAME", "node-1")
    monkeypatch.setenv("ENABLE_CHAOS", "1" if chaos else "0")
    import main
    return TestClient(importlib.reload(main).app)


@pytest.fixture
def client(monkeypatch):
    return load(monkeypatch)


def test_samples_are_recognized(client):
    samples = client.get("/samples").json()
    assert sorted(s["label"] for s in samples) == list(range(10))
    for s in samples:  # le modèle est entraîné sur ces chiffres : il doit les retrouver
        r = client.post("/predict", json={"pixels": s["pixels"]}).json()
        assert r["digit"] == s["label"]


def test_predict_response_shape(client):
    r = client.post("/predict", json={"pixels": [0] * 64}).json()
    assert len(r["proba"]) == 10 and abs(sum(r["proba"]) - 1) < 0.02
    assert (r["pod"], r["node"]) == ("ml-api-test-abc12", "node-1")


@pytest.mark.parametrize("pixels", [[0] * 63, [0] * 65, ["x"] * 64])
def test_predict_rejects_bad_input(client, pixels):
    assert client.post("/predict", json={"pixels": pixels}).status_code == 422


def test_probes_and_ui(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["status"] == "ready"
    page = client.get("/")
    assert page.status_code == 200 and "Atelier de chiffres" in page.text


def test_chaos_endpoints_only_when_enabled(monkeypatch):
    assert load(monkeypatch).post("/sick").status_code == 404
    chaotic = load(monkeypatch, chaos=True)
    assert chaotic.post("/sick").status_code == 200
    assert chaotic.get("/healthz").status_code == 500  # liveness échoue : K8s redémarrera le conteneur
    load(monkeypatch)  # remet l'état sain pour les autres tests
