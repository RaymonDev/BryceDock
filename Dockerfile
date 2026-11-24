# syntax=docker/dockerfile:1

ARG TARGETARCH

# ==============================================================================
# Stage 1: Build unshieldv3 natively for the target architecture (amd64 / arm64)
# Extracts InstallShield 3 data.1 archives without running the 16-bit installer
# ==============================================================================
FROM debian:bookworm-slim AS unshieldv3-builder

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
# Stage 2: Shared X11 + TigerVNC + Openbox + noVNC runtime base
# ==============================================================================
FROM debian:bookworm-slim AS runtime-base

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:0 \
    RESOLUTION=640x480 \
    WINEPREFIX=/data/wine \
    WINEDEBUG=-all \
    NOVNC_PORT=6080 \
    VNC_PORT=5900 \
    VNC_PASSWORD="" \
    BRYCE_ISO_PATH=/iso/Bryce3DCD.iso \
    BRYCE_ISO_URL=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus-x11 \
        fonts-dejavu-core \
        fonts-liberation \
        libarchive-tools \
        novnc \
        openbox \
        procps \
        python3 \
        tigervnc-standalone-server \
        tigervnc-tools \
        websockify \
        x11-xserver-utils \
        xdotool \
        xterm \
    && rm -rf /var/lib/apt/lists/*

# ==============================================================================
# Stage 2a (amd64 / x86_64): Native 32-bit Wine (zero CPU emulation)
# ==============================================================================
FROM runtime-base AS runtime-amd64

ENV WINEARCH=win32

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1:i386 \
        libgl1-mesa-dri:i386 \
        wine \
        wine32:i386 \
    && rm -rf /var/lib/apt/lists/*

# ==============================================================================
# Stage 2b (arm64 / Apple Silicon): Native ARM64 Hangover Wine (WoW64) + FEX
# Translates Bryce 3D's 32-bit x86 + x87 FP code ~15x faster than QEMU/Rosetta
# (technique pioneered for Bryce 3D by highda/bryce-macos-installer)
# ==============================================================================
FROM runtime-base AS runtime-arm64

ARG HANGOVER_VERSION=11.16
ARG HANGOVER_SHA256=dd7373202a168b7200cc36627a25b588511f3783756c2661bf4c022be8380aa8

ENV HODLL=libwow64fex.dll \
    FEX_X87REDUCEDPRECISION=1 \
    WINEDLLOVERRIDES="mscoree=;mshtml=;winemenubuilder.exe=d"

RUN curl -fsSL -o /tmp/hangover.tar \
        "https://github.com/AndreRH/hangover/releases/download/hangover-${HANGOVER_VERSION}/hangover_${HANGOVER_VERSION}_debian12_bookworm_arm64.tar" \
    && echo "${HANGOVER_SHA256}  /tmp/hangover.tar" | sha256sum -c - \
    && mkdir -p /tmp/hangover \
    && tar -xf /tmp/hangover.tar -C /tmp/hangover \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        /tmp/hangover/*.deb \
        libfontconfig1 \
        libfreetype6 \
        libgl1 \
        libgl1-mesa-dri \
        libxcomposite1 \
        libxcursor1 \
        libxfixes3 \
        libxi6 \
        libxinerama1 \
        libxrandr2 \
        libxrender1 \
    && rm -rf /tmp/hangover /tmp/hangover.tar /var/lib/apt/lists/*

# ==============================================================================
# Stage 3: Final image (automatically selects runtime-amd64 or runtime-arm64)
# ==============================================================================
FROM runtime-${TARGETARCH} AS final

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
