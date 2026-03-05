#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST="$ROOT/test"

echo "=== Startar testmiljö ==="
podman-compose -f "$TEST/docker-compose-test.yaml" up -d --build

echo ""
echo "=== Väntar på att tjänster ska bli redo ==="
for svc in "http://localhost:9000/minio/health/live" "http://localhost:9060/ers/config/activesessions"; do
    echo -n "  Väntar på $svc "
    for i in $(seq 1 20); do
        if curl -sf "$svc" -u admin:admin > /dev/null 2>&1; then
            echo " OK"
            break
        fi
        echo -n "."
        sleep 2
    done
done

echo ""
echo "=== Laddar testdata i MinIO ==="
cd "$ROOT"
pip install boto3 -q
python3 test/seed.py

echo ""
echo "=== Installerar compliance-engine ==="
pip install -e "$ROOT/compliance-engine" -q

echo ""
echo "=== Kör compliance-motorn ==="
echo "------------------------------------------------------------"
COMPLIANCE_CONFIG="$TEST/config.yaml" python3 -m engine.main
echo "------------------------------------------------------------"

echo ""
echo "=== Resultat i PostgreSQL ==="
psql postgresql://compliance:compliance@localhost/compliance \
    -c "SELECT hostname, mac_address, action, reason FROM compliance_status ORDER BY action DESC;"

echo ""
echo "=== Karantänerade (enligt fake-ISE) ==="
curl -sf http://localhost:9060/ers/config/ancendpoint/quarantined | python3 -m json.tool

echo ""
echo "=== Klar! ==="
echo "MinIO-konsol: http://localhost:9001  (minioadmin/minioadmin)"
echo "Stoppa miljön: podman-compose -f test/docker-compose-test.yaml down"
