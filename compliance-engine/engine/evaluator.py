"""
Compliance-evaluation: korrelerar ISE-sessioner mot S3-rapporter
och bestämmer vilken åtgärd som ska vidtas.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .ise import Session
from .s3 import ComplianceReport

log = logging.getLogger(__name__)


class Action(Enum):
    OK = "ok"
    ALERT = "alert"           # Logga och larma - ingen nätverksåtgärd
    QUARANTINE = "quarantine" # Hård karantän via ISE ANC
    RELEASE = "release"       # Frigör från karantän - klienten är nu compliant


@dataclass
class Verdict:
    session: Session
    action: Action
    reason: str
    report: Optional[ComplianceReport] = None


def evaluate(
    sessions: list[Session],
    reports: dict[str, ComplianceReport],
    quarantined_macs: set[str],
    max_report_age_minutes: float = 90,
    grace_period_minutes: float = 10,
) -> list[Verdict]:
    """
    För varje ISE-autentiserad session — bestäm åtgärd.

    quarantined_macs: MAC-adresser som för närvarande är karantänerade i ISE.
    Används för att avgöra om en nu-compliant klient ska frigöras.
    """
    verdicts = []

    for session in sessions:
        report = reports.get(session.mac_address)
        verdict = _evaluate_session(
            session, report, quarantined_macs, max_report_age_minutes, grace_period_minutes
        )
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


def _session_age_minutes(session: Session) -> Optional[float]:
    """Minuter sedan klienten senast autentiserades mot ISE."""
    if not session.session_start:
        return None
    return (datetime.now(timezone.utc) - session.session_start).total_seconds() / 60


def _evaluate_session(
    session: Session,
    report: Optional[ComplianceReport],
    quarantined_macs: set[str],
    max_age: float,
    grace: float,
) -> Verdict:

    session_age = _session_age_minutes(session)
    in_quarantine = session.mac_address in quarantined_macs

    # Grace period: klienten har precis autentiserats och hinner inte ha
    # levererat en rapport ännu (t.ex. dator som startats efter dagar offline).
    # Ge den grace_period_minutes på sig innan vi agerar.
    if session_age is not None and session_age < grace:
        return Verdict(
            session=session,
            action=Action.OK,
            report=report,
            reason=f"Grace period — session {session_age:.0f} min gammal (grace: {grace:.0f} min)",
        )

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

    # Rapport för gammal — klienten är tyst
    if report.age_minutes > max_age:
        return Verdict(
            session=session,
            action=Action.QUARANTINE,
            report=report,
            reason=f"Rapport för gammal: {report.age_minutes:.0f} min (max {max_age:.0f} min)",
        )

    # Hård säkerhetscheck — dessa triggar karantän direkt
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

    # Klienten är nu compliant — frigör från karantän om den satt där
    if in_quarantine:
        return Verdict(
            session=session,
            action=Action.RELEASE,
            report=report,
            reason=f"Åter compliant (score: {report.raw.get('oscap', {}).get('score', '?')})",
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
