"""
Hämtar compliance-rapporter från S3.
Bygger ett index av senaste rapport per MAC-adress.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import boto3

log = logging.getLogger(__name__)


@dataclass
class ComplianceReport:
    hostname: str
    mac_address: str
    timestamp: datetime
    compliant: bool
    high_severity_failures: int
    security_checks: dict
    emergency: bool = False
    age_minutes: float = 0.0
    raw: dict = field(default_factory=dict)


class S3ReportStore:
    def __init__(self, bucket: str, prefix: str = "compliance/", endpoint_url: str = None,
                 access_key: str = None, secret_key: str = None):
        self.bucket = bucket
        self.prefix = prefix
        kwargs = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        self.client = boto3.client("s3", **kwargs)

    def load_all(self) -> dict[str, ComplianceReport]:
        """
        Laddar alla latest.json från S3.
        Returnerar dict indexerat på MAC-adress (lowercase, colon-separerat).
        """
        reports: dict[str, ComplianceReport] = {}
        paginator = self.client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith("/latest.json"):
                    continue

                try:
                    report = self._fetch(key)
                    if report:
                        reports[report.mac_address] = report
                except Exception as e:
                    log.warning("Kunde inte läsa %s: %s", key, e)

        log.info("Laddade %d compliance-rapporter från S3", len(reports))
        return reports

    def _fetch(self, key: str) -> Optional[ComplianceReport]:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        raw = json.loads(resp["Body"].read())

        mac = raw.get("mac_address", "").lower().replace("-", ":")
        if not mac:
            return None

        timestamp = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_minutes = (now - timestamp).total_seconds() / 60

        oscap = raw.get("oscap", {})
        security = raw.get("security_checks", {})

        report = ComplianceReport(
            hostname=raw.get("hostname", key),
            mac_address=mac,
            timestamp=timestamp,
            compliant=raw.get("compliant", False),
            high_severity_failures=oscap.get("high_severity_failures", 0),
            security_checks=security,
            emergency=raw.get("emergency", False),
            age_minutes=age_minutes,
            raw=raw,
        )
        return report
