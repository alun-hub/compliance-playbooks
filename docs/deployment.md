# Driftsättning i produktion

## Förkrav

| Komponent | Krav |
|---|---|
| Fedora-klienter | Fedora 39+ med nätverksåtkomst till git-repo och S3 |
| Git-server | HTTPS-åtkomst från alla klienter (GitHub, Gitea, GitLab) |
| S3-kompatibel lagring | AWS S3 eller on-prem (MinIO, Ceph) |
| Cisco ISE | Version 3.0+, ERS API aktiverat |
| Cisco-switchar | RADIUS CoA-stöd (de flesta Catalyst-modeller) |
| Compliance-server | En Linux-server (VM räcker), Python 3.11+ |
| PostgreSQL | 14+, kan köra på samma server som compliance-motorn |
| Grafana | 9+, kan köra på samma server |

---

## Steg 1 — Cisco ISE

### 1.1 Aktivera ERS API

Administration → System → Settings → API Settings → ERS Settings
- Sätt **ERS (Read/Write)** till **Enable**

### 1.2 Skapa tjänstkonto för compliance-motorn

Administration → Identity Management → Identities → Users → Add

| Fält | Värde |
|---|---|
| Name | compliance-svc |
| Password | (starkt lösenord) |
| User Group | ERS Admin |

ERS Admin-gruppen har rätt att läsa sessioner och applicera ANC-policys.

### 1.3 Skapa ANC-policy

Operations → Adaptive Network Control → Policy List → Add

| Fält | Värde |
|---|---|
| Name | **Quarantine** (måste matcha `quarantine_policy` i config.yaml) |
| Actions | QUARANTINE |

### 1.4 Konfigurera RADIUS CoA på switcharna

Varje switch som hanterar klienter måste tillåta ISE att skicka CoA.
Exempelkonfiguration (Cisco Catalyst IOS-XE):

```
aaa server radius dynamic-author
 client <ISE-IP> server-key <hemlig-nyckel>
 port 3799
 auth-type any
```

ISE sköter resten automatiskt när ANC-policyn appliceras.

---

## Steg 2 — S3-bucket

### 2.1 Skapa bucket

```bash
aws s3api create-bucket \
  --bucket <bucket-namn> \
  --region eu-north-1 \
  --create-bucket-configuration LocationConstraint=eu-north-1
```

### 2.2 Aktivera versionshantering (rekommenderat)

```bash
aws s3api put-bucket-versioning \
  --bucket <bucket-namn> \
  --versioning-configuration Status=Enabled
```

### 2.3 Sätt bucket-policy

Ersätt `BUCKET_NAME` i `iac/s3-bucket-policy.json` och applicera:

```bash
sed 's/BUCKET_NAME/<bucket-namn>/g' iac/s3-bucket-policy.json > /tmp/bp.json
aws s3api put-bucket-policy --bucket <bucket-namn> --policy file:///tmp/bp.json
```

Bucket-policyn kräver KMS-kryptering och TLS på alla anrop.

### 2.4 Skapa KMS-nyckel

```bash
aws kms create-key --description "Compliance reports"
# Notera KeyId i svaret
```

### 2.5 Skapa IAM-policy och roll för klienter

Ersätt `BUCKET_NAME` i `iac/iam-client-policy.json`:

```bash
sed 's/BUCKET_NAME/<bucket-namn>/g' iac/iam-client-policy.json > /tmp/cp.json
aws iam create-policy \
  --policy-name FedoraComplianceClientWrite \
  --policy-document file:///tmp/cp.json
```

Skapa en IAM-roll för EC2-instanser (om klienterna är i AWS) eller
använd IAM Identity Center för on-prem-klienter med `aws configure sso`.

Tagga varje klient-roll med `hostname`-taggen som matchar maskinens hostname
— det begränsar varje klient till att bara skriva sin egna rapport.

### 2.6 Skapa IAM-policy för compliance-motorn

```bash
sed 's/BUCKET_NAME/<bucket-namn>/g' iac/iam-engine-policy.json > /tmp/ep.json
aws iam create-policy \
  --policy-name FedoraComplianceEngineRead \
  --policy-document file:///tmp/ep.json
```

---

## Steg 3 — Git-repo

### 3.1 Forka/klona repot till er interna git-server

```bash
git clone https://github.com/alun-hub/compliance-playbooks.git
cd compliance-playbooks
git remote set-url origin https://git.intern.example.com/infra/compliance-playbooks.git
git push -u origin main
```

### 3.2 Anpassa konfigurationen

Redigera `group_vars/all.yml`:

```yaml
s3_bucket: ert-compliance-bucket
oscap_profile: xccdf_org.ssgproject.content_profile_cis_workstation_l1
oscap_max_age_minutes: 1440
```

Redigera `systemd/ansible-pull.service` — uppdatera git-URL:

```ini
ExecStart=/usr/bin/ansible-pull \
  --url https://git.intern.example.com/infra/compliance-playbooks.git \
  ...
```

Om repot kräver autentisering, konfigurera git credentials på klienterna
via `git credential store` eller SSH-nyckel i `/root/.ssh/`.

### 3.3 Lägg till tailoring-fil (valfritt men rekommenderat)

Se `docs/compliance-tuning.md` för hur man skapar och aktiverar en
tailoring-fil som undantar regler som inte är relevanta i er miljö.

Commit och pusha alla ändringar:

```bash
git add -A && git commit -m "Anpassa till produktion" && git push
```

---

## Steg 4 — Bootstrap av klienter

### 4.1 Förbered inventory

Skapa en inventory-fil med alla klienter som ska bootstrappas:

```ini
# inventory/clients
[fedora_clients]
laptop-001.intern.example.com
laptop-002.intern.example.com
workstation-010.intern.example.com
```

### 4.2 Konfigurera AWS-credentials på klienterna

Klienterna behöver kunna anropa S3. Alternativen:

**Alternativ A — IAM Instance Profile (AWS)**
Koppla IAM-rollen direkt till EC2-instansen. Kräver ingen konfiguration på klienten.

**Alternativ B — aws configure på varje klient**
```bash
# Körs som en task i bootstrap.yml eller manuellt
aws configure set aws_access_key_id <nyckel>
aws configure set aws_secret_access_key <hemlighet>
aws configure set region eu-north-1
```

**Alternativ C — IAM Identity Center (SSO)**
Lämpligt för on-prem med federation mot AD/LDAP.

### 4.3 Kör bootstrap-playbooken

```bash
# Mot enstaka klient
ansible-playbook -i "laptop-001.intern.example.com," bootstrap.yml -u root

# Mot hela gruppen
ansible-playbook -i inventory/clients bootstrap.yml -u root

# Via AWX — skapa ett Job Template mot bootstrap.yml
# med inventory-gruppen som target
```

Bootstrap-playbooken:
1. Installerar ansible och awscli på klienten
2. Installerar `ansible-pull.service` och `ansible-pull.timer`
3. Aktiverar och startar timern
4. Triggar en första körning direkt
5. Väntar på att `/tmp/compliance-report.json` skapas (max 15 min)

### 4.4 Verifiera att klienter rapporterar

```bash
# Kontrollera S3 att rapporter har börjat komma in
aws s3 ls s3://<bucket-namn>/compliance/ --recursive | sort -k1,2

# Kontrollera att timern är aktiv på en klient
ssh root@laptop-001 systemctl status ansible-pull.timer

# Kontrollera journald-loggen på klienten
ssh root@laptop-001 journalctl -u ansible-pull.service -n 50
```

---

## Steg 5 — Compliance-motor

### 5.1 Förbered servern

```bash
# Skapa dedikerad användare
useradd -r -m -s /sbin/nologin compliance

# Installera Python
dnf install -y python3 python3-pip git

# Klona repot
git clone https://git.intern.example.com/infra/compliance-playbooks.git /opt/compliance-playbooks
```

### 5.2 Installera compliance-motorn

```bash
pip3 install /opt/compliance-playbooks/compliance-engine
```

### 5.3 Konfigurera

```bash
mkdir -p /etc/compliance-engine

cat > /etc/compliance-engine/config.yaml << 'EOF'
ise:
  host: ise.intern.example.com
  username: compliance-svc
  password: "${ISE_PASSWORD}"
  verify_ssl: true

s3:
  bucket: ert-compliance-bucket

evaluation:
  max_report_age_minutes: 90
  grace_period_minutes: 10
  quarantine_policy: Quarantine

database:
  dsn: "postgresql://compliance:<lösenord>@localhost/compliance"

dry_run: false
EOF

chmod 600 /etc/compliance-engine/config.yaml

# Lägg ISE-lösenordet i en separat secrets-fil
cat > /etc/compliance-engine/secrets.env << 'EOF'
ISE_PASSWORD=<lösenord>
EOF
chmod 600 /etc/compliance-engine/secrets.env
chown compliance:compliance /etc/compliance-engine/secrets.env
```

### 5.4 Installera systemd-enheter

```bash
cp /opt/compliance-playbooks/compliance-engine/systemd/compliance-engine.service \
   /etc/systemd/system/
cp /opt/compliance-playbooks/compliance-engine/systemd/compliance-engine.timer \
   /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now compliance-engine.timer
```

### 5.5 Verifiera

```bash
# Trigga en manuell körning
systemctl start compliance-engine.service

# Kontrollera utdata
journalctl -u compliance-engine.service -n 100

# Kontrollera att data hamnade i databasen
sudo -u postgres psql compliance \
  -c "SELECT action, count(*) FROM compliance_status GROUP BY action;"
```

---

## Steg 6 — PostgreSQL

### 6.1 Installera

```bash
dnf install -y postgresql-server postgresql
postgresql-setup --initdb
systemctl enable --now postgresql
```

### 6.2 Skapa databas och användare

```bash
sudo -u postgres psql << 'EOF'
CREATE DATABASE compliance;
CREATE USER compliance WITH PASSWORD '<lösenord>';
GRANT ALL PRIVILEGES ON DATABASE compliance TO compliance;
\c compliance
GRANT ALL ON SCHEMA public TO compliance;
EOF
```

Schema skapas automatiskt vid första körning av compliance-motorn.

### 6.3 Konfigurera pg_hba.conf för lokal åtkomst

```bash
# /var/lib/pgsql/data/pg_hba.conf
# Lägg till:
local   compliance      compliance                              scram-sha-256
host    compliance      compliance      127.0.0.1/32           scram-sha-256
```

```bash
systemctl reload postgresql
```

---

## Steg 7 — Grafana

### 7.1 Installera

```bash
dnf install -y grafana
systemctl enable --now grafana-server
```

### 7.2 Provisionera datakälla automatiskt

```bash
cat > /etc/grafana/provisioning/datasources/compliance.yaml << 'EOF'
apiVersion: 1
datasources:
  - name: Compliance DB
    type: postgres
    uid: compliance-pg
    url: localhost:5432
    user: compliance
    secureJsonData:
      password: <lösenord>
    jsonData:
      database: compliance
      sslmode: disable
      postgresVersion: 1400
    isDefault: true
EOF
```

### 7.3 Provisionera dashboard automatiskt

```bash
# Skapa dashboard-katalog
mkdir -p /var/lib/grafana/dashboards

# Kopiera dashboard (med korrekt datakällans UID)
python3 - << 'EOF'
import json
with open('/opt/compliance-playbooks/grafana/dashboard.json') as f:
    d = json.load(f)
# Ersätt ${DS_POSTGRES} med faktisk UID
out = json.dumps(d).replace('"${DS_POSTGRES}"', '{"type":"postgres","uid":"compliance-pg"}')
with open('/var/lib/grafana/dashboards/compliance.json', 'w') as f:
    f.write(out)
EOF

cat > /etc/grafana/provisioning/dashboards/compliance.yaml << 'EOF'
apiVersion: 1
providers:
  - name: compliance
    type: file
    options:
      path: /var/lib/grafana/dashboards
EOF

systemctl restart grafana-server
```

Grafana är tillgängligt på port 3000. Standardlösenord admin/admin — byt vid första inloggning.

---

## Steg 8 — Brandväggsregler

Compliance-servern behöver nå:

| Destination | Port | Protokoll | Syfte |
|---|---|---|---|
| ISE | 9060 | TCP/HTTPS | ERS API |
| S3/MinIO | 443 | TCP/HTTPS | Hämta compliance-rapporter |

Klienterna behöver nå:

| Destination | Port | Protokoll | Syfte |
|---|---|---|---|
| Git-server | 443 | TCP/HTTPS | ansible-pull |
| S3/MinIO | 443 | TCP/HTTPS | Pusha rapporter |

---

## Steg 9 — Verifiera hela kedjan

```bash
# 1. Kontrollera att en klient har skickat rapport
aws s3 ls s3://<bucket>/compliance/ --recursive

# 2. Kör compliance-motorn manuellt och kontrollera utdata
systemctl start compliance-engine.service
journalctl -u compliance-engine.service --no-pager

# 3. Kontrollera PostgreSQL
sudo -u postgres psql compliance \
  -c "SELECT hostname, action, reason, checked_at FROM compliance_status ORDER BY checked_at DESC LIMIT 20;"

# 4. Öppna Grafana och verifiera att data syns i dashboarden
# http://<compliance-server>:3000

# 5. Testa karantän — stoppa auditd på en testklient
# (kräver att ISE CoA är konfigurerat mot switcharna)
ssh root@testklient systemctl stop auditd
# Vänta på nästa compliance-motor-körning (max 15 min)
# Kontrollera att klienten hamnar i karantän-VLAN
```

---

## Löpande underhåll

### Uppdatera compliance-regler

```bash
# Uppdatera SCAP Security Guide på klienterna
dnf update scap-security-guide

# Uppdatera tailoring-filen vid behov
# Commit till git-repot — klienterna hämtar vid nästa ansible-pull
```

### Uppdatera compliance-motorn

```bash
cd /opt/compliance-playbooks
git pull
pip3 install --upgrade /opt/compliance-playbooks/compliance-engine
systemctl restart compliance-engine.timer
```

### Rensa gammal data ur PostgreSQL

```bash
# Ta bort data äldre än 90 dagar
sudo -u postgres psql compliance \
  -c "DELETE FROM compliance_status WHERE checked_at < now() - interval '90 days';"
```

### Frigöra klient från karantän manuellt

Via ISE-konsolen: Operations → Adaptive Network Control → Endpoints
Sök på MAC-adress → Clear Policy.

Eller via API (kräver compliance-engine-miljön):

```python
from engine.ise import ISEClient
ise = ISEClient("ise.intern.example.com", "compliance-svc", "lösenord")
ise.release_quarantine("aa:bb:cc:dd:ee:ff")
```
