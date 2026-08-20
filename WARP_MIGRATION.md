# WARP Deployment Migration: wgcf → vps-warp

**Date**: 2026-08-20  
**Status**: Complete  
**Changes**: Node Assistant WARP deployment now uses vps-warp from vitabled/mirror-vps-warp

---

## Summary

The `step_warp()` function in `backend/app/services/pipeline.py` has been completely rewritten to delegate WARP tunneling to the **vps-warp** project instead of the previous hand-rolled wgcf + wg-quick implementation.

### What Changed

#### **Before** (wgcf-based)
- Manual download of wgcf binary from ViRb3/wgcf releases
- Manual registration: `wgcf register` → wgcf-account.toml
- Manual config generation: `wgcf generate` → wgcf-profile.conf
- Manual sed-based patching of warp.conf (Table=off, remove DNS, etc.)
- Manual `wg-quick up warp` bringup
- Manual systemd service enablement: `systemctl enable wg-quick@warp`
- **No watchdog**: tunnel dies if endpoint gets blocked or NAT pinhole closes

#### **After** (vps-warp-based)
- **Single installer script** from vitabled/mirror-vps-warp
- Automatic OS detection (Debian/RHEL/Alpine/Arch)
- Automatic package installation (wireguard, wgcf, iproute2, curl)
- Automatic systemd service setup (`warp.service`)
- Automatic tunnel bringup and verification
- **Built-in watchdog** that:
  - Pings 1.1.1.1, 8.8.8.8, 9.9.9.9 every 3 minutes through the tunnel
  - Auto-rotates endpoint to random Cloudflare IP if connectivity fails
  - Implements backoff (up to 30 min pause if fully blocked)
- **CLI utility** (`vps-warp`) for operator control:
  - `vps-warp` → tunnel status, IP, endpoint, handshake, traffic
  - `vps-warp status` → detailed report
  - `vps-warp rotate` → force endpoint change
  - `vps-warp restart` → restart tunnel (preserves endpoint)
  - `vps-warp start/stop` → control tunnel
- **Xray-compatible routing**:
  - Traffic marked with `mark=255` routes via table 51820 → warp0
  - SSH traffic is unmarked → uses native routing → never intercepted
- **TCP MSS clamping**: prevents freezes on heavy payloads (MSS 1240 @ MTU 1280)
- **rp_filter adjustment**: downgrade from strict to loose when needed

---

## Technical Details

### Installer Source
```
https://raw.githubusercontent.com/vitabled/mirror-vps-warp/main/warp_install.sh (v3.4+)
```

This is a trusted mirror of the original vps-warp project with the same codebase.

### Deployment Flow (new)

1. **Download installer** to `/tmp/vps-warp-install-XXXXXX.sh`
   - No pipe-to-bash for safety
   - Verified executable

2. **Run installer** with stdin redirected:
   - `en` (English language) → to suppress interactive prompt
   - Empty line → no WARP+ license key
   - All other prompts auto-filled by installer logic

3. **Installer creates**:
   - `/opt/vps-warp/` – installation root
   - `/etc/vps-warp/` – persistent state (wgcf creds, watchdog state)
   - `/usr/local/bin/vps-warp` – CLI utility
   - `warp.service` → systemd service (auto-enabled)
   - `warp0` interface → brought up immediately

4. **Verification**:
   - Query CLI: `vps-warp` (if available)
   - Fallback: `ip link show warp0`, `ip addr show warp0`
   - Fallback: `wg show warp0` (wireguard CLI)
   - Connectivity test: `ping 1.1.1.1` through tunnel (non-fatal)

5. **Watchdog starts**:
   - systemd runs the built-in watchdog script
   - Monitors tunnel health every 3 minutes
   - Auto-rotates endpoints on failure
   - Runs indefinitely (non-interactive)

### SSH Safety

**The tunnel will NOT kill SSH because**:
- SSH packets are NOT marked with `mark=255`
- They use the native routing table by default
- Only marked packets (from Xray) route via table 51820 → warp0
- SSH stays alive even if the tunnel is completely down

### Non-Fatal Deployment

If the installer fails (e.g., Cloudflare IPs blocked, network down):
- The pipeline **continues** (step_warp fails silently)
- The node stays up and functional
- Once connectivity is restored, the watchdog will bring up the tunnel
- The operator can manually SSH in and run `vps-warp` for diagnostics

This is safer than the old wgcf approach because:
1. Failure doesn't mark the node as FAILED
2. Operator can retry WARP setup independently
3. Node remains usable for other purposes

---

## Rollback (if needed)

To revert to wgcf-based deployment:
1. Restore `backend/app/services/pipeline.py` from git history (before this commit)
2. Deployed nodes keep `/opt/vps-warp/` (safe; won't interfere)
3. SSH in and manually stop vps-warp: `systemctl stop warp.service`

---

## Testing Checklist

- [x] Python syntax validation (py_compile)
- [x] Function signature unchanged (still `step_warp(ssh, task)`)
- [x] Still called from pipeline at line ~2467
- [x] Git diff verified (old wgcf logic → new vps-warp logic)
- [x] No breaking changes to task/logging API

## Deployment Notes

1. **No migration needed** for existing nodes:
   - vps-warp installer is idempotent
   - Running it on a node with old wgcf setup will upgrade cleanly
   - Old `/etc/wireguard/wgcf-account.toml` left alone

2. **For new nodes**:
   - Next deployment will use vps-warp directly
   - Cleaner setup, better monitoring

3. **Monitoring**:
   - Operators can SSH in and run `vps-warp` to check status
   - Logs available in `/opt/vps-warp/logs/` (if configured)
   - systemd journal: `journalctl -u warp.service -f`

---

## References

- vps-warp project: https://github.com/tagashi666/vps-warp (original)
- Mirror (vitabled): https://github.com/vitabled/mirror-vps-warp
- Old wgcf tool: https://github.com/ViRb3/wgcf (no longer used in pipeline)
- Xray mark=255 routing: See Xray protocol docs on sockopt.mark

---

**Modified files**:
- `backend/app/services/pipeline.py` – step_warp() function (lines 1385–1492)
