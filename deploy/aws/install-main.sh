#!/usr/bin/env bash
# AWS main server, once, as root:  sudo bash /opt/callsight/deploy/aws/install-main.sh
# Installs Docker + the NFS server, makes the data folders, and shares the recordings and config/ READ-ONLY with
# the private network (the AWS security group lets only the GPU server reach port 2049).
# Safe to run again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 nfs-kernel-server
usermod -aG docker ubuntu                      # takes effect at the next login

# uid 1000 = user "ubuntu" on the server = user "app" inside the containers
install -d -o 1000 -g 1000 /srv/callsight /srv/callsight/audio /srv/callsight/reports /srv/callsight/backup
install -d -o 1000 -g 1000 /opt/callsight /opt/callsight/config
chmod 750 /srv/callsight/backup

install -d /etc/exports.d
cat > /etc/exports.d/callsight.exports <<'EOF'
/srv/callsight/audio   10.20.0.0/16(ro,sync,no_subtree_check,root_squash)
/opt/callsight/config  10.20.0.0/16(ro,sync,no_subtree_check,root_squash)
EOF
systemctl enable --now nfs-kernel-server
exportfs -ra

bash "$(dirname "$0")/setup-internet.sh" off   # the GPU server's way out stays closed until setup needs it
docker --version
docker compose version
exportfs -v
echo "main server ready. Next: .env (deploy/aws/env.main.example), then"
echo "  docker compose -f docker-compose.yml -f deploy/aws/compose.main.yml up -d --build"
