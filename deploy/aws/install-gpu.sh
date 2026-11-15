#!/usr/bin/env bash
# AWS GPU server, once, as root:  sudo bash /opt/callsight/deploy/aws/install-gpu.sh
# Needs the download switch ON on the main server (sudo bash deploy/aws/setup-internet.sh on).
# Checks Docker + the NVIDIA runtime of the AWS GPU image, installs the NFS client, and installs the boot service
# with the "stay on" file in place, so the server does NOT power itself off during setup.
# Safe to run again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

docker compose version >/dev/null || { echo "docker compose is missing on this image" >&2; exit 1; }
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "no NVIDIA driver on this image" >&2; exit 1; }
docker info 2>/dev/null | grep -qi nvidia || { echo "Docker has no NVIDIA runtime on this image" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q nfs-common
usermod -aG docker ubuntu

install -d /etc/callsight
touch /etc/callsight/stay-on                    # removed at the end of setup (docs/aws.md)
install -m 644 "$(dirname "$0")/callsight-gpu.service" /etc/systemd/system/callsight-gpu.service
systemctl daemon-reload
systemctl enable callsight-gpu
echo "GPU server ready for: .env (deploy/aws/env.gpu.example), image build, model downloads."
echo "The server stays on until /etc/callsight/stay-on is removed."
