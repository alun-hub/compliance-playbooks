# Fedora Compliance & Härdsäkring

Centralt system för att övervaka och verifiera att en stor installationsbas av Fedora-klienter är korrekt konfigurerade och härdade. Klienter som inte uppfyller säkerhetskraven eller slutar rapportera karantäneras automatiskt från nätverket.

## Dokumentation

| Dokument | Innehåll |
|---|---|
| [docs/deployment.md](docs/deployment.md) | Komplett steg-för-steg för driftsättning i produktion |
| [docs/compliance-tuning.md](docs/compliance-tuning.md) | Justera karantänregler, skippa OpenSCAP-regler, tailoring-filer |

---

## Bakgrund och mål

I en stor miljö med många Fedora-klienter är det svårt att garantera att säkerhetskonfigurationer förblir intakta. En användare eller ett intrång kan stänga av `auditd`, sätta SELinux i permissive-läge eller stoppa loggvidebefordran — utan att det märks centralt. Det räcker inte att konfigurera klienterna rätt en gång; de måste kontinuerligt verifiera sig själva och rapportera sin status.

Systemet löser detta genom tre sammankopplade delar:

1. **Klienterna självgranskar sig** via Ansible och OpenSCAP och rapporterar till en central S3-bucket
2. **En compliance-motor** korrelerar rapporterna mot aktiva nätverkssessioner i Cisco ISE
3. **Klienter som inte uppfyller kraven** karantäneras automatiskt via ISE ANC

---

## Arkitektur

```
┌─────────────────────────────────────────────────────────────┐
│  Fedora-klient                                              │
│                                                             │
│  systemd timer (var 30:e min)                               │
│       │                                                     │
│       ▼                                                     │
│  ansible-pull ──► Git-repo                                  │
│       │                                                     │
│       ▼                                                     │
│  self_check   ── Är compliance-timern aktiv?                │
│  security_checks  auditd, SELinux, rsyslog, firewalld       │
│  openscap     ── CIS-scan (en gång per dygn)                │
│  report       ── Bygg JSON + pusha till S3                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ latest.json per klient
                           ▼
                    ┌─────────────┐
                    │     S3      │
                    │ compliance/ │
                    │  <host>/    │
                    │ latest.json │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   ┌─────────────┐  ┌───────────┐  ┌──────────────┐
   │ Compliance- │  │  Grafana  │  │  Cisco ISE   │
   │   motor     │  │ Dashboard │  │  (802.1x)    │
   │ (var 15min) │  └───────────┘  └──────┬───────┘
   └──────┬──────┘                        │
          │ ISE ERS API                   │
          ├───────────────────────────────┘
          │ Aktiva sessioner (MAC-adresser)
          │
          ▼
     Klient tyst eller
     bryter mot policy?
          │
          ▼
   ISE ANC → Karantän-VLAN
```

---

## Repostruktur

```
compliance-playbooks/
├── local.yml                        # Ansible entry point (körs av ansible-pull)
├── group_vars/all.yml               # Standardvärden för alla klienter
├── bootstrap.yml                    # Engångskörning för att sätta upp nya klienter
├── roles/
│   ├── self_check/                  # Verifiera att timern lever
│   ├── security_checks/             # auditd, SELinux, rsyslog, firewalld
│   ├── openscap/                    # CIS-benchmark scan
│   └── report/                      # Bygg JSON-rapport och pusha till S3
├── systemd/
│   ├── ansible-pull.service         # Läser konfiguration från /etc/fedora-compliance/
│   └── ansible-pull.timer           # Kör var 30:e minut
├── packaging/
│   ├── fedora-compliance-client.spec  # RPM SPEC-fil
│   ├── build-rpm.sh                   # Byggskript
│   ├── client.conf                    # Konfigmall (git-URL, branch)
│   └── vars.yml                       # Ansible-variabelmall (S3-bucket m.m.)
├── compliance-engine/               # Central compliance-motor (Python)
│   ├── engine/
│   │   ├── ise.py                   # Cisco ISE ERS API-klient
│   │   ├── s3.py                    # Läser compliance-rapporter från S3
│   │   ├── evaluator.py             # Beslutslogik (karantän/alert/ok)
│   │   ├── db.py                    # Skriver verdikter till PostgreSQL
│   │   └── main.py                  # Huvudloop
│   ├── config.yaml                  # Konfiguration (ISE, S3, PostgreSQL)
│   └── systemd/                     # Service och timer för compliance-motorn
├── grafana/
│   └── dashboard.json               # Grafana-dashboard (importeras manuellt)
├── iac/
│   ├── s3-bucket-policy.json        # Kräver KMS och TLS
│   ├── iam-client-policy.json       # Klienter får bara skriva sin egna rapport
│   └── iam-engine-policy.json       # Compliance-motorn får läsa alla rapporter
├── test/
│   ├── docker-compose-test.yaml     # MinIO, PostgreSQL, fake-ISE, Grafana
│   ├── fake-ise/                    # Mock av Cisco ISE ERS API (FastAPI)
│   ├── seed.py                      # Lägger in testrapporter i MinIO
│   ├── config.yaml                  # Compliance-motorkonfig för testmiljö
│   └── run-test.sh                  # Kör hela testflödet
└── docs/
    ├── deployment.md                # Produktionsdriftsättning steg-för-steg
    └── compliance-tuning.md         # Justera regler, tailoring-filer
```

---

## Komponenter

### Ansible-roller på klienterna

Körs lokalt på varje klient via `ansible-pull` och systemd timer. Git-repot är den enda källan till sanning.

**`self_check`** — körs alltid och först
Verifierar att `ansible-pull.timer` är aktiv. Om timern är inaktiverad skickas omedelbart en nödrapport till S3 med `emergency: true` — compliance-motorn triggar karantän.

**`security_checks`** — snabba systemkontroller
Kontrollerar `auditd`, SELinux (måste vara Enforcing), `rsyslog` och konfigurerad logg-forwarding, `firewalld` samt att auditd-regler är laddade.

**`openscap`** — CIS-benchmarkscan
Kör `oscap xccdf eval` mot Fedoras CIS-profil en gång per dygn. Resultatet parsas till score, pass/fail-antal och lista med misslyckade regler sorterade efter allvarlighetsgrad.

**`report`** — sammanställer och skickar
Bygger ett JSON-dokument och pushar till `s3://bucket/compliance/<hostname>/latest.json`. Rapporten innehåller MAC-adress (för ISE-korrelation) och tidsstämpel (för att detektera tysta klienter).

#### Timing

```
Systemd timer: var 30:e minut (± 5 min slumpmässig fördröjning)
  └── self_check        ~1 sekund
  └── security_checks   ~5 sekunder
  └── openscap          ~5–10 minuter (körs bara om >24h sedan senaste scan)
  └── report + S3-push  ~5 sekunder
```

---

### Compliance-motor

Körs på en central server var 15:e minut. Korrelerar aktiva ISE-sessioner mot S3-rapporter och vidtar åtgärder.

#### Beslutslogik (prioritetsordning)

| Tillstånd | Åtgärd | Motivering |
|---|---|---|
| Ingen rapport i S3 | **Karantän** | Klienten har aldrig checkat in |
| `emergency: true` i rapporten | **Karantän** | Compliance-timern har saboterats |
| Rapport äldre än 90 min | **Karantän** | Klienten är tyst trots aktiv nätverkssession |
| auditd stoppad | **Karantän** | Säkerhetsloggning utslagen |
| SELinux ej Enforcing | **Karantän** | Mandatory access control utslagen |
| rsyslog stoppad | **Karantän** | Central loggning utslagen |
| rsyslog forwarding saknas | **Karantän** | Loggar når inte central plattform |
| OpenSCAP high severity > 0 | **Karantän** | Kritisk säkerhetsbrist |
| `compliant: false` (övriga) | **Alert** | Loggas men ingen nätverksåtgärd |
| Allt OK | Ingenting | — |

Klienter som är offline syns inte i ISE:s aktiva sessioner och granskas inte — det hanterar "kommer och går"-problematiken naturligt.

---

## Rapportformat (S3)

```json
{
  "schema_version": "1",
  "hostname": "laptop-42",
  "fqdn": "laptop-42.example.com",
  "timestamp": "2026-03-05T08:15:00+00:00",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "os": {
    "distribution": "Fedora",
    "version": "41",
    "kernel": "6.12.0-200.fc41.x86_64"
  },
  "oscap": {
    "score": 84.5,
    "pass": 156,
    "fail": 28,
    "notapplicable": 12,
    "high_severity_failures": 0,
    "failed_rules": [
      {"id": "xccdf_org.ssgproject.content_rule_package_aide_installed", "severity": "medium"}
    ]
  },
  "security_checks": {
    "auditd_running": true,
    "selinux_enforcing": true,
    "rsyslog_running": true,
    "rsyslog_forwarding_configured": true,
    "firewalld_running": true,
    "audit_rules_loaded": true,
    "timer_intact": true
  },
  "compliant": true
}
```

---

## Konfiguration

### Klienter — `/etc/fedora-compliance/`

Med RPM-paketet installerat konfigureras klienterna via två filer:

**`/etc/fedora-compliance/client.conf`** — git-URL (läses av systemd):
```ini
COMPLIANCE_GIT_URL=https://git.intern.example.com/infra/compliance-playbooks.git
COMPLIANCE_GIT_BRANCH=main
```

**`/etc/fedora-compliance/vars.yml`** — Ansible-variabler:
```yaml
s3_bucket: ert-compliance-bucket
oscap_profile: xccdf_org.ssgproject.content_profile_cis_workstation_l1
oscap_max_age_minutes: 1440

# On-prem S3 (MinIO, Ceph m.fl.) — utelämna för AWS:
# s3_endpoint_url: https://minio.intern.example.com
# s3_access_key: nyckel
# s3_secret_key: hemlighet
# s3_sse: false
```

`group_vars/all.yml` i repot innehåller standardvärden — `vars.yml` på klienten overridar per-miljö.

### Compliance-motor — `compliance-engine/config.yaml`

```yaml
ise:
  host: ise.intern.example.com
  username: compliance-svc
  password: "${ISE_PASSWORD}"   # Sätts via /etc/compliance-engine/secrets.env
  verify_ssl: true

s3:
  bucket: ert-compliance-bucket

evaluation:
  max_report_age_minutes: 90
  grace_period_minutes: 10
  quarantine_policy: Quarantine  # Måste matcha ANC-policy i ISE

database:
  dsn: "postgresql://compliance:<lösenord>@localhost/compliance"

dry_run: false   # Sätt till true för att testa utan att faktiskt karantänera
```

---

## RPM-paketering

Klientinstallationen distribueras som ett RPM-paket.

```bash
# Installera byggverktyg
sudo dnf install -y rpm-build rpmdevtools

# Bygg RPM från repots rot
./packaging/build-rpm.sh 1.0.0

# Resultat: ~/rpmbuild/RPMS/noarch/fedora-compliance-client-1.0.0-1.fcXX.noarch.rpm
```

RPM:en installerar systemd-enheter och konfigmallar, kräver `ansible-core`, `openscap-scanner`, `scap-security-guide` och `awscli2` som beroenden, och aktiverar timern automatiskt.

```bash
# Installera på klient
sudo dnf install fedora-compliance-client-1.0.0-1.fc43.noarch.rpm

# Konfigurera och starta
sudo vi /etc/fedora-compliance/client.conf
sudo vi /etc/fedora-compliance/vars.yml
sudo systemctl start ansible-pull.service
```

---

## Testmiljö

Kör hela systemet lokalt med podman-compose:

```bash
# Starta MinIO, PostgreSQL, fake-ISE och Grafana
podman-compose -f test/docker-compose-test.yaml up -d --build

# Lägg in testrapporter i MinIO
python3 test/seed.py

# Kör compliance-motorn mot testmiljön
PYTHONPATH=compliance-engine COMPLIANCE_CONFIG=test/config.yaml python3 -m engine.main

# Grafana: http://localhost:3000
# MinIO-konsol: http://localhost:9001  (minioadmin/minioadmin)
```

Testmiljön innehåller 6 förkonfigurerade scenarier (1 OK, 1 Alert, 4 karantäner) via en fake-ISE som simulerar Cisco ISE ERS API.

---

## Testläge

Sätt `dry_run: true` i `config.yaml` för att köra compliance-motorn utan att faktiskt karantänera. Alla beslut loggas med prefixet `[DRY-RUN]`.

OpenSCAP kan testas manuellt:

```bash
sudo oscap xccdf eval \
  --profile xccdf_org.ssgproject.content_profile_cis_workstation_l1 \
  --results /tmp/oscap-result.xml \
  /usr/share/xml/scap/ssg/content/ssg-fedora-ds.xml

python3 roles/openscap/files/parse_oscap.py /tmp/oscap-result.xml
```
