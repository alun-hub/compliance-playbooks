"""
Compliance-motor - huvudloop.
Körs av systemd timer var X:e minut.
"""

import logging
import os
import sys

import yaml

from .db import ComplianceDB
from .evaluator import Action, evaluate
from .ise import ISEClient
from .s3 import S3ReportStore

log = logging.getLogger(__name__)


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
    )


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run(config: dict) -> dict:
    ise_cfg = config["ise"]
    s3_cfg = config["s3"]
    eval_cfg = config.get("evaluation", {})

    ise = ISEClient(
        host=ise_cfg["host"],
        username=ise_cfg["username"],
        password=ise_cfg["password"],
        verify_ssl=ise_cfg.get("verify_ssl", True),
        port=ise_cfg.get("port", 9060),
        scheme=ise_cfg.get("scheme", "https"),
    )

    store = S3ReportStore(
        bucket=s3_cfg["bucket"],
        prefix=s3_cfg.get("prefix", "compliance/"),
        endpoint_url=s3_cfg.get("endpoint_url"),
        access_key=s3_cfg.get("access_key"),
        secret_key=s3_cfg.get("secret_key"),
    )

    sessions = ise.get_active_sessions()
    reports = store.load_all()

    verdicts = evaluate(
        sessions=sessions,
        reports=reports,
        max_report_age_minutes=eval_cfg.get("max_report_age_minutes", 90),
        grace_period_minutes=eval_cfg.get("grace_period_minutes", 10),
    )

    stats = {"ok": 0, "alert": 0, "quarantine": 0, "quarantine_errors": 0}
    dry_run = config.get("dry_run", False)

    for verdict in verdicts:
        stats[verdict.action.value] = stats.get(verdict.action.value, 0) + 1

        if verdict.action == Action.QUARANTINE:
            if dry_run:
                log.warning("[DRY-RUN] Skulle karantänera %s: %s", verdict.session.mac_address, verdict.reason)
            else:
                ok = ise.quarantine(
                    verdict.session.mac_address,
                    policy=eval_cfg.get("quarantine_policy", "Quarantine"),
                )
                if not ok:
                    stats["quarantine_errors"] += 1

    if "database" in config:
        try:
            db = ComplianceDB(config["database"]["dsn"])
            db.write_verdicts(verdicts)
        except Exception:
            log.exception("Kunde inte skriva till databasen")

    log.info(
        "Körning klar | sessioner=%d ok=%d alert=%d karantän=%d fel=%d",
        len(sessions),
        stats["ok"],
        stats["alert"],
        stats["quarantine"],
        stats["quarantine_errors"],
    )

    return stats


def main():
    config_path = os.environ.get("COMPLIANCE_CONFIG", "/etc/compliance-engine/config.yaml")
    setup_logging()

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        log.error("Konfigurationsfil hittades inte: %s", config_path)
        sys.exit(1)

    try:
        run(config)
    except Exception:
        log.exception("Oväntat fel i compliance-motorn")
        sys.exit(1)


if __name__ == "__main__":
    main()
