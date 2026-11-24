#!/usr/bin/env bash
set -euo pipefail

BRYCE_DIR="/data/bryce3d"
CD_DIR="/data/cd"
WINE_DIR="${WINEPREFIX:-/data/wine}"
ISO_FILE="${BRYCE_ISO_PATH:-/iso/Bryce3DCD.iso}"

IS_ARM64=0
case "$(uname -m)" in
    aarch64|arm64) IS_ARM64=1 ;;
esac

log() {
    echo "[brycedock] $*"
}

# ------------------------------------------------------------------------------
# 1. Extract Bryce 3D from ISO on first run (no copyrighted files baked in image)
# ------------------------------------------------------------------------------
install_from_iso() {
    local src_iso="$ISO_FILE"
    local tmp_iso=""

    if [ ! -f "$src_iso" ]; then
        if [ -n "${BRYCE_ISO_URL:-}" ]; then
            log "ISO not found at ${src_iso}; downloading from BRYCE_ISO_URL..."
            tmp_iso="/tmp/Bryce3DCD.iso"
            curl -fSL "$BRYCE_ISO_URL" -o "$tmp_iso"
            src_iso="$tmp_iso"
        else
            log "ERROR: Bryce3DCD.iso not found at ${src_iso} and BRYCE_ISO_URL is not set."
            log "Mount Bryce3DCD.iso to /iso/Bryce3DCD.iso or set BRYCE_ISO_URL."
            exit 1
        fi
    fi

    log "Extracting InstallShield 3 archive (data.1) from ${src_iso}..."
    local tmp_work="/tmp/bryce_extract"
    rm -rf "$tmp_work"
    mkdir -p "$tmp_work/unpacked" "$BRYCE_DIR" "$CD_DIR"

    # Extract data.1 via bsdtar (handles ISO9660/Joliet cleanly without Mac HFS fork collisions)
    bsdtar -xf "$src_iso" -C "$tmp_work" data.1

    log "Unpacking data.1 with unshieldv3..."
    unshieldv3 extract "$tmp_work/data.1" "$tmp_work/unpacked" >/dev/null

    # Move prog/* into /data/bryce3d (case-insensitive search for prog folder)
    local prog_dir
    prog_dir="$(find "$tmp_work/unpacked" -maxdepth 2 -type d -iname 'prog' | head -n 1)"
    if [ -z "$prog_dir" ] || [ ! -d "$prog_dir" ]; then
        log "ERROR: Could not locate 'prog' directory inside data.1"
        exit 1
    fi

    cp -a "$prog_dir"/. "$BRYCE_DIR/"
    chmod -R u+rwX "$BRYCE_DIR"

    # Extract CD bonus libraries, sample scenes, presets, models, and user guide
    log "Extracting CD libraries and sample scenes to ${CD_DIR}..."
    bsdtar -xf "$src_iso" -C "$CD_DIR" \
        --exclude='data.1' \
        --exclude='Adobe Acrobat Reader 3*' \
        --exclude='DirectX5*' \
        --exclude='OpenGL*' \
        --exclude='QuickTime Installer.EXE' \
        --exclude='SETUP.EXE' \
        --exclude='SETUP.INS' \
        --exclude='_INST32I.EX_' \
        --exclude='_ISDEL.EXE' \
        --exclude='_SETUP.DLL' \
        --exclude='_SETUP.LIB' \
        --exclude='setup.bmp' \
        --exclude='setup.ini' \
        --exclude='setup.pkg' \
        || true
    chmod -R u+rwX "$CD_DIR" 2>/dev/null || true

    rm -rf "$tmp_work"
    if [ -n "$tmp_iso" ]; then
        rm -f "$tmp_iso"
    fi

    log "Bryce 3D extracted successfully to ${BRYCE_DIR}."
}

# ------------------------------------------------------------------------------
# 2. Initialize Wine prefix and map drives / file associations
# ------------------------------------------------------------------------------
init_wine_prefix() {
    mkdir -p "$WINE_DIR"
    if [ "$IS_ARM64" -eq 1 ]; then
        log "Initializing ARM64 Hangover Wine (WoW64 + FEX) prefix at ${WINE_DIR}..."
        WINEPREFIX="$WINE_DIR" wineboot --init
    else
        log "Initializing 32-bit Wine prefix at ${WINE_DIR}..."
        WINEARCH=win32 WINEPREFIX="$WINE_DIR" wineboot --init
    fi
    wineserver -w

    # Link Bryce 3D inside C:\Bryce3D and map D: (CD content) and E: (/workspace)
    mkdir -p "$WINE_DIR/drive_c" "$WINE_DIR/dosdevices"
    ln -sfn "$BRYCE_DIR" "$WINE_DIR/drive_c/Bryce3D"
    ln -sfn "$CD_DIR" "$WINE_DIR/dosdevices/d:"
    ln -sfn "/workspace" "$WINE_DIR/dosdevices/e:"
    ln -sfn "/workspace" "$BRYCE_DIR/Projects"
    ln -sfn "$CD_DIR" "$BRYCE_DIR/CD_Content"

    # Configure Wine version (Win98 on x86_64 win32; WinXP + GrabPointer=N on ARM64 WoW64),
    # drive types, and .br3 file association
    if [ "$IS_ARM64" -eq 1 ]; then
        cat > /tmp/bryce_setup.reg <<'EOF'
Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\Software\Wine]
"Version"="winxp"

[HKEY_CURRENT_USER\Software\Wine\X11 Driver]
"GrabPointer"="N"

[HKEY_LOCAL_MACHINE\Software\Wine\Drives]
"d:"="cdrom"
"e:"="hd"

[HKEY_CLASSES_ROOT\.br3]
@="Bryce3Dfile"

[HKEY_CLASSES_ROOT\Bryce3Dfile]
@="Bryce3D File"

[HKEY_CLASSES_ROOT\Bryce3Dfile\DefaultIcon]
@="C:\\Bryce3D\\Bryce 3D.exe,0"

[HKEY_CLASSES_ROOT\Bryce3Dfile\Shell\open\command]
@="\"C:\\Bryce3D\\Bryce 3D.exe\" \"%1\""
EOF
    else
        cat > /tmp/bryce_setup.reg <<'EOF'
Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\Software\Wine]
"Version"="win98"

[HKEY_LOCAL_MACHINE\Software\Wine\Drives]
"d:"="cdrom"
"e:"="hd"

[HKEY_CLASSES_ROOT\.br3]
@="Bryce3Dfile"

[HKEY_CLASSES_ROOT\Bryce3Dfile]
@="Bryce3D File"

[HKEY_CLASSES_ROOT\Bryce3Dfile\DefaultIcon]
@="C:\\Bryce3D\\Bryce 3D.exe,0"

[HKEY_CLASSES_ROOT\Bryce3Dfile\Shell\open\command]
@="\"C:\\Bryce3D\\Bryce 3D.exe\" \"%1\""
EOF
    fi
    WINEPREFIX="$WINE_DIR" wine regedit /tmp/bryce_setup.reg
    wineserver -w
    rm -f /tmp/bryce_setup.reg

    touch "$WINE_DIR/.bryce_initialized"
    log "Wine prefix initialized."
}

# Map standard Windows user folders (Desktop, Documents, Pictures, Videos, Downloads)
# directly to /workspace (./projects on host) so saving anywhere lands on the user's PC.
sync_workspace_links() {
    local user_dir="$WINE_DIR/drive_c/users/bryce"
    if [ -d "$user_dir" ]; then
        for folder in Desktop Documents Pictures Videos Downloads; do
            rm -rf "${user_dir}/${folder}"
            ln -sfn "/workspace" "${user_dir}/${folder}"
        done
    fi
    ln -sfn "/workspace" "$WINE_DIR/dosdevices/e:"
    ln -sfn "$CD_DIR" "$WINE_DIR/dosdevices/d:"
    ln -sfn "/workspace" "$BRYCE_DIR/Projects"
    ln -sfn "$CD_DIR" "$BRYCE_DIR/CD_Content"
}

# ------------------------------------------------------------------------------
# 3. Start X11 + TigerVNC + Openbox + noVNC
# ------------------------------------------------------------------------------
start_display() {
    rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true

    local vnc_args=(
        "${DISPLAY}"
        -geometry "${RESOLUTION}"
        -depth 24
        -rfbport "${VNC_PORT}"
        -localhost no
        -AlwaysShared
        -AcceptSetDesktopSize=1
        -desktop "Bryce 3D (1997)"
    )

    if [ -n "${VNC_PASSWORD:-}" ]; then
        mkdir -p "${HOME}/.vnc"
        printf '%s\n' "${VNC_PASSWORD}" | vncpasswd -f > "${HOME}/.vnc/passwd"
        chmod 600 "${HOME}/.vnc/passwd"
        vnc_args+=(-SecurityTypes VncAuth -PasswordFile "${HOME}/.vnc/passwd")
        log "VNC authentication enabled."
    else
        vnc_args+=(-SecurityTypes None)
        log "VNC authentication disabled (local browser access)."
    fi

    log "Starting Xvnc on ${DISPLAY} (${RESOLUTION})..."
    Xvnc "${vnc_args[@]}" >/tmp/xvnc.log 2>&1 &
    XVNC_PID=$!

    for _ in $(seq 1 50); do
        if xset -display "${DISPLAY}" q >/dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done

    if ! xset -display "${DISPLAY}" q >/dev/null 2>&1; then
        log "ERROR: Xvnc failed to start. Log:"
        cat /tmp/xvnc.log
        exit 1
    fi

    xsetroot -solid "#2b2f36" || true

    log "Starting Openbox window manager..."
    openbox --config-file "${HOME}/.config/openbox/rc.xml" >/tmp/openbox.log 2>&1 &

    # Click proxy keeps Bryce 3D's Mac-style preset popups open on normal clicks/taps
    log "Starting RFB click proxy (127.0.0.1:5901 -> ${VNC_PORT})..."
    python3 /usr/local/bin/rfb_click_proxy.py --listen 5901 --target "${VNC_PORT}" >/tmp/rfb_proxy.log 2>&1 &
    sleep 0.5

    log "Starting noVNC on http://0.0.0.0:${NOVNC_PORT} ..."
    websockify --web=/usr/share/novnc "${NOVNC_PORT}" "127.0.0.1:5901" >/tmp/novnc.log 2>&1 &
    NOVNC_PID=$!
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
if [ ! -f "$BRYCE_DIR/Bryce 3D.exe" ]; then
    install_from_iso
else
    log "Bryce 3D already extracted in ${BRYCE_DIR}."
fi

start_display

# Seamlessly rebuild only the Wine prefix if switching container architectures
# (e.g. Apple Silicon upgrading from QEMU amd64 win32 to native arm64 Hangover WoW64).
# Extracted Bryce 3D files, registration (MetaImage.dll), CD libraries, and /workspace stay intact.
if [ "$IS_ARM64" -eq 1 ] && [ -f "$WINE_DIR/system.reg" ] && grep -q '^#arch=win32' "$WINE_DIR/system.reg"; then
    log "Detected legacy 32-bit x86 Wine prefix on ARM64; rebuilding prefix for native Hangover + FEX..."
    rm -rf "$WINE_DIR"
elif [ "$IS_ARM64" -eq 0 ] && [ -f "$WINE_DIR/system.reg" ] && grep -q '^#arch=win64' "$WINE_DIR/system.reg"; then
    log "Detected 64-bit Wine prefix on x86_64; rebuilding 32-bit Wine prefix..."
    rm -rf "$WINE_DIR"
fi

if [ ! -f "$WINE_DIR/.bryce_initialized" ]; then
    init_wine_prefix
fi

sync_workspace_links

if [ "$IS_ARM64" -eq 1 ]; then
    log "Runtime: Native ARM64 Hangover Wine (WoW64) + FEX (FEX_X87REDUCEDPRECISION=${FEX_X87REDUCEDPRECISION:-1})"
fi

log "=================================================================="
log " Bryce 3D is ready! Open http://localhost:${NOVNC_PORT} in your browser."
log " Save/Export to:  Desktop, Documents, Bryce3D\\Projects, or E:\\ (-> ./projects)"
log " Original CD:     My Computer -> D:\\ (or Bryce3D\\CD_Content)"
log " Right-click desktop background for menu (relaunch Bryce 3D, etc.)"
log "=================================================================="

# Launch Bryce 3D; if closed by the user, keep container alive so they can
# relaunch from the right-click desktop menu (or exit on SIGTERM/SIGINT).
trap 'log "Shutting down..."; kill "${NOVNC_PID:-}" "${XVNC_PID:-}" 2>/dev/null || true; exit 0' SIGTERM SIGINT

auto_personalize() {
    local name="${BRYCE_NAME:-Bryce User}"
    local org="${BRYCE_ORG:-Studio}"
    local serial="${BRYCE_SERIAL:-}"

    if [ -z "$serial" ] || [ -f "$BRYCE_DIR/MetaImage.dll" ]; then
        return 0
    fi

    log "Waiting for Bryce 3D personalization dialog..."
    # Wait until Bryce 3D creates a visible top-level window on X11
    for _ in $(seq 1 40); do
        if [ -f "$BRYCE_DIR/MetaImage.dll" ]; then
            return 0
        fi
        if xdotool search --onlyvisible --class "bryce" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    # Give the personalization dialog a moment to finish rendering
    sleep 2.5

    if [ -f "$BRYCE_DIR/MetaImage.dll" ]; then
        return 0
    fi

    local geom w h cx cy
    geom="$(xdotool getdisplaygeometry)"
    w="${geom%% *}"
    h="${geom##* }"
    cx=$((w / 2))
    cy=$((h / 2))

    log "Applying configured personalization to Bryce 3D..."
    xdotool mousemove --sync $((cx + 40)) $((cy - 25)) click 1
    sleep 0.2
    xdotool type --delay 25 -- "$name"

    xdotool mousemove --sync $((cx + 40)) $((cy + 6)) click 1
    sleep 0.2
    xdotool type --delay 25 -- "$org"

    xdotool mousemove --sync $((cx + 40)) $((cy + 37)) click 1
    sleep 0.2
    xdotool type --delay 25 -- "$serial"

    sleep 0.3
    xdotool mousemove --sync $((cx + 184)) $((cy + 86)) click 1

    for _ in $(seq 1 20); do
        if [ -f "$BRYCE_DIR/MetaImage.dll" ]; then
            log "Bryce 3D personalization saved successfully."
            return 0
        fi
        sleep 0.5
    done
    log "WARNING: Personalization dialog did not complete automatically; you can finish it in the browser."
}

cd "$BRYCE_DIR"
wine "C:\\Bryce3D\\Bryce 3D.exe" >/tmp/bryce.log 2>&1 &

if [ -n "${BRYCE_SERIAL:-}" ] && [ ! -f "$BRYCE_DIR/MetaImage.dll" ]; then
    auto_personalize &
fi

wait "${XVNC_PID}"
