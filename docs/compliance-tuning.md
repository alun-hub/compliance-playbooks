# Compliance-tuning

## Beslutslogik — karantän vs alert

Compliance-motorn har två nivåer av åtgärder:

| Åtgärd | Effekt |
|---|---|
| **KARANTÄN** | Klienten placeras i karantän-VLAN via ISE ANC |
| **ALERT** | Händelsen loggas och hamnar i Grafana — ingen nätverksåtgärd |

Logiken finns i `compliance-engine/engine/evaluator.py`.

### Hårda regler (triggar karantän)

Funktionen `_hard_security_failures()` definierar vilka security_checks-brister
som alltid leder till karantän oavsett OpenSCAP-score:

```python
def _hard_security_failures(report: ComplianceReport) -> list[str]:
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
```

**Justera:** Ta bort en rad för att degradera till alert istället för karantän.
Exempel — om firewalld inte är ett krav i er miljö, ta bort den raden.

### OpenSCAP-tröskel

I `_evaluate_session()` triggar karantän om `high_severity_failures > 0`.
Justera till en högre tröskel om miljön inte är färdighärdad än:

```python
# Nuvarande — karantän vid minsta high-brist
if report.high_severity_failures > 0:

# Exempel — tolerera upp till 2 high-brister innan karantän
if report.high_severity_failures > 2:
```

### Specifika OpenSCAP-regler som alltid triggar karantän

Lägg till en lista med regel-ID:n som alltid leder till karantän oavsett allvarlighetsgrad:

```python
# I evaluator.py — lägg till konstant
ALWAYS_QUARANTINE_RULES = {
    "xccdf_org.ssgproject.content_rule_no_empty_passwords",
    "xccdf_org.ssgproject.content_rule_sshd_disable_root_login",
    "xccdf_org.ssgproject.content_rule_grub2_password",
}

# I _evaluate_session() — lägg till kontroll efter hard_failures
failed_ids = {r["id"] for r in report.raw.get("oscap", {}).get("failed_rules", [])}
critical_hits = failed_ids & ALWAYS_QUARANTINE_RULES
if critical_hits:
    return Verdict(
        session=session,
        action=Action.QUARANTINE,
        report=report,
        reason=f"Kritiska OpenSCAP-regler: {', '.join(critical_hits)}",
    )
```

---

## Skippa OpenSCAP-regler med tailoring-fil

Den rekommenderade metoden för att undanta regler är en **XCCDF tailoring-fil**.
Den är granskningsbar, versionshanterad och dokumenterar varför regler undantas.

### Skapa en tailoring-fil

```bash
# Interaktivt via SCAP Workbench (grafiskt verktyg)
dnf install scap-workbench

# Eller skapa manuellt — exempel nedan
```

Exempelfil `roles/openscap/files/fedora-tailoring.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<xccdf:Tailoring xmlns:xccdf="http://checklists.nist.gov/xccdf/1.2"
                 id="xccdf_compliance_tailoring_fedora">

  <xccdf:benchmark href="/usr/share/xml/scap/ssg/content/ssg-fedora-ds.xml"/>

  <xccdf:Profile id="xccdf_compliance_profile_fedora_custom"
                 extends="xccdf_org.ssgproject.content_profile_cis_workstation_l1">

    <xccdf:title>Anpassad CIS L1 - Fedora</xccdf:title>
    <xccdf:description>CIS L1 med lokala undantag</xccdf:description>

    <!-- Undanta regler som inte gäller i vår miljö -->

    <!-- Cron används inte — krav på cron.allow är irrelevant -->
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_file_cron_allow_exists"
                  selected="false"/>
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_file_cron_deny_not_exist"
                  selected="false"/>
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_file_permissions_crontab"
                  selected="false"/>

    <!-- AIDE är ersatt av annan FIM-lösning -->
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_package_aide_installed"
                  selected="false"/>
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_aide_build_database"
                  selected="false"/>
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_aide_periodic_cron_checking"
                  selected="false"/>

    <!-- rsync behövs för backup-klienter -->
    <xccdf:select idref="xccdf_org.ssgproject.content_rule_package_rsync_removed"
                  selected="false"/>

  </xccdf:Profile>
</xccdf:Tailoring>
```

### Aktivera tailoring-filen i Ansible-rollen

I `roles/openscap/tasks/main.yml`, lägg till `--tailoring-file` flaggan:

```yaml
- name: Kör OpenSCAP CIS-scan med tailoring
  command: >
    oscap xccdf eval
    --profile {{ oscap_profile }}
    --tailoring-file /usr/share/compliance/fedora-tailoring.xml
    --results {{ oscap_result_file }}
    {{ oscap_datastream }}
```

Kopiera tailoring-filen till klienterna via en extra task i openscap-rollen:

```yaml
- name: Installera tailoring-fil
  copy:
    src: fedora-tailoring.xml
    dest: /usr/share/compliance/fedora-tailoring.xml
    mode: '0644'
```

### Hitta regel-ID att undanta

```bash
# Lista alla regler i profilen
oscap info --profile xccdf_org.ssgproject.content_profile_cis_workstation_l1 \
  /usr/share/xml/scap/ssg/content/ssg-fedora-ds.xml | grep "Rule"

# Kör en scan och se vad som misslyckas — parse-skriptet listar regel-ID
python3 roles/openscap/files/parse_oscap.py /tmp/oscap-result.xml | \
  python3 -c "import json,sys; [print(r['severity'], r['id']) for r in json.load(sys.stdin)['failed_rules']]"
```

---

## Justera grace period och rapportålder

I `compliance-engine/config.yaml`:

```yaml
evaluation:
  # Hur gammal en rapport får vara innan karantän triggas.
  # Bör vara minst 3x ansible-pull-intervallets längd (30 min * 3 = 90 min)
  max_report_age_minutes: 90

  # Nyansluten klient får X minuter att leverera sin första rapport
  # innan compliance-motorn agerar. Sätt högt om klienterna tar tid på sig
  # att boota (OpenSCAP-scan tar 5-10 min vid första körning).
  grace_period_minutes: 10
```
