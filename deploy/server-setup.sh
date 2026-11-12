#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04 / 24.04 server (ARM or x86):
# Docker, open web ports, and a daily database backup.
# Run on the server, from the project folder:  deploy/server-setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null; then
  echo "Installing Docker..."
  sudo apt-get update -q
  sudo apt-get install -yq ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -q
  sudo apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

# Oracle's Ubuntu images allow only SSH in the server firewall: open HTTP and HTTPS
# (inserted before the final REJECT rule, then saved so it survives reboots).
reject=$(sudo iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')
if [ -n "$reject" ]; then
  for port in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || sudo iptables -I INPUT "$reject" -p tcp --dport "$port" -j ACCEPT
  done
  sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT "$reject" -p udp --dport 443 -j ACCEPT
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq iptables-persistent
  sudo netfilter-persistent save
fi

# Daily database backup at 03:15 server time (keeps 7 days, see deploy/backup.sh).
chmod +x deploy/*.sh
( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' || true
  echo "15 3 * * * $PWD/deploy/backup.sh >> $HOME/copilot-backup.log 2>&1" ) | crontab -

echo "Done. Log out and back in once (so Docker works without sudo), then run deploy/configure.sh."
