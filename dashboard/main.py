"""Plan du cluster : lit l'état réel de Kubernetes et le diffuse en direct à une page web.

Sources (lecture seule, hors boutons de démo) :
  - API Kubernetes : pods (état, nœud, redémarrages) et HPA
  - metrics-server : CPU et mémoire par pod, la mesure même que l'autoscaler utilise
  - /metrics de chaque pod : requêtes/s et latence, lues en direct (résolution 1 s, contre 15 s via Prometheus)
  - Locust : nombre d'utilisateurs simulés
DEMO=1 remplace tout ça par un cluster simulé : on voit l'interface sans Kubernetes.
"""
import asyncio
import json
import math
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from prometheus_client.parser import text_string_to_metric_families

NS = os.getenv("NAMESPACE", "ml-platform")
APP = os.getenv("TARGET_APP", "ml-api")
LOCUST = os.getenv("LOCUST_URL", "http://locust-master.loadtest.svc.cluster.local:8089")
CHAOS = os.getenv("ENABLE_CHAOS") == "1"  # boutons de panne : lab uniquement
DEMO = os.getenv("DEMO") == "1"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = "https://kubernetes.default.svc"
# profil -> (utilisateurs simulés, arrivées par seconde) ; chaque utilisateur envoie ~0,5 requête/s
PROFILES = {"calm": (10, 5), "rush": (60, 10), "blackfriday": (400, 40)}

INDEX = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
LOG = deque(maxlen=60)  # journal d'événements, le plus récent en premier
HISTORY = deque(maxlen=180)  # 3 minutes de points, un par seconde
STATE: dict = {"pods": [], "hpa": None, "locust": None, "error": "démarrage…", "chaos": CHAOS, "demo": DEMO}
PREV = {"init": False, "pods": {}, "desired": None, "lstate": None}
RATE: dict = {}  # pod -> (t, requêtes, secondes cumulées, rps lissé, latence lissée)

FR = {"Ready": "prêt", "Starting": "démarre", "ContainerCreating": "création du conteneur", "Pending": "en attente",
      "Terminating": "arrêt en cours", "CrashLoopBackOff": "boucle de crash", "Error": "erreur",
      "ImagePullBackOff": "image introuvable", "ErrImagePull": "image introuvable", "OOMKilled": "mémoire dépassée"}
BAD = {"CrashLoopBackOff", "Error", "ImagePullBackOff", "ErrImagePull", "OOMKilled"}
LSTATE = {"spawning": "montée en charge", "running": "charge stable", "stopping": "arrêt", "stopped": "arrêtée",
          "ready": "arrêtée"}


# ----------------------------------------------------------------- lecture du cluster
def cpu_m(q: str) -> float:  # "250m" | "12345678n" | "2" -> millicores
    for suffix, div in (("n", 1e6), ("u", 1e3), ("m", 1)):
        if q.endswith(suffix):
            return float(q[:-1]) / div
    return float(q) * 1000


def mem_mi(q: str) -> float:  # "123456Ki" | "12Mi" | "1Gi" -> Mio
    for suffix, mult in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024)):
        if q.endswith(suffix):
            return float(q[:-2]) * mult
    return float(q) / 2**20


async def k8s(method: str, path: str):
    token = Path(f"{SA}/token").read_text()  # relu à chaque appel : Kubernetes renouvelle ce jeton
    r = await KC.request(method, API + path, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {path.split('?')[0]} -> {r.status_code}")
    return r.json()


def pod_status(p: dict) -> str:  # mêmes règles que la colonne STATUS de `kubectl get pods`
    if p["metadata"].get("deletionTimestamp"):
        return "Terminating"
    st = p["status"]
    cs = (st.get("containerStatuses") or [{}])[0]
    state = cs.get("state", {})
    waiting = state.get("waiting", {}).get("reason")
    if st.get("phase") == "Pending":
        return waiting or "Pending"
    if st.get("phase") == "Running":
        if waiting:
            return waiting
        if "terminated" in state:
            return state["terminated"].get("reason", "Error")
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in st.get("conditions", []))
        return "Ready" if ready else "Starting"
    return st.get("phase", "Unknown")


def pod_row(item: dict, usage: dict) -> dict:
    md, st = item["metadata"], item["status"]
    cs = (st.get("containerStatuses") or [{}])[0]
    req = item["spec"]["containers"][0].get("resources", {}).get("requests", {})
    u = usage.get(md["name"])
    cpu = sum(cpu_m(c["usage"]["cpu"]) for c in u["containers"]) if u else 0
    mem = sum(mem_mi(c["usage"]["memory"]) for c in u["containers"]) if u else 0
    req_m = cpu_m(req.get("cpu", "0"))
    return {"name": md["name"], "node": item["spec"].get("nodeName"), "ip": st.get("podIP"),
            "status": pod_status(item), "restarts": cs.get("restartCount", 0),
            "last": cs.get("lastState", {}).get("terminated", {}).get("reason"),
            "cpu_m": round(cpu, 1), "mem_mi": round(mem), "req_m": req_m,
            "util": round(cpu / req_m * 100) if req_m else 0, "rps": 0.0, "lat": 0.0}


async def scrape(ip: str):  # compteurs cumulés de /predict dans le /metrics d'un pod
    n = s = 0.0
    r = await PC.get(f"http://{ip}:8000/metrics")
    for fam in text_string_to_metric_families(r.text):
        for sm in fam.samples:
            if sm.labels.get("handler") != "/predict":
                continue
            if sm.name == "http_requests_total":
                n += sm.value
            elif sm.name == "http_request_duration_seconds_sum":
                s += sm.value
    return n, s


async def add_rates(rows: list):
    ready = [r for r in rows if r["status"] == "Ready" and r["ip"]]
    got = await asyncio.gather(*(scrape(r["ip"]) for r in ready), return_exceptions=True)
    now = time.time()
    for r, g in zip(ready, got):
        if isinstance(g, Exception):
            continue
        n, s = g
        old = RATE.get(r["name"])
        if old:
            dt, dn, ds = now - old[0], max(0, n - old[1]), max(0, s - old[2])  # max(0, ..) : compteur remis à zéro au redémarrage
            rps = 0.5 * dn / dt + 0.5 * old[3]  # lissage : à 5 req/s, un compteur entier fait sauter la mesure
            lat = 0.5 * (ds / dn * 1000 if dn else 0) + 0.5 * old[4]
        else:
            rps = lat = 0.0
        RATE[r["name"]] = (now, n, s, rps, lat)
        r["rps"], r["lat"] = round(rps, 1), round(lat)
    for gone in set(RATE) - {r["name"] for r in rows}:
        del RATE[gone]


async def locust():
    j = (await LC.get(f"{LOCUST}/stats/requests")).json()
    return {"state": j["state"], "users": j["user_count"], "rps": round(j.get("total_rps", 0), 1),
            "fail": round(j.get("fail_ratio", 0), 3)}


async def snapshot():
    sel = f"labelSelector=app%3D{APP}"
    pj, mj, hj, lj = await asyncio.gather(
        k8s("GET", f"/api/v1/namespaces/{NS}/pods?{sel}"),
        k8s("GET", f"/apis/metrics.k8s.io/v1beta1/namespaces/{NS}/pods?{sel}"),
        k8s("GET", f"/apis/autoscaling/v2/namespaces/{NS}/horizontalpodautoscalers/{APP}"),
        locust(), return_exceptions=True)
    if isinstance(pj, Exception):
        raise pj  # sans la liste des pods il n'y a rien à montrer : l'erreur part vers l'interface
    usage = {} if isinstance(mj, Exception) else {i["metadata"]["name"]: i for i in mj["items"]}
    rows = [pod_row(i, usage) for i in sorted(pj["items"], key=lambda i: i["metadata"]["name"])]
    await add_rates(rows)
    hpa = None
    if not isinstance(hj, Exception):
        m = hj["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"]
        cur = (hj["status"].get("currentMetrics") or [{}])[0].get("resource", {}).get("current", {})
        hpa = {"min": hj["spec"]["minReplicas"], "max": hj["spec"]["maxReplicas"], "target": m,
               "util": cur.get("averageUtilization"), "current": hj["status"].get("currentReplicas", 0),
               "desired": hj["status"].get("desiredReplicas", 0)}
    publish(rows, hpa, None if isinstance(lj, Exception) else lj)


# ----------------------------------------------------------------- état partagé, journal
def note(kind: str, msg: str):
    LOG.appendleft({"t": int(time.time()), "k": kind, "m": msg})


def publish(rows: list, hpa, loc, err=None):
    cur = {r["name"]: r for r in rows}
    if PREV["init"]:  # au premier passage on ne raconte pas l'existant
        for n, r in cur.items():
            o, s = PREV["pods"].get(n), n[-5:]
            if o is None:
                note("info", f"Pod {s} créé sur {r['node'] or 'aucun nœud'}")
            elif o["status"] != r["status"]:
                kind = "good" if r["status"] == "Ready" else "bad" if r["status"] in BAD else "warn" if r["status"] == "Terminating" else "info"
                note(kind, f"Pod {s} : {FR.get(r['status'], r['status'])}")
            if o and r["restarts"] > o["restarts"]:
                note("bad", f"Pod {s} redémarré ({FR.get(r['last'], r['last'] or 'cause inconnue')})")
        for n in PREV["pods"].keys() - cur.keys():
            note("info", f"Pod {n[-5:]} supprimé")
        if hpa and PREV["desired"] not in (None, hpa["desired"]):
            note("warn", f"Autoscaler : {PREV['desired']} → {hpa['desired']} pods souhaités")
        if loc and PREV["lstate"] not in (None, loc["state"]):
            note("info", f"Charge : {LSTATE.get(loc['state'], loc['state'])}, {loc['users']} utilisateurs")
    PREV.update(init=True, pods=cur, desired=hpa["desired"] if hpa else None, lstate=loc["state"] if loc else None)

    ready = [r for r in rows if r["status"] == "Ready"]
    rps = sum(r["rps"] for r in ready)
    lat = sum(r["rps"] * r["lat"] for r in ready) / rps if rps else 0
    util = sum(r["util"] for r in ready) / len(ready) if ready else 0
    point = {"t": int(time.time()), "rps": round(rps, 1), "lat": round(lat), "util": round(util),
             "ready": len(ready), "desired": hpa["desired"] if hpa else len(rows)}
    HISTORY.append(point)
    STATE.update(pods=rows, hpa=hpa, locust=loc, error=err, point=point, ns=NS, app=APP)


async def poll():
    while True:
        t0 = time.time()
        try:
            await (sim_snapshot() if DEMO else snapshot())
        except Exception as e:  # la boucle ne doit jamais mourir : l'interface affiche l'erreur
            STATE["error"] = str(e)
        await asyncio.sleep(max(0.0, 1 - (time.time() - t0)))


# ----------------------------------------------------------------- mode DEMO (cluster simulé)
NODES = ["docker-desktop-worker", "docker-desktop-worker2"]


class Sim:
    CPU_MS, IDLE_M, LIMIT_M, REQ_M, MIN, MAX = 19, 8, 400, 200, 3, 8  # mesuré : ~19 ms de CPU par requête

    def __init__(s):
        s.pods, s.target, s.rate, s.users, s.hpa_t, s.low = [], 0, 5, 0.0, 0.0, None
        for _ in range(s.MIN):
            s.add(warm=False, age=60)

    def add(s, warm=False, age=0.0):
        node = min(NODES, key=lambda n: sum(p["node"] == n for p in s.pods))  # comme topologySpreadConstraints
        name = "ml-api-6d9f7c8b5-" + "".join(random.choices("bcdfghjklmnpqrstvwxz2456789", k=5))
        s.pods.append({"name": name, "node": node, "ip": f"10.244.{random.randint(1, 3)}.{random.randint(2, 250)}",
                       "start": time.time() - age, "warm": warm, "restarts": 0, "last": None, "dead": 0, "err": 0, "sick": 0})

    def status(s, p, now):
        if p["dead"]:
            return "Terminating"
        if p["err"] > now:
            return "Error"
        a = now - p["start"]
        if p["warm"]:
            return "Starting" if a < 3 else "Ready"
        return "Pending" if a < 1 else "ContainerCreating" if a < 3 else "Starting" if a < 7 else "Ready"

    def crash(s, p, now):
        p.update(err=now + 2, start=now + 2, warm=True, sick=0, restarts=p["restarts"] + 1, last="Error")

    def chaos(s, action):
        now = time.time()
        ready = [p for p in s.pods if s.status(p, now) == "Ready"]
        if not ready:
            raise HTTPException(409, "aucun pod prêt")
        p = random.choice(ready)
        if action == "delete":
            p["dead"] = now
            s.add()  # le ReplicaSet recrée aussitôt le pod manquant
        elif action == "crash":
            s.crash(p, now)
        else:
            p["sick"] = now
        return p["name"]

    def step(s):
        now = time.time()
        s.users += max(-s.rate, min(s.rate, s.target - s.users))  # montée progressive, comme Locust
        for p in list(s.pods):
            if p["dead"] and now - p["dead"] > 6:
                s.pods.remove(p)
            elif p["sick"] and now - p["sick"] > 15:  # la liveness probe finit par tuer le conteneur
                s.crash(p, now)
        rows = []
        stats = [(p, s.status(p, now)) for p in s.pods]
        ready = [p for p, st in stats if st == "Ready"]
        cap_pod = (s.LIMIT_M - s.IDLE_M) / s.CPU_MS  # requêtes/s qu'un pod peut servir avant d'atteindre sa limite CPU
        cap = cap_pod * len(ready)
        demand = s.users * 0.5 * random.uniform(0.93, 1.07)
        served = min(demand, cap)
        # saturé, les utilisateurs attendent : latence = utilisateurs / débit - temps de réflexion (2 s)
        wait = max(0.0, s.users / cap - 2.0) * 1000 if ready and demand > cap else 0.0
        for p, st in stats:
            share = min(served / len(ready) * random.uniform(0.9, 1.1), cap_pod) if st == "Ready" and ready else 0
            need = share * s.CPU_MS + s.IDLE_M
            cpu = min(need, s.LIMIT_M) if st == "Ready" else 60 if st == "Starting" else 5
            lat = s.CPU_MS + 6 * need / s.LIMIT_M + wait * random.uniform(0.9, 1.1)
            rows.append({"name": p["name"], "node": p["node"], "ip": p["ip"], "status": st, "restarts": p["restarts"],
                         "last": p["last"], "cpu_m": round(cpu, 1), "mem_mi": 175 + random.randint(0, 6), "req_m": s.REQ_M,
                         "util": round(cpu / s.REQ_M * 100), "rps": round(share, 1),
                         "lat": round(lat) if st == "Ready" and share else 0})
        live = [r for r in rows if r["status"] == "Ready"]
        util = sum(r["util"] for r in live) / len(live) if live else 0
        current = len([p for p in s.pods if not p["dead"]])
        want = max(s.MIN, min(s.MAX, math.ceil(len(live) * util / 50))) if live else s.MIN
        if now - s.hpa_t >= 15:  # l'HPA réel se réveille toutes les 15 s
            s.hpa_t = now
            if want > current:  # montée immédiate : +100 % ou +4 pods par période, le plus grand des deux
                for _ in range(min(want - current, max(current, 4))):
                    s.add()
                s.low = None
            elif want < current:  # descente : 60 s de sous-charge continue, puis 1 pod toutes les 30 s
                s.low = s.low or now
                if now - s.low >= 60:
                    next(p for p in s.pods if not p["dead"])["dead"] = now
                    s.low = now - 30
            else:
                s.low = None
        state = "stopped" if s.target == 0 and s.users == 0 else "spawning" if s.users != s.target else "running"
        loc = {"state": state, "users": int(s.users), "rps": round(sum(r["rps"] for r in rows), 1), "fail": 0}
        hpa = {"min": s.MIN, "max": s.MAX, "target": 50, "util": round(util), "current": current, "desired": max(want, 0)}
        return rows, hpa, loc


SIM = Sim() if DEMO else None


async def sim_snapshot():
    publish(*SIM.step())


# ----------------------------------------------------------------- serveur web
@asynccontextmanager
async def lifespan(_):
    global KC, PC, LC
    KC = None if DEMO else httpx.AsyncClient(verify=f"{SA}/ca.crt", timeout=3)
    PC, LC = httpx.AsyncClient(timeout=0.8), httpx.AsyncClient(timeout=3)
    task = asyncio.create_task(poll())
    yield
    task.cancel()


app = FastAPI(title="ml-dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/events")
async def events():
    async def gen():
        first = True
        while True:
            msg = dict(STATE, log=list(LOG))
            if first:  # à la connexion, on envoie l'historique pour que les courbes ne repartent pas de zéro
                msg["history"], first = list(HISTORY), False
            yield f"data: {json.dumps(msg)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/load/{profile}")
async def load(profile: str):
    if profile != "stop" and profile not in PROFILES:
        raise HTTPException(404, "profil inconnu")
    users, rate = PROFILES.get(profile, (0, 100))
    if DEMO:
        SIM.target, SIM.rate = users, rate
        return {"users": users}
    try:
        r = await (LC.get(f"{LOCUST}/stop") if profile == "stop"
                   else LC.post(f"{LOCUST}/swarm", data={"user_count": users, "spawn_rate": rate}))
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Locust injoignable ({type(e).__name__}) : le déploiement loadtest/ est-il appliqué ?")
    return {"users": users}


@app.post("/chaos/{action}")
async def chaos(action: str):
    if not CHAOS:
        raise HTTPException(403, "pannes désactivées (ENABLE_CHAOS=1 requis)")
    if action not in ("delete", "crash", "sick"):
        raise HTTPException(404, "action inconnue")
    if DEMO:
        return {"pod": SIM.chaos(action)}
    ready = [p for p in STATE["pods"] if p["status"] == "Ready"]
    if not ready:
        raise HTTPException(409, "aucun pod prêt")
    p = random.choice(ready)
    try:
        if action == "delete":
            await k8s("DELETE", f"/api/v1/namespaces/{NS}/pods/{p['name']}")
        else:
            await PC.post(f"http://{p['ip']}:8000/{action}")
    except (httpx.ReadError, httpx.RemoteProtocolError):
        pass  # /crash coupe la connexion : c'est le résultat attendu
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(502, str(e))
    return {"pod": p["name"]}
