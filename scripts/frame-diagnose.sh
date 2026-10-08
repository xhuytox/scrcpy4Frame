#!/usr/bin/env bash
# Print a diagnostic report for a Steam Frame, collected over SSH. Read-only. See -h.
set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") [<ssh-host>]

Connects to the Frame (Developer Mode with SSH) and reports on:
  - architecture, glibc, OS release, systemd version (the adb uaccess rule ships with systemd >= 258)
    and adb/Android udev rules
  - USB devices and USB-C data role
  - whether adb/scrcpy exist on the host (expected: no)
  - flatpak version and Flathub remote
  - avahi-daemon state and its disallow-other-stacks setting (python-zeroconf needs port 5353)
  - firewall rules (nft, if passwordless sudo is available)
  - desktop session (XDG_CURRENT_DESKTOP of the running session)
  - OpenVR registry (openvrpaths.vrpath), the SteamVR install and bin/linuxarm64/vrclient.so
  - IPv4 addresses and NetworkManager devices
  - TCP listeners on 5555-5599 (Lepton's adbd) and adb server ports 5037/5038
  - free disk space for builds

Arguments:
  <ssh-host>   ssh destination, e.g. deck@192.168.1.42 (default: \$FRAME_HOST)

Save the report with: $(basename "$0") <host> | tee frame-report.txt
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
(($# <= 1)) || { usage >&2; exit 1; }
HOST="${1:-${FRAME_HOST:-}}"
[[ -n "$HOST" ]] || { usage >&2; echo >&2; echo "frame-diagnose: error: no ssh host given" >&2; exit 1; }

printf 'scrcpy4Frame device report for %s (collected %s)\n' "$HOST" "$(date '+%Y-%m-%d %H:%M:%S %Z')"

# No set -e on the remote side, so a missing tool can't cut the report short.
ssh -o ConnectTimeout=15 "$HOST" 'LC_ALL=C bash -s' <<'REMOTE'
set -u
section() { printf '\n=== %s ===\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
indent() { sed 's/^/  /'; }

section "System"
echo "arch:     $(uname -m)"
echo "kernel:   $(uname -r)"
echo "user:     $(id -un) (uid $(id -u))"
echo "hostname: $(cat /etc/hostname 2>/dev/null || hostname 2>/dev/null)"
echo "glibc:    $(getconf GNU_LIBC_VERSION 2>/dev/null || echo unknown)  (host-run vr-toast needs >= the build SDK's symbol versions, about 2.38+)"
echo "/etc/os-release:"
if [ -r /etc/os-release ]; then indent </etc/os-release; else echo "  (missing)"; fi

section "systemd and udev"
sd_line="$(systemctl --version 2>/dev/null | head -1)"
echo "${sd_line:-systemctl not available}"
sd_ver="$(printf '%s\n' "$sd_line" | awk '{print $2}' | grep -oE '^[0-9]+' || true)"
if [ -n "$sd_ver" ] && [ "$sd_ver" -ge 258 ]; then
  echo "-> systemd $sd_ver >= 258: upstream uaccess rule for Android debug (adb) interfaces is present"
elif [ -n "$sd_ver" ]; then
  echo "-> systemd $sd_ver < 258: no upstream adb uaccess rule; USB adb may need a udev rule"
fi
echo "adb/android udev rules:"
rules="$( { ls /usr/lib/udev/rules.d 2>/dev/null; ls /etc/udev/rules.d 2>/dev/null; } | grep -i -E 'android|adb' || true)"
if [ -n "$rules" ]; then printf '%s\n' "$rules" | indent; else echo "  none"; fi

section "USB"
if have lsusb; then lsusb 2>&1 | indent; else echo "  lsusb not available"; fi
found_typec=0
for port in /sys/class/typec/port*; do
  [ -d "$port" ] || continue
  found_typec=1
  printf '  %s: data_role=[%s] power_role=[%s]\n' "$(basename "$port")" \
    "$(cat "$port/data_role" 2>/dev/null || echo '?')" "$(cat "$port/power_role" 2>/dev/null || echo '?')"
done
[ "$found_typec" = 1 ] || echo "  no /sys/class/typec ports"

section "Host tools (expected: none, the app bundles its own)"
for t in adb scrcpy fastboot; do
  printf '  %-9s %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo 'not found')"
done

section "Flatpak"
if have flatpak; then
  echo "  $(flatpak --version)"
  echo "  remotes:"
  flatpak remotes --show-details 2>&1 | indent | indent
  if flatpak remotes 2>/dev/null | awk '{print $1}' | grep -qx flathub; then
    echo "  -> flathub remote present"
  else
    echo "  -> flathub remote MISSING (frame-build.sh adds it with --user)"
  fi
  echo "  relevant installed refs:"
  refs="$(flatpak list --columns=ref,installation 2>/dev/null |
          grep -E 'org\.kde\.(Platform|Sdk)|io\.qt\.PySide\.BaseApp|org\.flatpak\.Builder|scrcpy4frame' || true)"
  if [ -n "$refs" ]; then printf '%s\n' "$refs" | indent | indent; else echo "    none"; fi
else
  echo "  flatpak not installed"
fi

section "Avahi (mDNS)"
if have systemctl; then
  echo "  avahi-daemon: active=$(systemctl is-active avahi-daemon 2>/dev/null) enabled=$(systemctl is-enabled avahi-daemon 2>/dev/null)"
fi
conf=/etc/avahi/avahi-daemon.conf
if [ -r "$conf" ]; then
  line="$(grep -E '^[[:space:]]*#?[[:space:]]*disallow-other-stacks' "$conf" || true)"
  echo "  $conf: ${line:-disallow-other-stacks not mentioned (default: no)}"
  if printf '%s\n' "$line" | grep -qE '^[[:space:]]*disallow-other-stacks[[:space:]]*=[[:space:]]*yes'; then
    echo "  -> other mDNS stacks are DISALLOWED: python-zeroconf cannot share port 5353"
  else
    echo "  -> other mDNS stacks allowed (python-zeroconf can share port 5353)"
  fi
else
  echo "  $conf not readable"
fi

section "Firewall"
if have systemctl; then
  for u in firewalld nftables iptables ufw; do
    st="$(systemctl is-active "$u" 2>/dev/null)"
    [ "$st" = inactive ] || [ -z "$st" ] || echo "  $u: $st"
  done
fi
if sudo -n true 2>/dev/null; then
  if sudo -n nft list ruleset >/tmp/s4f-nft.$$ 2>&1; then
    n="$(wc -l </tmp/s4f-nft.$$)"
    if [ "$n" -eq 0 ]; then echo "  nft ruleset: empty (no filtering)"; else
      echo "  nft ruleset ($n lines):"; head -150 /tmp/s4f-nft.$$ | indent | indent
      [ "$n" -le 150 ] || echo "    ... (truncated)"
    fi
  else
    echo "  nft failed:"; indent </tmp/s4f-nft.$$
  fi
  rm -f /tmp/s4f-nft.$$
else
  echo "  passwordless sudo not available. On the Frame run:  sudo nft list ruleset"
fi

section "Desktop session"
if have loginctl; then
  loginctl list-sessions --no-legend 2>/dev/null | while read -r sid _; do
    [ -n "$sid" ] || continue
    printf '  session %s: %s\n' "$sid" \
      "$(loginctl show-session "$sid" -p Type -p Class -p Desktop -p State -p Remote 2>/dev/null | tr '\n' ' ')"
  done
fi
found_env=0
for p in plasmashell kwin_wayland gamescope steam; do
  pid="$(pgrep -u "$(id -u)" -x "$p" 2>/dev/null | head -1)"
  [ -n "$pid" ] || continue
  vars="$(tr '\0' '\n' </proc/"$pid"/environ 2>/dev/null |
          grep -E '^(XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|XDG_SESSION_DESKTOP|WAYLAND_DISPLAY|DISPLAY)=' | tr '\n' ' ')"
  echo "  $p (pid $pid): ${vars:-environment not readable}"
  found_env=1
done
[ "$found_env" = 1 ] || echo "  no plasmashell/kwin_wayland/gamescope/steam process found for this user"

section "OpenVR / SteamVR"
vrpath="$HOME/.config/openvr/openvrpaths.vrpath"
runtimes=""
if [ -f "$vrpath" ]; then
  echo "  $vrpath:"
  indent <"$vrpath" | indent
  if have python3; then
    runtimes="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1])).get("runtime", [])))' "$vrpath" 2>/dev/null)"
  else
    runtimes="$(tr -d '\n' <"$vrpath" | sed -n 's/.*"runtime"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' | tr ',' '\n' | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')"
  fi
else
  echo "  $vrpath: missing (SteamVR never ran for this user?)"
fi
for d in "$HOME/.local/share/Steam/steamapps/common/SteamVR" "$HOME/.steam/steam/steamapps/common/SteamVR" \
         "$HOME/.steam/debian-installation/steamapps/common/SteamVR"; do
  [ -d "$d" ] && runtimes="$(printf '%s\n%s' "$runtimes" "$d")"
done
printf '%s\n' "$runtimes" | sed '/^$/d' | sort -u | while read -r rt; do
  echo "  runtime: $rt"
  for sub in linuxarm64 linux64; do
    if [ -f "$rt/bin/$sub/vrclient.so" ]; then echo "    bin/$sub/vrclient.so: present"; else echo "    bin/$sub/vrclient.so: missing"; fi
  done
done
running="$(pgrep -a -f 'vrserver|vrcompositor|vrmonitor|vrstartup' 2>/dev/null | grep -v pgrep || true)"
if [ -n "$running" ]; then echo "  SteamVR processes:"; printf '%s\n' "$running" | cut -c1-160 | indent | indent; else echo "  SteamVR is not running"; fi

section "Network"
if have ip; then
  addrs="$(ip -4 -brief addr 2>/dev/null)"
  [ -n "$addrs" ] || addrs="$(ip -4 addr 2>&1)"
  printf '%s\n' "$addrs" | indent
else
  echo "  ip not available"
fi
if have nmcli; then
  echo "  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>&1 | indent | indent
else
  echo "  nmcli not available"
fi

section "Listening TCP ports"
if have ss; then
  l5555="$(ss -ltnpH '( sport >= :5555 and sport <= :5599 )' 2>/dev/null)"
  if [ -n "$l5555" ]; then
    echo "  5555-5599 (an adbd here is most likely Lepton's Android container):"
    printf '%s\n' "$l5555" | indent | indent
  else
    echo "  5555-5599: nothing listening"
  fi
  ladb="$(ss -ltnpH '( sport = :5037 or sport = :5038 )' 2>/dev/null)"
  if [ -n "$ladb" ]; then echo "  adb server ports 5037/5038:"; printf '%s\n' "$ladb" | indent | indent
  else echo "  adb server ports 5037/5038: free"; fi
else
  echo "  ss not available"
fi
if have systemctl; then
  units="$(systemctl list-units --all --no-legend 2>/dev/null | grep -i -E 'lepton|waydroid' | awk '{print $1, $3, $4}')"
  [ -z "$units" ] || { echo "  Lepton/Waydroid units:"; printf '%s\n' "$units" | indent | indent; }
fi

section "Disk"
df -h "$HOME" 2>/dev/null | indent
echo "  (a Flatpak build on the Frame needs roughly 8 GiB free)"
REMOTE
