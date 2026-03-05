"""
Fake Cisco ISE ERS API för testning.

Exponerar de endpoints som compliance-motorn använder:
  GET  /ers/config/activesessions      - returnerar hårdkodade testsessioner
  GET  /ers/config/ancendpoint         - returnerar karantänerade MAC-adresser
  PUT  /ers/config/ancendpoint/apply   - loggar karantänförfrågan
  PUT  /ers/config/ancendpoint/clear   - loggar frigörning

Testsessioner (MAC → scenario):
  aa:bb:cc:dd:ee:01  compliant
  aa:bb:cc:dd:ee:02  ej compliant (OpenSCAP-brister)
  aa:bb:cc:dd:ee:03  ingen rapport i S3  → karantän
  aa:bb:cc:dd:ee:04  gammal rapport       → karantän
  aa:bb:cc:dd:ee:05  nödrapport (timer)   → karantän
  aa:bb:cc:dd:ee:06  SELinux disabled     → karantän (men simuleras redan frigiven)
"""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fake-ise")

app = FastAPI(title="Fake Cisco ISE ERS API")


def _ts(minutes_ago: int = 60) -> str:
    """ISO-tidsstämpel X minuter sedan."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


SESSIONS = [
    {"callingStationId": "56-F7-B5-84-DE-AE", "userName": "alun",  "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/0", "sessionStartTime": _ts(120)},
    {"callingStationId": "AA-BB-CC-DD-EE-01", "userName": "alice",  "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/1", "sessionStartTime": _ts(120)},
    {"callingStationId": "AA-BB-CC-DD-EE-02", "userName": "bob",    "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/2", "sessionStartTime": _ts(120)},
    {"callingStationId": "AA-BB-CC-DD-EE-03", "userName": "carol",  "nasIpAddress": "10.0.0.2", "nasPortId": "GigabitEthernet1/0/1", "sessionStartTime": _ts(120)},
    {"callingStationId": "AA-BB-CC-DD-EE-04", "userName": "dave",   "nasIpAddress": "10.0.0.2", "nasPortId": "GigabitEthernet1/0/2", "sessionStartTime": _ts(120)},
    {"callingStationId": "AA-BB-CC-DD-EE-05", "userName": "eve",    "nasIpAddress": "10.0.0.3", "nasPortId": "GigabitEthernet1/0/1", "sessionStartTime": _ts(5)},   # grace period
    {"callingStationId": "AA-BB-CC-DD-EE-06", "userName": "frank",  "nasIpAddress": "10.0.0.3", "nasPortId": "GigabitEthernet1/0/2", "sessionStartTime": _ts(120)},
]

# Simulerar att ee:06 redan är karantänerad (men nu compliant igen → ska frigöras)
quarantined: dict[str, str] = {"aa:bb:cc:dd:ee:06": "Quarantine"}


@app.get("/ers/config/activesessions")
def active_sessions():
    log.info("GET activesessions → returnerar %d sessioner", len(SESSIONS))
    return JSONResponse({
        "SearchResult": {
            "total": len(SESSIONS),
            "resources": SESSIONS,
        }
    })


@app.get("/ers/config/ancendpoint")
def list_anc_endpoints(filter: str = ""):
    """Returnerar endpoints med ANC-policy. Stödjer filter=policyName.EQ.<policy>."""
    if filter.startswith("policyName.EQ."):
        policy_filter = filter.split("policyName.EQ.", 1)[1]
        filtered = {mac: pol for mac, pol in quarantined.items() if pol == policy_filter}
    else:
        filtered = quarantined

    resources = [{"id": mac} for mac in filtered]
    log.info("GET ancendpoint filter=%r → %d karantänerade", filter, len(resources))
    return JSONResponse({
        "SearchResult": {
            "total": len(resources),
            "resources": resources,
        }
    })


@app.put("/ers/config/ancendpoint/apply")
async def apply_anc(request: Request):
    body = await request.json()
    data = {d["name"]: d["value"] for d in body["OperationAdditionalData"]["additionalData"]}
    mac = data.get("macAddress", "?")
    policy = data.get("policyName", "?")
    log.warning("KARANTÄN: mac=%s policy=%s", mac, policy)
    quarantined[mac] = policy
    return JSONResponse({}, status_code=200)


@app.put("/ers/config/ancendpoint/clear")
async def clear_anc(request: Request):
    body = await request.json()
    data = {d["name"]: d["value"] for d in body["OperationAdditionalData"]["additionalData"]}
    mac = data.get("macAddress", "?")
    log.info("FRIGÖR: mac=%s", mac)
    quarantined.pop(mac, None)
    return JSONResponse({}, status_code=200)
