# Ouvre les écrans du projet et ferme les tunnels à l'appui sur Entrée.
# Atelier de chiffres : http://localhost:8001
# Plan du cluster     : http://localhost:8000
# Locust              : http://localhost:8089
# Grafana             : http://localhost:3000

$jobs = @()

$jobs += Start-Job -ScriptBlock {
    kubectl -n ml-platform port-forward svc/ml-api 8001:80
}

$jobs += Start-Job -ScriptBlock {
    kubectl -n ml-platform port-forward svc/ml-dashboard 8000:80
}

$jobs += Start-Job -ScriptBlock {
    kubectl -n loadtest port-forward svc/locust-master 8089:8089
}

$jobs += Start-Job -ScriptBlock {
    kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
}

try {
    Start-Sleep -Seconds 3

    # Vérifier si les tâches sont toujours actives
    $jobs | Format-Table Id, State

    Start-Process "http://localhost:8000"
    Start-Process "http://localhost:8001"

    Read-Host "Tunnels ouverts. Appuie sur Entrée pour les fermer"
}
finally {
    $jobs | Stop-Job -ErrorAction SilentlyContinue
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
}