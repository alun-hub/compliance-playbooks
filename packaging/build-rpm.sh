#!/usr/bin/env bash
# Bygger fedora-compliance-client RPM från repots rot.
# Användning: ./packaging/build-rpm.sh [version]
set -euo pipefail

VERSION="${1:-1.0.0}"
NAME="fedora-compliance-client"
TARBALL="${NAME}-${VERSION}.tar.gz"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "=== Bygger ${NAME}-${VERSION} ==="

# --- Sätt upp rpmbuild-trädet ---
rpmdev-setuptree 2>/dev/null || mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# --- Skapa källkods-tarball ---
echo "Skapar tarball..."
STAGE="${BUILD_DIR}/${NAME}-${VERSION}"
mkdir -p "${STAGE}/systemd" "${STAGE}/packaging"

cp "${REPO_ROOT}/systemd/ansible-pull.service" "${STAGE}/systemd/"
cp "${REPO_ROOT}/systemd/ansible-pull.timer"   "${STAGE}/systemd/"
cp "${REPO_ROOT}/packaging/client.conf"        "${STAGE}/packaging/"
cp "${REPO_ROOT}/packaging/vars.yml"           "${STAGE}/packaging/"

tar -czf ~/rpmbuild/SOURCES/${TARBALL} -C "${BUILD_DIR}" "${NAME}-${VERSION}"
echo "  → ~/rpmbuild/SOURCES/${TARBALL}"

# --- Uppdatera version i SPEC ---
sed "s/^Version:.*/Version:        ${VERSION}/" \
    "${REPO_ROOT}/packaging/${NAME}.spec" \
    > ~/rpmbuild/SPECS/${NAME}.spec
echo "  → ~/rpmbuild/SPECS/${NAME}.spec"

# --- Bygg RPM ---
echo "Bygger RPM..."
rpmbuild -bb ~/rpmbuild/SPECS/${NAME}.spec

# --- Hitta och visa resultatet ---
RPM_FILE=$(find ~/rpmbuild/RPMS -name "${NAME}-${VERSION}*.rpm" | head -1)
echo ""
echo "=== Klar ==="
echo "RPM: ${RPM_FILE}"
echo ""
echo "Installera:"
echo "  sudo dnf install ${RPM_FILE}"
echo ""
echo "Konfigurera efter installation:"
echo "  sudo vi /etc/fedora-compliance/client.conf  # Sätt git-URL"
echo "  sudo vi /etc/fedora-compliance/vars.yml      # Sätt S3-bucket"
echo "  sudo systemctl start ansible-pull.service    # Trigga första körning"
