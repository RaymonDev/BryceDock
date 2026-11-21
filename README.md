# BryceDock

> **Run classic 1997 Bryce 3D in your browser from a single Docker command — no X11 setup, no 16-bit installer headaches, no GPU passthrough required.**

**BryceDock** packages a clean 32-bit Wine runtime, a native Linux InstallShield 3 extractor (`unshieldv3`), TigerVNC (`Xvnc`), Openbox, an RFB click proxy (for 1997 Mac-style preset popups), and noVNC into a lightweight Docker container (~565 MB compressed). Drop in your `Bryce3DCD.iso`, launch the container, and sculpt & raytrace 1997 procedural landscapes directly in a browser tab.

---

## Table of Contents
1. [Platform Compatibility](#platform-compatibility)
2. [Performance](#performance)
3. [Step 1: Install Docker](#step-1-install-docker)
4. [Step 2: Prepare BryceDock & Your ISO](#step-2-prepare-brycedock--your-iso)
5. [Step 3: Build & Launch](#step-3-build--launch)
6. [Step 4: Run & Use Bryce 3D](#step-4-run--use-bryce-3d)
7. [Step 5: Manage, Update & Uninstall](#step-5-manage-update--uninstall)
8. [Running Without Docker Compose](#running-without-docker-compose)
9. [Configuration Options](#configuration-options)
10. [How It Works](#how-it-works)
11. [Troubleshooting](#troubleshooting)
12. [Legal Notice](#legal-notice)

---

## Platform Compatibility

| Operating System | Architecture | Status | Notes |
| --- | --- | --- | --- |
| **Linux** | `x86_64` (AMD64) | **Full native speed** | Runs natively via Docker Engine + Compose with near-zero CPU overhead. |
| **Windows 10 / 11** | `x86_64` (AMD64) | **Full native speed** | Works via Docker Desktop (WSL2 backend) or Docker Engine inside WSL2. |
| **macOS (Intel)** | `x86_64` (AMD64) | **Full native speed** | Runs via Docker Desktop, OrbStack, or Colima. |
| **macOS (Apple Silicon M1–M4)** | `arm64` | **Works via QEMU emulation (slower)** | `docker-compose.yml` pins `platform: linux/amd64`. Because Rosetta 2 only translates 64-bit x86_64 and **not** 32-bit (`i386`) Wine binaries, disable *"Use Rosetta for x86_64/amd64 emulation"* in Docker Desktop settings so Docker uses **QEMU**. |
| **Linux ARM64** (e.g., Raspberry Pi) | `arm64` | **Experimental via QEMU** | Requires `qemu-user-static` (`binfmt_misc`) for `linux/amd64` + `i386` emulation; significantly slower than x86_64. |

---

## Performance

- **First-time Docker image build:** ~1–2 minutes (cached after that)
- **Cold first start** (extracts `Bryce3DCD.iso`, initializes 32-bit Wine, applies `.env` personalization): **~16 seconds**
- **Warm start / restart** (reuses persisted `bryce3d_data` volume): **~2–4 seconds**
- **UI responsiveness:** Smooth and immediate over local browser noVNC (`http://localhost:6080`)
- **Raytracing speed:** On x86_64 (Linux, Windows, Intel Mac), Wine executes 32-bit x86 instructions **natively on your CPU** (not emulated). Even though Bryce 3D uses single-threaded software raytracing (no GPU acceleration existed in 1997), modern single-core IPC makes it dramatically faster than 1997 hardware:
  - Default fractal mountain terrain (`172,800` pixels, `340,307` total rays, anti-aliased): **~1 second (`00:01`)**
  - Complex scenes with reflections, volumetric clouds, transparency, and high resolutions will take seconds to minutes depending on single-core CPU speed (and longer under QEMU on Apple Silicon).

---

## Step 1: Install Docker

Skip this step if `docker --version` and `docker compose version` already work on your computer.

### Windows 10 / 11
1. Download and install **[Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)**.
2. During installation, keep **"Use WSL 2 instead of Hyper-V"** checked (reboot if prompted).
3. Launch **Docker Desktop** and wait until the status bar says **Engine running**.
4. Open **PowerShell** (or **Windows Terminal**) and verify:
   ```powershell
   docker --version
   docker compose version
   ```
   *(If you use Docker Engine directly inside WSL2 Ubuntu without Docker Desktop, run the Linux commands below inside your WSL2 Ubuntu terminal.)*

### macOS (Intel & Apple Silicon)
1. Download and install **[Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/)** (choose the Intel or Apple Silicon installer matching your Mac).
2. Open **Docker Desktop** from Applications and wait until the whale icon in the menu bar is steady.
3. **Apple Silicon (M1/M2/M3/M4) only:** Open Docker Desktop **Settings → General** and **uncheck** *"Use Rosetta for x86_64/amd64 emulation on Apple Silicon"*, then click **Apply & Restart**.
4. Open **Terminal** and verify:
   ```bash
   docker --version
   docker compose version
   ```

### Linux (Ubuntu / Debian)
1. Install Docker Engine and the Compose plugin:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker "$USER"
   ```
2. Log out and back in (or run `newgrp docker`) so your user can run `docker` without `sudo`.
3. Verify:
   ```bash
   docker --version
   docker compose version
   ```

---

## Step 2: Prepare BryceDock & Your ISO

1. **Clone the repository** and open a terminal inside the project folder:
   ```bash
   git clone https://github.com/RaymonDev/BryceDock.git
   cd BryceDock
   ```
2. **Add your `Bryce3DCD.iso`:**
   - Copy your `Bryce3DCD.iso` file into this folder (right next to `docker-compose.yml`).
   - *Alternative (no local ISO file):* Remove the `./Bryce3DCD.iso:/iso/Bryce3DCD.iso:ro` line in `docker-compose.yml` and set `BRYCE_ISO_URL` to a direct download URL in `.env` or `docker-compose.yml`.
3. **(Optional) Pre-configure first-run personalization:**
   Create a local `.env` file from the template so Bryce 3D skips the first-launch serial prompt automatically:
   - **Linux / macOS:**
     ```bash
     cp .env.example .env
     ```
   - **Windows PowerShell:**
     ```powershell
     Copy-Item .env.example .env
     ```
   Open `.env` in any text editor and fill in your Bryce 3D serial number:
   ```env
   BRYCE_NAME=Bryce User
   BRYCE_ORG=Studio
   BRYCE_SERIAL=YOUR-SERIAL-HERE
   ```
   *(If you skip this step, Bryce 3D simply displays its personalization dialog inside the browser on first launch and saves your entry permanently in the Docker volume.)*

Your folder should now look like:
```text
BryceDock/
├── Bryce3DCD.iso
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── rfb_click_proxy.py
├── .env                  # (optional, excluded from Docker image & Git)
├── .env.example
├── openbox/
│   ├── menu.xml
│   └── rc.xml
└── README.md
```

---

## Step 3: Build & Launch

Run this single command from inside the project folder (works identically on **Windows PowerShell**, **macOS Terminal**, and **Linux**):

```bash
docker compose up -d --build
```

What happens automatically:
1. Docker builds the `brycedock` image (~1–2 minutes the first time; instant on later runs).
2. On first boot, the container unpacks `data.1` from `Bryce3DCD.iso` with `unshieldv3`, extracts the original CD libraries, initializes the 32-bit Wine prefix, starts the X11/noVNC desktop + RFB click proxy, and launches Bryce 3D (~16 seconds).

You can watch startup progress until you see `Bryce 3D is ready!`:
```bash
docker compose logs -f
```
*(Press `Ctrl + C` to exit the log viewer — the container keeps running in the background.)*

Verify the container is healthy:
```bash
docker compose ps
```
You should see status `Up ... (healthy)` with port `0.0.0.0:6080->6080/tcp`.

---

## Step 4: Run & Use Bryce 3D

1. Open any modern web browser (Chrome, Firefox, Edge, Safari) and go to:
   ```
   http://localhost:6080
   ```
2. noVNC connects automatically (`resize=scale` fits the workspace cleanly to your browser window).
3. If you didn't set `BRYCE_SERIAL` in `.env`, enter your **Name**, **Organization**, and **Serial Number** once and click the **✓** button.

### First Things to Try
- **True fullscreen in your browser:** Click the small collapsible tab on the left edge of the browser page (noVNC sidebar) and click the **Fullscreen** icon (or press `F11`). BryceDock defaults to Bryce 3D's native `640x480` layout scaled automatically by noVNC (`resize=scale`) so the interface fills your screen cleanly without oversized borders.
- **Reveal the top menu bar:** Move your mouse cursor all the way to the **very top edge** of the Bryce 3D screen to reveal `File | Edit | Objects | Help`.
- **Create & render a mountain:** Click the **Mountain (Terrain)** icon on the top **Create** bar, then click the large **Render** sphere on the left sidebar (or press `Ctrl+R`).
- **Browse preset libraries:** Click the small `▾` arrows next to **Create** (Objects), **Edit** (Materials), or **Sky&Fog** (Skies) — works cleanly with both mouse clicks and laptop touchpad taps.
- **Open classic 1997 sample scenes:** Go to **File → Open...** (`Ctrl+O`), open **My Computer → `D:`** (or **Bryce3D → CD_Content**), and browse **`Sample Scenes With Animation`** (e.g., `Eric Wenger\Techno City.br3` or `Lava lake.br3`).
- **Relaunch Bryce 3D if you close it:** Right-click anywhere on the dark desktop background to open the Openbox menu (**Launch Bryce 3D**, **Wine Configuration**, **Terminal**).

### Saving Scenes, Exporting Renders & Transferring Files

To make exporting effortless, **Desktop**, **Documents**, **Pictures**, **Videos**, **Bryce3D → Projects**, and **My Computer → `E:`** inside Bryce 3D are **all mapped directly to the `./projects/` folder on your PC**. Wherever you choose to save, your files appear immediately in `./projects/` on your computer (and any file you drop into `./projects/` on your PC is immediately visible inside **Documents** / **Desktop** in Bryce 3D):

1. **Save your Bryce 3D scene (`.br3`):**
   - Move the mouse to the very top edge → **File → Save As...** (or press `Ctrl+S`).
   - Click **Documents** or **Desktop** (or open the **Projects** folder inside **Bryce3D**), enter a filename (e.g., `my_landscape.br3`), and click **Save**.
   - Find it immediately on your PC at `./projects/my_landscape.br3`.
2. **Export a rendered image (`.bmp` / `.psd`):**
   - First render your scene (`Ctrl+R` or click the large **Render** sphere on the left).
   - Move the mouse to the top edge → **File → Export Image...**.
   - Save to **Documents** or **Desktop**, pick the format (**Windows Bitmap `.bmp`** or **Photoshop `.psd`**), and click **Save**.
   - Find your exported image on your PC in `./projects/`.
3. **Render a high-resolution image directly to disk:**
   - Move the mouse to the top edge → **File → Render to Disk...**.
   - Choose your output resolution (e.g., `1920x1080` or higher) and save directly to **Documents** or **Desktop** (`./projects/`).
4. **Render & export an animation (`.avi` or image sequence):**
   - Configure frame count/FPS via **File → Animation Setup...** (`Ctrl+Shift+N`).
   - Go to **File → Render Animation...** (`Ctrl+Shift+R`), select **AVI Movie** or **BMP Sequence**, and save to **Documents** or **Desktop** (`./projects/`).
5. **Import your own 3D models or textures from your PC:**
   - Copy `.obj`, `.3ds`, `.dxf`, or image files into `./projects/` on your PC.
   - Inside Bryce 3D, open **File → Import Object...** (or **File → Open Image...**) and pick them from **Documents** or **Desktop**.

### File & Drive Map

| Inside Bryce 3D | Host / Container Path | Purpose |
| --- | --- | --- |
| **Documents**, **Desktop**, **Bryce3D → Projects**, or **My Computer → `E:`** | `./projects/` on your PC | Save `.br3` scenes, export rendered `.bmp`/`.psd`/`.avi` files, and import your own `.obj`/`.3ds`/`.dxf` models |
| **Bryce3D → CD_Content** or **My Computer → `D:`** | Extracted into `bryce3d_data` | Original CD sample scenes, model/texture/sky/terrain libraries, tutorials, and User Guide PDFs |

---

## Step 5: Manage, Update & Uninstall

### Pause / Stop (keeps your installation & settings)
```bash
docker compose stop
```
Start again later in ~2–4 seconds:
```bash
docker compose start
```

### Restart Bryce 3D
```bash
docker compose restart
```

### Stop & Remove Container (keeps `bryce3d_data` volume and `./projects`)
```bash
docker compose down
```

### Apply Changes to `docker-compose.yml`, `.env`, or `RESOLUTION`
```bash
docker compose up -d --force-recreate
```

### Full Clean Reset (re-extract ISO & fresh Wine prefix)
```bash
docker compose down -v
docker compose up -d
```
*(Your `./projects/` folder on the host is never deleted by Docker.)*

### Complete Uninstall
```bash
docker compose down -v --rmi local
```

---

## Running Without Docker Compose

If you prefer plain `docker build` and `docker run`:

**Linux / macOS (Bash/Zsh):**
```bash
docker build -t brycedock .

docker run -d --name brycedock --platform linux/amd64 \
  -p 6080:6080 \
  --shm-size=512m \
  -e RESOLUTION=640x480 \
  -e BRYCE_NAME="Bryce User" \
  -e BRYCE_ORG="Studio" \
  -e BRYCE_SERIAL="YOUR-SERIAL-HERE" \
  -v "$(pwd)/Bryce3DCD.iso:/iso/Bryce3DCD.iso:ro" \
  -v "$(pwd)/projects:/workspace" \
  -v brycedock_data:/data \
  brycedock
```

**Windows (PowerShell):**
```powershell
docker build -t brycedock .

docker run -d --name brycedock --platform linux/amd64 `
  -p 6080:6080 `
  --shm-size=512m `
  -e RESOLUTION=640x480 `
  -e BRYCE_NAME="Bryce User" `
  -e BRYCE_ORG="Studio" `
  -e BRYCE_SERIAL="YOUR-SERIAL-HERE" `
  -v "${PWD}\Bryce3DCD.iso:/iso/Bryce3DCD.iso:ro" `
  -v "${PWD}\projects:/workspace" `
  -v brycedock_data:/data `
  brycedock
```

**Auto-downloading the ISO from a URL (no local ISO file):**
```bash
docker run -d --name brycedock --platform linux/amd64 \
  -p 6080:6080 \
  --shm-size=512m \
  -e BRYCE_ISO_URL="https://example.org/path/to/Bryce3DCD.iso" \
  -v "$(pwd)/projects:/workspace" \
  -v brycedock_data:/data \
  brycedock
```

---

## Configuration Options

Set these in `.env` or under `environment:` in `docker-compose.yml`:

| Variable | Default | Description |
| --- | --- | --- |
| `RESOLUTION` | `640x480` | Virtual X11 desktop resolution. `640x480` matches Bryce 3D's native 1997 interface so it fills the browser cleanly; increase to `800x600` or `1024x768` if you expand the canvas via **File → Document Setup** |
| `BRYCE_NAME` | `Bryce User` | Name used for automatic first-run personalization |
| `BRYCE_ORG` | `Studio` | Organization used for automatic first-run personalization |
| `BRYCE_SERIAL` | *(empty)* | Optional serial number for automatic first-run personalization (kept in `.env`, never baked into the image) |
| `VNC_PASSWORD` | *(empty)* | Optional password to protect browser/VNC access on port `6080` / `5900` |
| `BRYCE_ISO_URL` | *(empty)* | Optional URL to download `Bryce3DCD.iso` on first boot if `/iso/Bryce3DCD.iso` is not mounted |

---

## How It Works

1. **Cross-platform browser GUI:** TigerVNC (`Xvnc`) provides an X11 display managed by Openbox, proxied to the browser by `websockify` + noVNC (`resize=scale` fits Bryce 3D's classic fixed-layout canvas neatly in any window).
2. **Server-side RFB click proxy (`rfb_click_proxy.py`):** Bryce 3D's `Create ▾`, `Edit ▾`, and `Sky&Fog ▾` preset libraries open on mouse-down and close if the button releases after the popup maps (~25 ms on modern CPUs). The lightweight server-side proxy sits between `websockify` and `Xvnc`, automatically sending a clean 8 ms release on those three preset arrows while passing every other click, drag, and slider through untouched — working reliably across all browsers, mice, and laptop touchpads.
3. **No 16-bit installer failures:** Bryce 3D's original `SETUP.EXE` is a 16-bit Windows 3.1/95 InstallShield 3 executable. BryceDock compiles [`unshieldv3`](https://github.com/wfr/unshieldv3) in a multi-stage build and extracts `data.1` natively on Linux via `bsdtar` + `unshieldv3`.
4. **32-bit Wine prefix:** Configures `WINEARCH=win32` in Windows 98 mode with 32-bit Mesa OpenGL libraries (`libgl1:i386`, `libgl1-mesa-dri:i386`), maps `Desktop`/`Documents`/`Projects`/`E:` to `./projects/`, and registers `.br3` file associations.
5. **Copyright-clean image:** `.dockerignore` and `.gitignore` exclude `Bryce3DCD.iso` and `.env`, so neither copyrighted software nor personal serial numbers are ever baked into the Docker image.

---

## Troubleshooting

- **Port `6080` is already in use:** Edit `docker-compose.yml` and change `"6080:6080"` to `"6081:6080"`, then open `http://localhost:6081`.
- **Apple Silicon Mac crashes or hangs launching Wine:** Open Docker Desktop **Settings → General** and disable *"Use Rosetta for x86_64/amd64 emulation on Apple Silicon"* so Docker uses QEMU (Rosetta 2 does not support 32-bit x86 instructions required by `wine32:i386`).
- **Cannot save to Documents/Desktop on Linux:** The container runs as UID `1000` (`bryce`). If Docker created `./projects` owned by `root`, run `sudo chown -R 1000:1000 ./projects` on the host.
- **Bryce 3D window closed:** Right-click the desktop background in the browser and click **Launch Bryce 3D**.
- **Want to re-enter registration or reset preferences:** Run `docker compose down -v && docker compose up -d`.

---

## Legal Notice

Bryce 3D is copyright MetaCreations Corp. / DAZ 3D. This repository contains **only** container build recipes and configuration scripts — no Bryce 3D binaries, CD assets, or license keys are included or distributed. You must supply your own legal copy of `Bryce3DCD.iso` and serial number.
