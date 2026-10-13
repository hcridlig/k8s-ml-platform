"""Entraîne le modèle au build de l'image (docker build). Le modèle est donc figé dans l'image :
une version d'image = une version de modèle, ce qui rend les rollouts reproductibles."""
import json

import joblib
from sklearn.datasets import load_digits
from sklearn.ensemble import RandomForestClassifier

X, y = load_digits(return_X_y=True)  # jeu de données fourni avec scikit-learn, aucun téléchargement
# 300 arbres : volontairement assez coûteux en CPU pour que l'autoscaling soit visible plus tard
model = RandomForestClassifier(n_estimators=300, random_state=0).fit(X, y)
joblib.dump(model, "model.joblib")
print("accuracy (train):", model.score(X, y))

# 20 vrais chiffres par classe, embarqués dans l'image : ils alimentent les boutons "Chiffre au hasard" et "Rafale"
# de l'interface sans qu'elle ait besoin de dessiner quoi que ce soit.
samples = {d: [X[i].astype(int).tolist() for i in (y == d).nonzero()[0][:20]] for d in range(10)}
with open("samples.json", "w") as f:
    json.dump(samples, f)
