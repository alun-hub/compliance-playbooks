"""
Skriver compliance-verdikter till PostgreSQL för Grafana-visualisering.

Schema skapas automatiskt vid första körning.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from .evaluator import Verdict

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_status (
    id              SERIAL PRIMARY KEY,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    hostname        TEXT,
    mac_address     TEXT NOT NULL,
    username        TEXT,
    nas_ip          TEXT,
    action          TEXT NOT NULL,
    reason          TEXT,
    oscap_score     NUMERIC,
    oscap_fail      INT,
    high_severity   INT,
    report_age_min  NUMERIC,
    compliant       BOOLEAN,
    selinux_ok      BOOLEAN,
    auditd_ok       BOOLEAN,
    rsyslog_ok      BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_compliance_mac      ON compliance_status (mac_address);
CREATE INDEX IF NOT EXISTS idx_compliance_time     ON compliance_status (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_action   ON compliance_status (action);
"""


class ComplianceDB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._ensure_schema()

    def _ensure_schema(self):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def write_verdicts(self, verdicts: list[Verdict]):
        now = datetime.now(timezone.utc)
        rows = []

        for v in verdicts:
            report = v.report
            oscap = report.raw.get("oscap", {}) if report else {}
            checks = report.security_checks if report else {}

            rows.append((
                now,
                report.hostname if report else None,
                v.session.mac_address,
                v.session.username,
                v.session.nas_ip,
                v.action.value,
                v.reason,
                oscap.get("score"),
                oscap.get("fail"),
                report.high_severity_failures if report else None,
                round(report.age_minutes, 1) if report else None,
                report.compliant if report else False,
                checks.get("selinux_enforcing"),
                checks.get("auditd_running"),
                checks.get("rsyslog_running"),
            ))

        with self._conn() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO compliance_status
                  (checked_at, hostname, mac_address, username, nas_ip,
                   action, reason, oscap_score, oscap_fail, high_severity,
                   report_age_min, compliant, selinux_ok, auditd_ok, rsyslog_ok)
                VALUES %s
                """,
                rows,
            )

        log.info("Sparade %d verdikter till databasen", len(rows))
