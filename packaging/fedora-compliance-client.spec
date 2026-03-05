Name:           fedora-compliance-client
Version:        1.0.0
Release:        1%{?dist}
Summary:        Automatisk compliance-kontroll och härdsäkring för Fedora-klienter

License:        MIT
URL:            https://github.com/alun-hub/compliance-playbooks
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

# Kör-beroenden
Requires:       ansible-core >= 2.14
Requires:       git
Requires:       python3
Requires:       openscap-scanner >= 1.3
Requires:       scap-security-guide
Requires:       awscli2

# Systemd-integration
%{?systemd_requires}
BuildRequires:  systemd-rpm-macros

%description
Fedora compliance-klient som via ansible-pull regelbundet hämtar och kör
compliance-kontroller mot en central git-server. Kontrollerna inkluderar:

  - OpenSCAP CIS-benchmark scanning (en gång per dygn)
  - Verifiering av säkerhetstjänster (auditd, SELinux, rsyslog, firewalld)
  - Rapportering till central S3-bucket

Klienten konfigureras i /etc/fedora-compliance/client.conf.


%prep
%autosetup


%build
# Inget att bygga — paketet innehåller enbart konfiguration och systemd-enheter.


%install
# Systemd-enheter
install -D -m 0644 systemd/ansible-pull.service \
    %{buildroot}%{_unitdir}/ansible-pull.service
install -D -m 0644 systemd/ansible-pull.timer \
    %{buildroot}%{_unitdir}/ansible-pull.timer

# Konfigurationsfiler
install -D -m 0600 packaging/client.conf \
    %{buildroot}%{_sysconfdir}/fedora-compliance/client.conf
install -D -m 0600 packaging/vars.yml \
    %{buildroot}%{_sysconfdir}/fedora-compliance/vars.yml

# Katalog för ansible-pull checkout
install -d -m 0700 %{buildroot}%{_sysconfdir}/ansible/pull


%post
%systemd_post ansible-pull.timer

# Aktivera timern automatiskt vid installation
# (kräver att client.conf är korrekt konfigurerad)
if [ $1 -eq 1 ]; then
    systemctl enable ansible-pull.timer 2>/dev/null || true
fi

%preun
%systemd_preun ansible-pull.timer

%postun
%systemd_postun_with_restart ansible-pull.timer


%files
%{_unitdir}/ansible-pull.service
%{_unitdir}/ansible-pull.timer

# %config(noreplace) bevarar lokal konfiguration vid uppgradering
%config(noreplace) %attr(0600, root, root) %{_sysconfdir}/fedora-compliance/client.conf
%config(noreplace) %attr(0600, root, root) %{_sysconfdir}/fedora-compliance/vars.yml

%dir %attr(0700, root, root) %{_sysconfdir}/ansible/pull


%changelog
* Thu Mar 05 2026 Alun <alun@example.com> - 1.0.0-1
- Initial release
