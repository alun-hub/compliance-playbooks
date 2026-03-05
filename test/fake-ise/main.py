"""
Fake Cisco ISE ERS API för testning.

Exponerar de endpoints som compliance-motorn använder:
  GET  /ers/config/activesessions  - returnerar hårdkodade testsessioner
  PUT  /ers/config/ancendpoint/apply  - loggar karantänförfrågan
  PUT  /ers/config/ancendpoint/clear  - loggar frigörning

Testsessioner (MAC → scenario):
  aa:bb:cc:dd:ee:01  compliant
  aa:bb:cc:dd:ee:02  ej compliant (OpenSCAP-brister)
  aa:bb:cc:dd:ee:03  ingen rapport i S3  → karantän
  aa:bb:cc:dd:ee:04  gammal rapport       → karantän
  aa:bb:cc:dd:ee:05  nödrapport (timer)   → karantän
  aa:bb:cc:dd:ee:06  SELinux disabled     → karantän
"""

import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fake-ise")

app = FastAPI(title="Fake Cisco ISE ERS API")

SESSIONS = [
    {"callingStationId": "56-F7-B5-84-DE-AE", "userName": "alun",   "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/0"},
    {"callingStationId": "AA-BB-CC-DD-EE-01", "userName": "alice",   "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/1"},
    {"callingStationId": "AA-BB-CC-DD-EE-02", "userName": "bob",     "nasIpAddress": "10.0.0.1", "nasPortId": "GigabitEthernet1/0/2"},
    {"callingStationId": "AA-BB-CC-DD-EE-03", "userName": "carol",   "nasIpAddress": "10.0.0.2", "nasPortId": "GigabitEthernet1/0/1"},
    {"callingStationId": "AA-BB-CC-DD-EE-04", "userName": "dave",    "nasIpAddress": "10.0.0.2", "nasPortId": "GigabitEthernet1/0/2"},
    {"callingStationId": "AA-BB-CC-DD-EE-05", "userName": "eve",     "nasIpAddress": "10.0.0.3", "nasPortId": "GigabitEthernet1/0/1"},
    {"callingStationId": "AA-BB-CC-DD-EE-06", "userName": "frank",   "nasIpAddress": "10.0.0.3", "nasPortId": "GigabitEthernet1/0/2"},
]

quarantined: list[dict] = []


@app.get("/ers/config/activesessions")
def active_sessions():
    log.info("GET activesessions → returnerar %d sessioner", len(SESSIONS))
    return JSONResponse({
        "SearchResult": {
            "total": len(SESSIONS),
            "resources": SESSIONS,
        }
    })


@app.put("/ers/config/ancendpoint/apply")
async def apply_anc(request: Request):
    body = await request.json()
    data = {d["name"]: d["value"] for d in body["OperationAdditionalData"]["additionalData"]}
    mac = data.get("macAddress", "?")
    policy = data.get("policyName", "?")
    log.warning("🔒 KARANTÄN: mac=%s policy=%s", mac, policy)
    quarantined.append({"mac": mac, "policy": policy})
    return JSONResponse({}, status_code=200)


@app.put("/ers/config/ancendpoint/clear")
async def clear_anc(request: Request):
    body = await request.json()
    data = {d["name"]: d["value"] for d in body["OperationAdditionalData"]["additionalData"]}
    mac = data.get("macAddress", "?")
    log.info("🔓 FRIGÖR: mac=%s", mac)
    return JSONResponse({}, status_code=200)


@app.get("/ers/config/ancendpoint/quarantined")
def list_quarantined():
    """Hjälpendpoint för att se vad som karantänerats under testet."""
    return quarantined
