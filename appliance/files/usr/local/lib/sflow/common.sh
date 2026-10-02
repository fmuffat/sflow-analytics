# Shared helpers of the sFlow Analytics appliance console tools (sourced, bash).

SFLOW_DIR=/opt/sflow-analytics
SFLOW_PKG=/opt/sflow-package
SFLOW_STATE=/etc/sflow
TITLE="sFlow Analytics appliance"
LOG=/var/log/sflow-setup.log

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

# whiptail helpers (answers on stdout; non-zero exit when cancelled)
ask()    { whiptail --title "$TITLE" --inputbox "$1" 10 72 "${2:-}" 3>&1 1>&2 2>&3; }
askpw()  { whiptail --title "$TITLE" --passwordbox "$1" 10 72 3>&1 1>&2 2>&3; }
info()   { whiptail --title "$TITLE" --msgbox "$1" "${2:-12}" 76; }
yesno()  { whiptail --title "$TITLE" --yesno "$1" 10 72; }

primary_nic() {
  ls /sys/class/net | grep -E '^(en|eth)' | head -1
}

current_ip() {
  ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1
}

valid_cidr() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]]; }
valid_ip()   { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }

# Network: DHCP or static, written to /etc/netplan/50-sflow.yaml.
configure_network() {
  local nic mode addr gw dns
  nic="$(primary_nic)"
  mode=$(whiptail --title "$TITLE" --menu "Network configuration of $nic" 12 72 2 \
    dhcp "Automatic (DHCP)" static "Static address" 3>&1 1>&2 2>&3) || return 1
  if [ "$mode" = static ]; then
    while :; do addr=$(ask "IP address with prefix length, e.g. 192.168.10.50/24") || return 1; valid_cidr "$addr" && break; info "Invalid address: $addr" 8; done
    while :; do gw=$(ask "Default gateway, e.g. 192.168.10.1") || return 1; valid_ip "$gw" && break; info "Invalid gateway: $gw" 8; done
    dns=$(ask "DNS servers (comma separated)" "$gw") || return 1
    cat >/etc/netplan/50-sflow.yaml <<EOF
network:
  version: 2
  ethernets:
    $nic:
      dhcp4: false
      addresses: [$addr]
      routes: [{to: default, via: $gw}]
      nameservers: {addresses: [$(echo "$dns" | tr -d ' ')]}
EOF
  else
    cat >/etc/netplan/50-sflow.yaml <<EOF
network:
  version: 2
  ethernets:
    $nic:
      dhcp4: true
EOF
  fi
  chmod 600 /etc/netplan/50-sflow.yaml
  rm -f /etc/netplan/01-sflow-dhcp.yaml
  netplan apply >>"$LOG" 2>&1
  sleep 3
  log "network: $mode ${addr:-} gw=${gw:-} dns=${dns:-} -> $(current_ip)"
}

# Certificate names: the nginx container regenerates its self-signed certificate when the
# volume is empty; after an IP change, recreate it so that the new address is included.
refresh_certificate() {
  [ -f "$SFLOW_DIR/.env" ] || return 0
  sed -i "s/^TLS_EXTRA_SAN=.*/TLS_EXTRA_SAN=IP:$(current_ip)/" "$SFLOW_DIR/.env"
  (cd "$SFLOW_DIR" && docker compose rm -sf nginx && docker volume rm "$(grep '^SFLOW_PROJECT=' .env | cut -d= -f2)_nginx-certs" && docker compose up -d) >>"$LOG" 2>&1 || true
}
