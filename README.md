# Fedora Compliance & Härdsäkring

Centralt system för att övervaka och verifiera att en stor installationsbas av Fedora-klienter är korrekt konfigurerade och härdade. Klienter som inte uppfyller säkerhetskraven eller slutar rapportera karantäneras automatiskt från nätverket.

## Dokumentation

| Dokument | Innehåll |
|---|---|
| [docs/deployment.md](docs/deployment.md) | Komplett steg-för-steg för driftsättning i produktion |
| [docs/compliance-tuning.md](docs/compliance-tuning.md) | Justera karantänregler, skippa OpenSCAP-regler, tailoring-filer |

## Bakgrund och mål

I en stor miljö med många Fedora-klienter är det svårt att garantera att säkerhetskonfigurationer förblir intakta. En användare eller ett intrång kan stänga av `auditd`, sätta SELinux i permissive-läge eller stoppa loggvidebefordran - utan att det märks centralt. Det räcker inte att konfigurera klienterna rätt en gång; de måste kontinuerligt verifiera sig själva och rapportera sin status.

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

## Komponenter

### compliance-playbooks/ — Ansible-roller på klienterna

Körs lokalt på varje klient via `ansible-pull` och systemd timer. Git-repot är den enda källan till sanning för vad som ska köras.

#### Roller

**`self_check`** — körs alltid och först
Verifierar att `ansible-pull.timer` är aktiv. Om timern är inaktiverad (t.ex. av en användare eller ett intrång) skickas omedelbart en nödrapport till S3 med `emergency: true`. Compliance-motorn ser detta och triggar karantän.

**`security_checks`** — snabba systemkontroller
Kontrollerar status på kritiska säkerhetstjänster:
- `auditd` — kernel audit-loggning
- SELinux — måste vara i `Enforcing`-läge
- `rsyslog` — loggning till central loggplattform
- `firewalld` — värdbaserad brandvägg
- Audit-regler — att faktiska regler är laddade

**`openscap`** — CIS-benchmarkscan
Kör `oscap xccdf eval` mot Fedoras CIS-profil. Eftersom en fullständig scan tar 5–10 minuter körs den bara en gång per dygn (`oscap_max_age_minutes: 1440`). Resultatet parsas av ett Python-skript och struktureras som:
- Total score (0–100)
- Antal pass/fail/notapplicable
- Lista med misslyckade regler, sorterade efter allvarlighetsgrad
- Antal `high`-regler som misslyckats (används av compliance-motorn)

**`report`** — sammanställer och skickar
Samlar fakta från de övriga rollerna och bygger ett JSON-dokument som pushas till S3 (`s3://bucket/compliance/<hostname>/latest.json`). Rapporten innehåller MAC-adress för korrelation mot ISE-sessioner och tidsstämpel för att detektera tysta klienter.

#### Timing

```
Systemd timer: var 30:e minut (± 5 min slumpmässig fördröjning)
  └── self_check        ~1 sekund
  └── security_checks   ~5 sekunder
  └── openscap          ~5–10 minuter (körs bara om >24h sedan senaste scan)
  └── report + S3-push  ~5 sekunder
```

---

### compliance-engine/ — Compliance-motor (central server)

Körs på en central server var 15:e minut via systemd timer. Korrelerar aktiva ISE-sessioner mot S3-rapporter och vidtar åtgärder.

#### Flöde

1. Hämtar alla aktiva 802.1x-sessioner från ISE via ERS REST API (MAC-adress, användare, switch-IP)
2. Laddar alla `latest.json` från S3 och indexerar dem på MAC-adress
3. För varje autentiserad session — utvärderar compliance-status
4. Triggar ISE ANC-policy vid regelbrott

#### Beslutslogik

Utvärderingen sker i prioritetsordning. Så fort ett villkor träffas fattas beslut utan att kontrollera resten.

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

**Varför 90 minuter och inte 30?**
Timern kör var 30:e minut med upp till 5 minuters slumpmässig fördröjning. En missad körning (t.ex. klienten var i viloläge) ska inte omedelbart trigga karantän. 90 minuter ger utrymme för en missad körning plus marginaler.

#### "Kommer och går"-problematiken

Klienter som är offline syns helt enkelt inte i ISE:s aktiva sessioner och granskas därför inte. Det är fullt normalt att laptops stängs av och slås på. Compliance-motorn agerar bara på klienter som faktiskt sitter på nätverket just nu.

#### ISE ANC (Adaptive Network Control)

I stället för att prata direkt med switcharna används ISE ANC. ISE skickar RADIUS CoA (Change of Authorization) till switchen och placerar klienten i karantän-VLAN. Förutsättning: en ANC-policy med namnet `Quarantine` måste skapas manuellt i ISE innan systemet tas i drift.

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
      {"id": "xccdf_org.ssgproject.content_rule_package_aide_installed", "severity": "medium"},
      ...
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

### compliance-playbooks/group_vars/all.yml

```yaml
s3_bucket: ditt-compliance-bucket
oscap_profile: xccdf_org.ssgproject.content_profile_cis_workstation_l1
oscap_max_age_minutes: 1440   # OpenSCAP körs en gång per dygn
```

### compliance-engine/config.yaml

```yaml
ise:
  host: ise.example.com
  username: compliance-svc
  password: "${ISE_PASSWORD}"
  verify_ssl: true

s3:
  bucket: ditt-compliance-bucket

evaluation:
  max_report_age_minutes: 90
  grace_period_minutes: 10
  quarantine_policy: Quarantine

dry_run: false   # Sätt till true för att testa utan att karantänera
```

---

## Driftsättning

### 1. Förkrav i ISE

Skapa en ANC-policy med namnet `Quarantine` i ISE (Administration → Network Resources → ANC Policies) innan systemet tas i drift. Sätt åtgärden till **QUARANTINE** (placerar klienten i karantän-VLAN via CoA).

Skapa ett ISE-tjänstkonto med:
- **ERS Read** — för att läsa aktiva sessioner
- **ERS Write** — för att applicera ANC-policys

### 2. S3-bucket och IAM

Ersätt `BUCKET_NAME` i `iac/`-filerna med faktiskt bucket-namn och applicera:

```bash
# Bucket-policy (kräver ingen kryptering)
aws s3api put-bucket-policy \
  --bucket ditt-compliance-bucket \
  --policy file://iac/s3-bucket-policy.json

# IAM-policy för klienter (en per klient via IAM Instance Profile eller IRSA)
aws iam create-policy \
  --policy-name FedoraComplianceClientWrite \
  --policy-document file://iac/iam-client-policy.json

# IAM-policy för compliance-motorn
aws iam create-policy \
  --policy-name FedoraComplianceEngineRead \
  --policy-document file://iac/iam-engine-policy.json
```

IAM-client-policyn begränsar varje klient till att bara skriva under `compliance/<eget-hostname>/` via `PrincipalTag/hostname`. Tagga varje klient-roll med `hostname`-taggen.

### 3. PostgreSQL för Grafana

```bash
createdb compliance
psql compliance -c "CREATE USER compliance WITH PASSWORD 'lösenord';"
psql compliance -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO compliance;"
# Schema skapas automatiskt vid första körning av compliance-motorn
```

### 4. Bootstrap av klienter

Kör bootstrap-playbooken mot nya klienter via AWX eller direkt:

```bash
# Uppdatera git_repo-variabeln i bootstrap.yml, sedan:
ansible-playbook -i "klient1,klient2," bootstrap.yml

# Eller mot en hel grupp via AWX Job Template
```

Bootstrap-playbooken installerar Ansible, sätter upp systemd-timern och triggar en första körning direkt. Klienten är självgående därefter.

### 5. Compliance-motor (central server)

```bash
# Skapa tjänstanvändare
useradd -r -s /sbin/nologin compliance

# Installera
pip install /opt/compliance-engine/

# Konfiguration
mkdir /etc/compliance-engine
cp compliance-engine/config.yaml /etc/compliance-engine/
# Fyll i ISE-lösenord och DB-DSN:
echo "ISE_PASSWORD=hemligt" > /etc/compliance-engine/secrets.env
chmod 600 /etc/compliance-engine/secrets.env

# Systemd
cp compliance-engine/systemd/compliance-engine.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now compliance-engine.timer
```

### 6. Grafana

1. Lägg till en PostgreSQL-datakälla i Grafana med anslutningsuppgifterna från steg 3
2. Importera `grafana/dashboard.json` (Dashboards → Import)
3. Välj din PostgreSQL-datakälla när du importerar

---

## Testläge

Sätt `dry_run: true` i `config.yaml` för att köra compliance-motorn utan att faktiskt karantänera. Alla beslut loggas med prefixet `[DRY-RUN]`.

OpenSCAP kan testas manuellt:

```bash
oscap xccdf eval \
  --profile xccdf_org.ssgproject.content_profile_cis_workstation_l1 \
  --results /tmp/oscap-result.xml \
  /usr/share/xml/scap/ssg/content/ssg-fedora-ds.xml

python3 compliance-playbooks/roles/openscap/files/parse_oscap.py /tmp/oscap-result.xml
```
