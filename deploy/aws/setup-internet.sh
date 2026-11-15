#!/usr/bin/env bash
# AWS main server, as root:  sudo bash deploy/aws/setup-internet.sh on | off | status
# The GPU server has no internet of its own; its only way out leads through this server. This switch decides
# whether this server passes its traffic on:
#   on   only while the GPU server downloads its images and models (setup, updates)
#   off  the normal state: nothing is passed on, the GPU server reaches the main server and nothing else
# The rules are not saved: after a restart of this server the state is "nothing passed on" (Docker's default for
# forwarded traffic is DROP). Run "off" after every setup anyway, then check from the GPU server:
#   curl -m 5 -sI https://example.com   must fail
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

GPU_NET=10.20.8.0/21                            # the three private parts 10.20.11-13.0/24 (callsight.yaml)
OUT=$(ip -4 route show default | awk '{print $5; exit}')
[ -n "$OUT" ] || { echo "no default route found" >&2; exit 1; }
iptables -N DOCKER-USER 2>/dev/null || true     # exists already when Docker runs
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER

rule() {  # rule add|del <table> <chain> <rule...>   (never twice, never an error when absent)
  local action=$1 table=$2 chain=$3; shift 3
  if [ "$action" = add ]; then
    iptables -t "$table" -C "$chain" "$@" 2>/dev/null || iptables -t "$table" -I "$chain" 1 "$@"
  else
    while iptables -t "$table" -C "$chain" "$@" 2>/dev/null; do iptables -t "$table" -D "$chain" "$@"; done
  fi
}
PASS_OUT=(-s "$GPU_NET" -o "$OUT" -j ACCEPT)
PASS_BACK=(-d "$GPU_NET" -i "$OUT" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT)
BLOCK=(-s "$GPU_NET" -o "$OUT" -j DROP)
HIDE=(-s "$GPU_NET" -o "$OUT" -j MASQUERADE)

case "${1:-status}" in
  on)
    sysctl -q -w net.ipv4.ip_forward=1
    rule del filter DOCKER-USER "${BLOCK[@]}"
    rule add filter DOCKER-USER "${PASS_OUT[@]}"
    rule add filter DOCKER-USER "${PASS_BACK[@]}"
    rule add nat POSTROUTING "${HIDE[@]}"
    echo "ON: this server passes the GPU server's downloads on. Switch it off when setup is done." ;;
  off)
    rule del filter DOCKER-USER "${PASS_OUT[@]}"
    rule del filter DOCKER-USER "${PASS_BACK[@]}"
    rule del nat POSTROUTING "${HIDE[@]}"
    rule add filter DOCKER-USER "${BLOCK[@]}"
    echo "OFF: nothing from the GPU server is passed on to the internet." ;;
  status)
    if iptables -C DOCKER-USER "${PASS_OUT[@]}" 2>/dev/null; then echo "ON"; else echo "OFF"; fi ;;
  *) echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac
