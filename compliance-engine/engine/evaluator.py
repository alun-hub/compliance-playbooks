"""
Compliance-evaluation: korrelerar ISE-sessioner mot S3-rapporter
och bestämmer vilken åtgärd som ska vidtas.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .ise import Session
from .s3 import ComplianceReport

log = logging.getLogger(__name__)


class Action(Enum):
    OK = "ok"
    ALERT = "alert"           # Logga och larma - ingen nätverksåtgärd
    QUARANTINE = "quarantine" # Hård karantän via ISE ANC


@dataclass
class Verdict:
    session: Session
    action: Action
    reason: str
    report: Optional[ComplianceReport] = None


def evaluate(
    sessions: list[Session],
    reports: dict[str, ComplianceReport],
    max_report_age_minutes: float = 90,
    grace_period_minutes: float = 10,
) -> list[Verdict]:
    """
    För varje ISE-autentiserad session - bestäm åtgärd.

    Karantän-regler (hård):
      - Ingen rapport alls (och klienten är äldre än grace period)
      - Rapport är för gammal (klienten tyst)
      - Nödrapport (timer saboterad)
      - high_severity_failures > 0
      - Kärnfunktion inaktiverad: auditd, SELinux, rsyslog

    Alert-regler (mjuk):
      - compliant == False men inga hårda regler triggar
    """
    verdicts = []

    for session in sessions:
        report = reports.get(session.mac_address)
        verdict = _evaluate_session(session, report, max_report_age_minutes, grace_period_minutes)
        verdicts.append(verdict)

        log.info(
            "[%s] %s mac=%s nas=%s anledning=%s",
            verdict.action.value.upper(),
            session.username or "okänd",
            session.mac_address,
            session.nas_ip,
            verdict.reason,
        )

    return verdicts


def _evaluate_session(
    session: Session,
    report: Optional[ComplianceReport],
    max_age: float,
    grace: float,
) -> Verdict:

    # Ingen rapport alls
    if report is None:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            reason="Ingen compliance-rapport hittad i S3",
        )

    # Nödrapport - timer saboterad
    if report.emergency:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            report=report,
            reason="Nödrapport: ansible-pull timer inaktiverad",
        )

    # Rapport för gammal - klienten skickar inte data
    if report.age_minutes > max_age:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            report=report,
            reason=f"Rapport för gammal: {report.age_minutes:.0f} min (max {max_age:.0f} min)",
        )

    # Hård säkerhetscheck - dessa triggar karantän direkt
    hard_failures = _hard_security_failures(report)
    if hard_failures:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            report=report,
            reason=f"Kritiska säkerhetsbrister: {', '.join(hard_failures)}",
        )

    # OpenSCAP high severity
    if report.high_severity_failures > 0:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            report=report,
            reason=f"{report.high_severity_failures} OpenSCAP-regler av hög allvarlighetsgrad misslyckas",
        )

    # Mjuk compliance-brist - alert men ingen karantän
    if not report.compliant:
        return Verdict(
            session=session,
            action=Action.ALERT,
            report=report,
            reason=f"Klienten är ej compliant (score: {report.raw.get('oscap', {}).get('score', '?')})",
        )

    return Verdict(
        session=session,
        action=Action.OK,
        report=report,
        reason="Compliant",
    )


def _hard_security_failures(report: ComplianceReport) -> list[str]:
    """Kontroller vars misslyckande triggar omedelbar karantän."""
    checks = report.security_checks
    failures = []

    if not checks.get("auditd_running", True):
        failures.append("auditd stoppad")
    if not checks.get("selinux_enforcing", True):
        failures.append("SELinux ej enforcing")
    if not checks.get("rsyslog_running", True):
        failures.append("rsyslog stoppad")
    if not checks.get("rsyslog_forwarding_configured", True):
        failures.append("rsyslog forwarding ej konfigurerad")
    if not checks.get("timer_intact", True):
        failures.append("compliance-timer inaktiv")

    return failures
