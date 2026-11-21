#!/usr/bin/env python3
"""
BryceDock RFB click proxy (sits between websockify/noVNC and Xvnc).

Why this exists
---------------
Bryce 3D's preset libraries (the small arrows next to Create / Edit / Sky&Fog)
use Mac-style popups: the popup opens on mouse-DOWN, and if the mouse button is
released while the popup is already open (and not over an item), it closes.
In 1997 the popup took longer to appear than a human click, so the button was
always up first. Under Wine on a modern CPU the popup appears in ~25 ms, before
a normal click (~80-150 ms, mouse or touchpad) is released, so it closes at once.

Fix: when button 1 is pressed on one of those three arrows, this proxy forwards
the press, then a release 8 ms later, and swallows the user's real release.
Everything else (dragging, sliders, trackballs, keyboard...) is passed through
untouched. It works for every VNC client (any browser, mouse or touchpad)
because it runs server-side.
"""
import argparse
import asyncio
import os
import socket
import struct
import time

# Arrow hotboxes relative to Bryce's 500x80 top palette window (x0, x1, y0, y1)
ARROW_BOXES = [
    (52, 82, 0, 26),    # Create  -> Objects presets
    (114, 144, 0, 26),  # Edit    -> Materials presets
    (217, 247, 0, 26),  # Sky&Fog -> Sky presets
]
QUICK_RELEASE_S = 0.008


class PaletteTracker:
    """Tracks the on-screen position of Bryce's 500x80 top palette via xdotool."""

    def __init__(self):
        w, h = 640, 480
        try:
            w, h = (int(v) for v in os.environ.get("RESOLUTION", "640x480").lower().split("x"))
        except ValueError:
            pass
        self.origin = ((w - 640) // 2 + 140, 38)  # sensible default until first probe
        self.last_probe = 0.0

    async def refresh_loop(self):
        while True:
            await self.probe()
            await asyncio.sleep(2.0)

    async def probe(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "search", "--onlyvisible", ".", "getwindowgeometry", "--shell", "%@",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        except Exception:
            return
        cur = {}
        found = None
        for line in out.decode(errors="replace").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            cur[k] = v
            if k == "SCREEN":
                try:
                    if int(cur.get("WIDTH", 0)) == 500 and int(cur.get("HEIGHT", 0)) == 80:
                        found = (int(cur["X"]), int(cur["Y"]))
                        break
                except ValueError:
                    pass
                cur = {}
        self.origin = found  # None when no palette is visible (e.g. inside Materials Lab)
        self.last_probe = time.monotonic()

    def in_hotbox(self, x, y):
        if self.origin is None:
            return False
        rx, ry = x - self.origin[0], y - self.origin[1]
        return any(x0 <= rx <= x1 and y0 <= ry <= y1 for x0, x1, y0, y1 in ARROW_BOXES)


async def pipe_raw(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def client_to_server(reader, writer, tracker):
    async def fwd(data):
        writer.write(data)
        await writer.drain()

    try:
        # --- Handshake ---
        version = await reader.readexactly(12)
        await fwd(version)
        if version.startswith(b"RFB 003.003"):
            pass  # server chooses security; no client byte
        else:
            sec = await reader.readexactly(1)
            await fwd(sec)
            if sec[0] == 2:          # VNC auth response
                await fwd(await reader.readexactly(16))
            elif sec[0] != 1:        # unknown security type -> raw passthrough
                return await pipe_raw(reader, writer)
        await fwd(await reader.readexactly(1))  # ClientInit (shared flag)

        prev_mask = 0
        suppress_release = False
        while True:
            t = (await reader.readexactly(1))[0]
            if t == 5:  # PointerEvent
                body = await reader.readexactly(5)
                mask, x, y = struct.unpack("!BHH", body)
                if suppress_release:
                    if mask & 1:
                        await fwd(struct.pack("!BBHH", 5, mask & ~1, x, y))
                        prev_mask = mask
                        continue
                    suppress_release = False
                    await fwd(struct.pack("!BBHH", 5, mask, x, y))
                    prev_mask = mask
                    continue
                if (mask & 1) and not (prev_mask & 1) and tracker.in_hotbox(x, y):
                    await fwd(struct.pack("!BBHH", 5, mask, x, y))
                    await asyncio.sleep(QUICK_RELEASE_S)
                    await fwd(struct.pack("!BBHH", 5, mask & ~1, x, y))
                    suppress_release = True
                    prev_mask = mask
                    continue
                await fwd(bytes([t]) + body)
                prev_mask = mask
            elif t == 0:      # SetPixelFormat
                await fwd(bytes([t]) + await reader.readexactly(19))
            elif t == 2:      # SetEncodings
                hdr = await reader.readexactly(3)
                n = struct.unpack("!H", hdr[1:3])[0]
                await fwd(bytes([t]) + hdr + await reader.readexactly(4 * n))
            elif t == 3:      # FramebufferUpdateRequest
                await fwd(bytes([t]) + await reader.readexactly(9))
            elif t == 4:      # KeyEvent
                await fwd(bytes([t]) + await reader.readexactly(7))
            elif t == 6:      # ClientCutText
                hdr = await reader.readexactly(7)
                ln = struct.unpack("!I", hdr[3:7])[0]
                await fwd(bytes([t]) + hdr + await reader.readexactly(ln))
            elif t == 150:    # EnableContinuousUpdates
                await fwd(bytes([t]) + await reader.readexactly(9))
            elif t == 248:    # ClientFence
                hdr = await reader.readexactly(8)
                await fwd(bytes([t]) + hdr + await reader.readexactly(hdr[7]))
            elif t == 250:    # xvp
                await fwd(bytes([t]) + await reader.readexactly(3))
            elif t == 251:    # SetDesktopSize
                hdr = await reader.readexactly(7)
                await fwd(bytes([t]) + hdr + await reader.readexactly(16 * hdr[5]))
            elif t == 255:    # QEMU client message
                sub = await reader.readexactly(1)
                if sub[0] == 0:   # extended key event
                    await fwd(bytes([t]) + sub + await reader.readexactly(10))
                else:
                    await fwd(bytes([t]) + sub)
                    return await pipe_raw(reader, writer)
            else:             # unknown message -> stop parsing, pass everything through
                await fwd(bytes([t]))
                return await pipe_raw(reader, writer)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader, client_writer, target_port, tracker):
    try:
        server_reader, server_writer = await asyncio.open_connection("127.0.0.1", target_port)
    except OSError:
        client_writer.close()
        return
    for w in (client_writer, server_writer):
        sock = w.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    await asyncio.gather(
        client_to_server(client_reader, server_writer, tracker),
        pipe_raw(server_reader, client_writer),
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=5901)
    ap.add_argument("--target", type=int, default=5900)
    args = ap.parse_args()
    tracker = PaletteTracker()
    asyncio.create_task(tracker.refresh_loop())
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, args.target, tracker), "127.0.0.1", args.listen)
    print(f"rfb_click_proxy: 127.0.0.1:{args.listen} -> 127.0.0.1:{args.target}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
