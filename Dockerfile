# syntax=docker/dockerfile:1

# ==============================================================================
# Stage 1: Build unshieldv3 (extracts InstallShield 3 data.1 / data.z archives
# natively on Linux, completely bypassing the legacy 16-bit SETUP.EXE installer)
# ==============================================================================
FROM --platform=linux/amd64 debian:bookworm-slim AS unshieldv3-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/wfr/unshieldv3.git /src/unshieldv3 \
    && cmake -S /src/unshieldv3 -B /src/unshieldv3/build -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build /src/unshieldv3/build -j"$(nproc)" \
    && cmake --install /src/unshieldv3/build

# ==============================================================================
# Stage 2: Runtime image (Wine 32-bit + TigerVNC X11 + Openbox + noVNC)
# ==============================================================================
FROM --platform=linux/amd64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:0 \
    RESOLUTION=640x480 \
    WINEARCH=win32 \
    WINEPREFIX=/data/wine \
    WINEDEBUG=-all \
    NOVNC_PORT=6080 \
    VNC_PORT=5900 \
    VNC_PASSWORD="" \
    BRYCE_ISO_PATH=/iso/Bryce3DCD.iso \
    BRYCE_ISO_URL=""

# Enable 32-bit architecture for Wine32 (Bryce 3D is a 32-bit Win95/NT4 app)
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus-x11 \
        fonts-dejavu-core \
        fonts-liberation \
        libarchive-tools \
        libgl1:i386 \
        libgl1-mesa-dri:i386 \
        novnc \
        openbox \
        procps \
        python3 \
        tigervnc-standalone-server \
        tigervnc-tools \
        websockify \
        wine \
        wine32:i386 \
        x11-xserver-utils \
        xdotool \
        xterm \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled InstallShield 3 unpacker
COPY --from=unshieldv3-builder /usr/local/bin/unshieldv3 /usr/local/bin/unshieldv3

# Auto-connect noVNC redirect so opening http://localhost:6080 goes straight to Bryce 3D
# Uses resize=scale so Bryce 3D's fixed-resolution 1997 workspace fits any browser window cleanly
RUN printf '%s\n' \
    '<!DOCTYPE html>' \
    '<html><head><meta charset="utf-8">' \
    '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true&reconnect_delay=2000">' \
    '<title>Bryce 3D</title></head>' \
    '<body style="background:#1a1a1a;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">' \
    '<p>Launching Bryce 3D&hellip; <a style="color:#6cf" href="vnc.html?autoconnect=true&resize=scale&reconnect=true">Click here</a></p>' \
    '</body></html>' \
    > /usr/share/novnc/index.html

# Create non-root user and runtime directories (including /tmp/.X11-unix for non-root Xvnc)
RUN useradd -m -s /bin/bash -u 1000 bryce \
    && mkdir -p /data /iso /workspace /home/bryce/.config/openbox /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix \
    && chown -R bryce:bryce /data /workspace /home/bryce

COPY --chown=bryce:bryce openbox/rc.xml /home/bryce/.config/openbox/rc.xml
COPY --chown=bryce:bryce openbox/menu.xml /home/bryce/.config/openbox/menu.xml
COPY entrypoint.sh rfb_click_proxy.py /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /usr/local/bin/rfb_click_proxy.py \
    && chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/rfb_click_proxy.py

USER bryce
WORKDIR /home/bryce

EXPOSE 6080 5900

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${NOVNC_PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
