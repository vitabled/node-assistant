# VPS-WARP Integration Guide

This document explains how vps-warp integrates with Node Assistant and Xray.

---

## Routing Architecture

### Before: Single Route Table
```
SSH traffic:        SSH_IP → inet.0 (native table, priority -1)
WARP traffic:       Any IP → warp (table local, via wg-quick default route)
                    ❌ SSH traffic hijacked if pings go through tunnel
                    ✓ Fixed by Table=off patch, but fragile
```

### After: Marked Routes via iptables + ip rule
```
SSH traffic:        SSH_IP → inet.0 (native route, unmarked)
                    ✓ Never marked by SSH itself
                    ✓ Always uses host's native route
                    ✓ Safe even if tunnel is down

Xray traffic:       Configured apps → marked with mark=255
                    → ip rule: if mark==255 → table 51820
                    → table 51820: default route via warp0
                    → warp0 (WireGuard interface) → Cloudflare

Non-Xray traffic:   Everything else → native routing (unmarked)
                    → No redirection through tunnel
```

### Key Files (vps-warp creates)
```
/opt/vps-warp/warp_install.sh       – Main installer (updater)
/etc/vps-warp/                      – Persistent state
  ├── wgcf/                         – WGCF working directory
  │   ├── wgcf-account.toml         – WARP credentials (private)
  │   └── wgcf-profile.conf         – Generated WireGuard config
  ├── version                       – Installed version (for `vps-warp update`)
  ├── license                       – WARP+ key if configured (0600)
  └── watchdog.state                – Backoff counter (auto-rotate state)

/usr/local/bin/vps-warp             – CLI utility
/etc/systemd/system/warp.service    – systemd service unit
/etc/sysctl.d/99-vps-warp.conf      – rp_filter downgrade (if needed)
/etc/wireguard/warp.conf            – Active WireGuard config
```

---

## Xray Integration

### Mark Configuration (in Xray config.json)

Node Assistant's Xray setup **must** include:
```json
{
  "outbounds": [
    {
      "protocol": "freedom",
      "streamSettings": {
        "sockopt": {
          "mark": 255
        }
      }
    }
  ]
}
```

This marks outgoing traffic with `mark=255`, which routes via table 51820 → warp0.

### Verification (on deployed node)

```bash
# Check IP rule exists
ip rule show
# Should list: 255: from all fwmark 0xff lookup 51820

# Check routing table
ip route show table 51820
# Should list: default dev warp0

# Test tunnel connectivity
ping -M do -s 1200 8.8.8.8  # Should succeed with MSS clamping
curl -I https://1.1.1.1     # Through tunnel
```

---

## Watchdog Behavior

### Startup
```
1. warp.service starts → /opt/vps-warp/warp_install.sh
2. Script brings up warp0 interface
3. systemd watches the service (Type=simple)
4. Watchdog script runs every 3 minutes in background
```

### Health Checks
```
Every 3 minutes:
├─ Ping 1.1.1.1 through warp0     (Cloudflare nameserver)
├─ Ping 8.8.8.8 through warp0     (Google nameserver)
├─ Ping 9.9.9.9 through warp0     (Quad9 nameserver)
├─ Check wg show output (handshake age)
│  └─ If handshake > 3min old → likely dead
│
└─ If ANY check fails:
   ├─ Increase backoff counter
   ├─ Select random Cloudflare endpoint
   │  └─ Endpoint = 188.114.{96,97}.x : {2408, 500, 4500, 1701}
   ├─ Reload warp0 config with new endpoint
   ├─ Clear backoff if successful
   └─ If blocked for extended time → backoff up to 30 min
```

### Backoff Logic
- **First failure** → Retry immediately
- **Consecutive failures** → Wait 1, 2, 4, 8, 15, 30 min (exponential)
- **Success** → Reset to immediate

This prevents hammering Cloudflare if they've blocked the entire datacenter.

---

## Node Operator Commands

After deployment, operators can SSH to the node and run:

```bash
# Check tunnel status and routing
vps-warp

# Same as above (explicit)
vps-warp status

# Show tunnel IP, endpoint, traffic, handshake
vps-warp

# Force endpoint rotation (select random Cloudflare IP)
vps-warp rotate

# Restart tunnel (preserves current endpoint)
vps-warp restart

# Start/stop tunnel
vps-warp start
vps-warp stop

# Update vps-warp installer
vps-warp update

# Uninstall (removes systemd service, keeps /etc/vps-warp state)
vps-warp uninstall
```

---

## Troubleshooting

### Tunnel is down
```bash
# Check if service is running
systemctl status warp.service

# Check interface
ip link show warp0

# Check logs
journalctl -u warp.service -n 50 -f

# Force rotate endpoint
vps-warp rotate
```

### SSH is slow
- SSH is NOT routed through tunnel (mark=255 only affects Xray)
- Check native routing: `ip route show` (not table 51820)
- If slow, it's a host/network issue, not WARP

### Xray traffic not going through tunnel
1. Check Xray config has `"mark": 255` in outbound sockopt
2. Verify iptables rule: `ip rule show | grep 255`
3. Verify table exists: `ip route show table 51820`
4. Restart Xray: `docker restart remnanode`

### Tunnel lost Cloudflare connectivity
- Watchdog will auto-rotate within 3 minutes
- Check: `vps-warp` to see endpoint and backoff state
- Manual rotate: `vps-warp rotate`
- If all endpoints blocked: contact hosting provider or wait for lift

---

## Security Notes

### Credentials Storage
- wgcf-account.toml is stored in `/etc/vps-warp/wgcf/` with mode 0600
- Readable only by root
- Contains Cloudflare WARP Account ID (pre-shared secret, not a private key)
- On compromised node, Account ID is leaked but not IP-binding private key

### License Key (WARP+)
- If WARP+ key is supplied during install, it's saved to `/etc/vps-warp/license`
- Mode 0600, root-only readable
- **Recommendation**: Disable persistent license (PERSIST_LICENSE=0 in installer)
  - On every update, re-enter the key if using WARP+
  - Reduces attack surface if node is compromised

### No Firewall Rules
- vps-warp does NOT add iptables rules for the tunnel itself
- WireGuard operates at kernel level (netfilter isn't involved)
- Only concern: if SELinux is enabled (uncommon on Ubuntu), may need policy

---

## Migration from Old wgcf Setup

### Existing nodes (deployed with wgcf)
If a node has old wgcf setup and you want to upgrade:

```bash
# On the node
systemctl stop wg-quick@warp

# Run new installer
bash <(curl -fsSL https://raw.githubusercontent.com/vitabled/mirror-vps-warp/main/warp_install.sh)

# Confirm: warp0 should be up
ip link show warp0
```

The installer:
1. Detects existing wgcf-account.toml
2. Reuses it (no need to re-register)
3. Sets up systemd watchdog on top
4. Leaves old wg-quick@warp.service untouched

### No breaking changes
- Old `/etc/wireguard/wgcf-account.toml` is reused
- No re-authentication needed
- Credentials are portable between wgcf and vps-warp

---

## References

- **vps-warp GitHub**: https://github.com/tagashi666/vps-warp
- **vitabled Mirror**: https://github.com/vitabled/mirror-vps-warp
- **Xray Protocol Docs**: https://xtls.github.io/
- **WireGuard Manual**: https://man.archlinux.org/man/wg-quick.8
- **Linux ip-rule**: https://man7.org/linux/man-pages/man8/ip-rule.8.html
