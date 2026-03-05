"""
Cisco ISE ERS API-klient.
Hämtar aktiva sessioner och applicerar ANC-policys (karantän).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)


@dataclass
class Session:
    mac_address: str
    username: str
    nas_ip: str
    nas_port: Optional[str]
    vlan: Optional[str]
    session_start: Optional[datetime] = None  # När klienten senast autentiserades


class ISEClient:
    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = True,
                 port: int = 9060, scheme: str = "https"):
        self.base = f"{scheme}://{host}:{port}/ers"
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.session.verify = verify_ssl

    def get_active_sessions(self) -> list[Session]:
        """Hämtar alla aktiva 802.1x-autentiserade sessioner från ISE."""
        sessions = []
        page = 1

        while True:
            resp = self.session.get(
                f"{self.base}/config/activesessions",
                params={"size": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("SearchResult", {})
            resources = data.get("resources", [])

            for r in resources:
                mac = r.get("callingStationId", "").lower().replace("-", ":")
                if not mac:
                    continue

                session_start = None
                raw_start = r.get("sessionStartTime")
                if raw_start:
                    try:
                        session_start = datetime.fromisoformat(
                            raw_start.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                sessions.append(Session(
                    mac_address=mac,
                    username=r.get("userName", ""),
                    nas_ip=r.get("nasIpAddress", ""),
                    nas_port=r.get("nasPortId"),
                    vlan=r.get("vlan"),
                    session_start=session_start,
                ))

            if len(resources) < 100:
                break
            page += 1

        log.info("Hämtade %d aktiva ISE-sessioner", len(sessions))
        return sessions

    def quarantine(self, mac_address: str, policy: str = "Quarantine") -> bool:
        """Applicerar ANC-policy på en klient via MAC-adress."""
        resp = self.session.put(
            f"{self.base}/config/ancendpoint/apply",
            json={
                "OperationAdditionalData": {
                    "additionalData": [
                        {"name": "macAddress", "value": mac_address},
                        {"name": "policyName", "value": policy},
                    ]
                }
            },
            timeout=30,
        )

        if resp.status_code in (200, 204):
            log.warning("KARANTÄN applicerad: %s (policy: %s)", mac_address, policy)
            return True
        else:
            log.error("Misslyckades karantänera %s: HTTP %s %s", mac_address, resp.status_code, resp.text)
            return False

    def get_quarantined_macs(self, policy: str = "Quarantine") -> set[str]:
        """Hämtar MAC-adresser som för närvarande har ANC-policy applicerad i ISE."""
        macs = set()
        page = 1

        while True:
            resp = self.session.get(
                f"{self.base}/config/ancendpoint",
                params={"size": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("SearchResult", {})
            resources = data.get("resources", [])

            for r in resources:
                mac = r.get("id", "").lower().replace("-", ":")
                if mac:
                    macs.add(mac)

            if len(resources) < 100:
                break
            page += 1

        log.info("Hämtade %d karantänerade MAC-adresser från ISE", len(macs))
        return macs

    def release_quarantine(self, mac_address: str, policy: str = "Quarantine") -> bool:
        """Tar bort ANC-policy (frigör klient från karantän)."""
        resp = self.session.put(
            f"{self.base}/config/ancendpoint/clear",
            json={
                "OperationAdditionalData": {
                    "additionalData": [
                        {"name": "macAddress", "value": mac_address},
                        {"name": "policyName", "value": policy},
                    ]
                }
            },
            timeout=30,
        )
        if resp.status_code in (200, 204):
            log.info("Karantän borttagen: %s", mac_address)
            return True
        else:
            log.error("Misslyckades frigöra %s: HTTP %s", mac_address, resp.status_code)
            return False
