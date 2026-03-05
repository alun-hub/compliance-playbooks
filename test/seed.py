#!/usr/bin/env python3
"""
Lägger in testrapporter i MinIO som matchar fake-ISE-sessionerna.

Scenario per MAC:
  aa:bb:cc:dd:ee:01  compliant, färsk rapport             → OK
  aa:bb:cc:dd:ee:02  ej compliant (medium-brister)        → ALERT
  aa:bb:cc:dd:ee:03  ingen rapport                        → KARANTÄN
  aa:bb:cc:dd:ee:04  rapport 3 timmar gammal              → KARANTÄN (tyst klient)
  aa:bb:cc:dd:ee:05  session 5 min gammal (grace period)  → OK (grace)
  aa:bb:cc:dd:ee:06  compliant men pre-karantänerad       → RELEASE (frigörs)
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.client import Config

ENDPOINT = "http://localhost:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
BUCKET = "compliance"

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4"),
)


def now():
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    return dt.isoformat()


REPORTS = {
    "laptop-01": {
        "mac": "aa:bb:cc:dd:ee:01",
        "timestamp": ts(now() - timedelta(minutes=15)),
        "compliant": True,
        "oscap": {"score": 91.0, "pass": 180, "fail": 18, "notapplicable": 10, "high_severity_failures": 0, "failed_rules": []},
        "security_checks": {"auditd_running": True, "selinux_enforcing": True, "rsyslog_running": True,
                             "rsyslog_forwarding_configured": True, "firewalld_running": True,
                             "audit_rules_loaded": True, "timer_intact": True},
    },
    "laptop-02": {
        "mac": "aa:bb:cc:dd:ee:02",
        "timestamp": ts(now() - timedelta(minutes=20)),
        "compliant": False,
        "oscap": {"score": 72.0, "pass": 144, "fail": 54, "notapplicable": 10, "high_severity_failures": 0,
                  "failed_rules": [
                      {"id": "xccdf_org.ssgproject.content_rule_package_aide_installed", "severity": "medium"},
                      {"id": "xccdf_org.ssgproject.content_rule_accounts_tmout", "severity": "medium"},
                  ]},
        "security_checks": {"auditd_running": True, "selinux_enforcing": True, "rsyslog_running": True,
                             "rsyslog_forwarding_configured": True, "firewalld_running": True,
                             "audit_rules_loaded": True, "timer_intact": True},
    },
    # laptop-03: ingen rapport läggs in → karantän
    "laptop-04": {
        "mac": "aa:bb:cc:dd:ee:04",
        "timestamp": ts(now() - timedelta(hours=3)),   # för gammal
        "compliant": True,
        "oscap": {"score": 88.0, "pass": 170, "fail": 20, "notapplicable": 8, "high_severity_failures": 0, "failed_rules": []},
        "security_checks": {"auditd_running": True, "selinux_enforcing": True, "rsyslog_running": True,
                             "rsyslog_forwarding_configured": True, "firewalld_running": True,
                             "audit_rules_loaded": True, "timer_intact": True},
    },
    # laptop-05: session bara 5 min gammal → grace period (rapporten spelar ingen roll)
    "laptop-05": {
        "mac": "aa:bb:cc:dd:ee:05",
        "timestamp": ts(now() - timedelta(minutes=5)),
        "emergency": True,
        "compliant": False,
        "oscap": {},
        "security_checks": {},
        "reason": "ansible-pull timer inaktiverad",
    },
    # laptop-06: pre-karantänerad i fake-ISE men har fixat SELinux → ska frigöras (RELEASE)
    "laptop-06": {
        "mac": "aa:bb:cc:dd:ee:06",
        "timestamp": ts(now() - timedelta(minutes=25)),
        "compliant": True,
        "oscap": {"score": 88.0, "pass": 170, "fail": 20, "notapplicable": 8, "high_severity_failures": 0, "failed_rules": []},
        "security_checks": {"auditd_running": True, "selinux_enforcing": True,
                             "rsyslog_running": True, "rsyslog_forwarding_configured": True,
                             "firewalld_running": True, "audit_rules_loaded": True, "timer_intact": True},
    },
}


def ensure_bucket():
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:
        s3.create_bucket(Bucket=BUCKET)
        print(f"Skapade bucket: {BUCKET}")


def upload_reports():
    for hostname, data in REPORTS.items():
        report = {
            "schema_version": "1",
            "hostname": hostname,
            "fqdn": f"{hostname}.test.local",
            "timestamp": data["timestamp"],
            "mac_address": data["mac"],
            "os": {"distribution": "Fedora", "version": "41", "kernel": "6.12.0"},
            "oscap": data.get("oscap", {}),
            "security_checks": data.get("security_checks", {}),
            "compliant": data.get("compliant", False),
            **({} if not data.get("emergency") else {"emergency": True, "reason": data.get("reason", "")}),
        }
        key = f"compliance/{hostname}/latest.json"
        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=json.dumps(report, indent=2).encode(),
            ContentType="application/json",
        )
        print(f"  ✓ {key}  ({data['mac']})")


if __name__ == "__main__":
    print("Ansluter till MinIO...")
    try:
        ensure_bucket()
    except Exception as e:
        print(f"Fel: {e}")
        print("Är MinIO igång på http://localhost:9000?")
        sys.exit(1)

    print(f"Laddar upp {len(REPORTS)} testrapporter:")
    upload_reports()
    print("\nKlart! Förväntade utfall:")
    print("  laptop-01  aa:bb:cc:dd:ee:01  → OK")
    print("  laptop-02  aa:bb:cc:dd:ee:02  → ALERT    (ej compliant, inga hårda brister)")
    print("  (ingen)    aa:bb:cc:dd:ee:03  → KARANTÄN (ingen rapport)")
    print("  laptop-04  aa:bb:cc:dd:ee:04  → KARANTÄN (rapport 3h gammal)")
    print("  laptop-05  aa:bb:cc:dd:ee:05  → OK       (grace period, session 5 min gammal)")
    print("  laptop-06  aa:bb:cc:dd:ee:06  → RELEASE  (compliant igen, frigörs från karantän)")
