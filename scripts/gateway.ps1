# Ouvre l'UNIQUE tunnel vers la Gateway, puis tout se joint par nom d'hôte sur le port 8080 :
#   http://app.localhost:8080       atelier de chiffres        http://grafana.localhost:8080   Grafana
#   http://cluster.localhost:8080   plan du cluster            http://argocd.localhost:8080    ArgoCD
#   http://locust.localhost:8080    Locust
# Remplace les trois port-forward de screens.ps1 : un seul tunnel vers Envoy, qui route ensuite selon l'en-tête Host.
# (Le tunnel s'accroche à UN pod Envoy ; c'est Envoy qui répartit ensuite chaque requête entre les pods de ml-api.)
# Si le tunnel tombe, relance le script.
$job = Start-Job { kubectl -n envoy-gateway-system port-forward svc/envoy-lab 8080:80 }
Start-Sleep -Seconds 3
Start-Process "http://app.localhost:8080"
Start-Process "http://cluster.localhost:8080"
Read-Host "Tunnel ouvert sur le port 8080. Appuie sur Entrée pour le fermer"
$job | Stop-Job
$job | Remove-Job
