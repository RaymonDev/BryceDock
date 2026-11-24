#!/usr/bin/env python3
"""
BryceDock RFB proxy (sits between websockify/noVNC and Xvnc).

Why this exists
---------------
1. Preset library popups (the small arrows next to Create / Edit / Sky&Fog):
   Bryce 3D uses Mac-style popups that open on mouse-DOWN and close immediately
   if the button is released while the popup is already open (~25 ms under Wine,
   faster than a human click). When button 1 is pressed on one of those three
   arrows, this proxy forwards the press, injects a release 8 ms later, and
   swallows the user's real release.

2. Reliable keyboard shortcuts (especially Ctrl+Z):
   - Auto-hide menu focus recovery: when Bryce 3D's top menu bar slides down
     (e.g. cursor brushing the top edge) and hides, Openbox can leave X11 focus
     on its hidden 1x1 fallback window so shortcuts stop reaching Bryce 3D.
     Before forwarding key-downs, if X11 focus is on Openbox's hidden window
     (never a real app/dialog), we restore focus to Bryce 3D's main window.
   - Non-Latin / IME Ctrl+<letter> shortcuts: under Russian, Greek, Hebrew,
     Arabic, or IME layouts, Ctrl+Z arrives with a non-Latin or zero keysym and
     the physical XT scancode (0x2c). We normalize non-ASCII/non-special keysyms
     while Ctrl is held via physical XT scancode, leaving all Latin layouts
     (QWERTY, QWERTZ, AZERTY, etc.) untouched.
   - macOS Cmd+Z: noVNC maps CmdLeft (scancode 0xdb) to Alt_L and CmdRight
     (0xdc) to Super_L, so Cmd+Z does nothing in Bryce 3D. When Meta (0xdb/0xdc)
     is held and Z is pressed, we cleanly send Ctrl+Z without affecting real Alt
     (0x38/0xb8) or other Cmd/Alt combos (Alt+D Replicate, Alt+Drag, etc.).
"""
import argparse
import asyncio
import ctypes
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

# Keysym / RFB XT-scancode constants
XK_CTRL_L = 0xFFE3
XK_CTRL_R = 0xFFE4
XK_SUPER_L = 0xFFEB
XK_SUPER_R = 0xFFEC
XK_Z_LOWER = 0x007A
XK_Z_UPPER = 0x005A
SC_CTRL_L = 0x1D
SC_CTRL_R = 0x9D
SC_META_L = 0xDB
SC_META_R = 0xDC
SC_KEY_Z = 0x2C

# Physical XT scancode -> lowercase Latin ASCII keysym (used ONLY when Ctrl is
# held AND the incoming keysym is non-ASCII / non-Latin / control / 0).
XT_TO_LATIN_KEYSYM = {
    0x10: ord("q"), 0x11: ord("w"), 0x12: ord("e"), 0x13: ord("r"),
    0x14: ord("t"), 0x15: ord("y"), 0x16: ord("u"), 0x17: ord("i"),
    0x18: ord("o"), 0x19: ord("p"),
    0x1E: ord("a"), 0x1F: ord("s"), 0x20: ord("d"), 0x21: ord("f"),
    0x22: ord("g"), 0x23: ord("h"), 0x24: ord("j"), 0x25: ord("k"),
    0x26: ord("l"),
    0x2C: ord("z"), 0x2D: ord("x"), 0x2E: ord("c"), 0x2F: ord("v"),
    0x30: ord("b"), 0x31: ord("n"), 0x32: ord("m"),
}


def is_ascii_printable_or_special(keysym):
    return (0x0020 <= keysym <= 0x007E) or (0xFF00 <= keysym <= 0xFFFF)


_XERR_FUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_IGNORE_XERR = _XERR_FUNC(lambda dpy, err: 0)


class X11FocusQuery:
    """Fast (~50 us) XGetInputFocus + XFetchName helper via libX11 with safe fallback."""

    def __init__(self):
        self.lib = None
        self.dpy = None
        self.root = None
        try:
            lib = ctypes.CDLL("libX11.so.6")
            lib.XSetErrorHandler.argtypes = [_XERR_FUNC]
            lib.XSetErrorHandler.restype = ctypes.c_void_p
            lib.XSetErrorHandler(_IGNORE_XERR)
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            lib.XDefaultRootWindow.restype = ctypes.c_ulong
            lib.XGetInputFocus.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.XGetInputFocus.restype = ctypes.c_int
            lib.XSetInputFocus.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            lib.XSetInputFocus.restype = ctypes.c_int
            lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.XSync.restype = ctypes.c_int
            lib.XFetchName.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            lib.XFetchName.restype = ctypes.c_int
            lib.XFree.argtypes = [ctypes.c_void_p]
            lib.XFree.restype = ctypes.c_int
            self.lib = lib
            self._open()
        except Exception:
            self.lib = None

    def _open(self):
        if self.lib is None:
            return
        disp = os.environ.get("DISPLAY", ":0").encode()
        dpy = self.lib.XOpenDisplay(disp)
        if dpy:
            self.dpy = dpy
            self.root = int(self.lib.XDefaultRootWindow(dpy))

    def get_focus(self):
        if self.lib is None:
            return None
        if not self.dpy:
            self._open()
            if not self.dpy:
                return None
        win = ctypes.c_ulong(0)
        rev = ctypes.c_int(0)
        try:
            self.lib.XGetInputFocus(self.dpy, ctypes.byref(win), ctypes.byref(rev))
            return int(win.value)
        except Exception:
            return None

    def get_window_name(self, wid):
        if self.lib is None or not self.dpy or not wid:
            return ""
        name_ptr = ctypes.c_void_p(0)
        try:
            if self.lib.XFetchName(self.dpy, ctypes.c_ulong(wid), ctypes.byref(name_ptr)) and name_ptr.value:
                name = ctypes.string_at(name_ptr.value).decode(errors="replace")
                self.lib.XFree(name_ptr)
                return name
        except Exception:
            pass
        return ""

    def set_focus(self, wid):
        if self.lib is None or not self.dpy or not wid:
            return False
        try:
            # RevertToParent = 2, CurrentTime = 0 (focuses without altering window Z-order)
            self.lib.XSetInputFocus(self.dpy, ctypes.c_ulong(wid), 2, 0)
            self.lib.XSync(self.dpy, 0)
            return True
        except Exception:
            return False


class PaletteTracker:
    """Tracks Bryce's 500x80 top palette, main window, and Openbox hidden focus window."""

    def __init__(self):
        w, h = 640, 480
        try:
            w, h = (int(v) for v in os.environ.get("RESOLUTION", "640x480").lower().split("x"))
        except ValueError:
            pass
        self.origin = ((w - 640) // 2 + 140, 38)  # sensible default until first probe
        self.bryce_main_wid = None
        self._openbox_cache = {}  # wid -> bool
        self.last_probe = 0.0
        self.x11 = X11FocusQuery()

    async def refresh_loop(self):
        while True:
            await self.probe()
            await asyncio.sleep(2.0)

    async def _run_xdotool(self, *args, timeout=2.0):
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return out.decode(errors="replace")
        except Exception:
            return ""

    async def probe(self):
        out = await self._run_xdotool(
            "search", "--onlyvisible", ".", "getwindowgeometry", "--shell", "%@",
            timeout=3.0)
        cur = {}
        found_palette = None
        large_windows = []
        for line in out.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            cur[k] = v
            if k == "SCREEN":
                try:
                    wid = int(cur.get("WINDOW", 0))
                    ww = int(cur.get("WIDTH", 0))
                    wh = int(cur.get("HEIGHT", 0))
                    wx = int(cur.get("X", 0))
                    wy = int(cur.get("Y", 0))
                    if ww == 500 and wh == 80 and found_palette is None:
                        found_palette = (wx, wy)
                    if ww >= 600 and wh >= 400:
                        large_windows.append(wid)
                except ValueError:
                    pass
                cur = {}
        self.origin = found_palette  # None when no palette is visible (e.g. inside Materials Lab)

        # Find Bryce 3D's main window (640x480 "Bryce 3D", never the thin top menu bar)
        main_wid = None
        for wid in large_windows:
            if self.x11.get_window_name(wid) == "Bryce 3D":
                main_wid = wid
                break
        if main_wid is None:
            bryce_out = await self._run_xdotool("search", "--onlyvisible", "--name", "^Bryce 3D$")
            for tok in bryce_out.split():
                try:
                    wid = int(tok)
                    if wid in large_windows:
                        main_wid = wid
                        break
                except ValueError:
                    pass
        self.bryce_main_wid = main_wid
        self.last_probe = time.monotonic()

    async def _is_dead_focus(self, focused):
        if focused in (0, 1):
            return True
        if self.x11.root is not None and focused == self.x11.root:
            return True
        cached = self._openbox_cache.get(focused)
        if cached is not None:
            return cached
        # Openbox sets _NET_WM_NAME ("Openbox") rather than legacy WM_NAME, so
        # query via xdotool once per window ID and cache the result.
        name = (await self._run_xdotool("getwindowname", str(focused), timeout=1.0)).strip()
        is_ob = (name == "Openbox")
        if len(self._openbox_cache) > 128:
            self._openbox_cache.clear()
        self._openbox_cache[focused] = is_ob
        return is_ob

    async def ensure_bryce_focus(self):
        """If X11 focus fell onto Openbox's hidden 1x1 window, restore it to Bryce 3D."""
        focused = self.x11.get_focus()
        if focused is None or not await self._is_dead_focus(focused):
            return
        if self.bryce_main_wid is None:
            await self.probe()
        if self.bryce_main_wid is not None:
            if not self.x11.set_focus(self.bryce_main_wid):
                await self._run_xdotool("windowfocus", "--sync", str(self.bryce_main_wid), timeout=1.0)

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


def pack_rfb_key(down, keysym):
    return struct.pack("!BBHI", 4, 1 if down else 0, 0, keysym)


def pack_qemu_key(down, keysym, scancode):
    return struct.pack("!BBHII", 255, 0, 1 if down else 0, keysym, scancode)


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
        ctrl_down = set()              # active Ctrl keys ("L", "R")
        meta_down = {}                 # scancode (0xdb/0xdc) -> [keysym, forwarded_bool]
        legacy_super_down = {}         # keysym (0xffeb/0xffec) -> forwarded_bool
        rewritten_qemu = {}            # scancode -> rewritten keysym
        rewritten_rfb = {}             # original keysym -> rewritten keysym
        swallow_qemu_keyup = set()     # scancodes whose release should be swallowed
        swallow_rfb_keyup = set()      # keysyms whose release should be swallowed

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
            elif t == 4:      # KeyEvent (legacy RFB)
                body = await reader.readexactly(7)
                down, _, keysym = struct.unpack("!BHI", body)
                if keysym == XK_CTRL_L:
                    ctrl_down.add("L") if down else ctrl_down.discard("L")
                elif keysym == XK_CTRL_R:
                    ctrl_down.add("R") if down else ctrl_down.discard("R")
                elif keysym in (XK_SUPER_L, XK_SUPER_R):
                    if down:
                        legacy_super_down[keysym] = True
                        await fwd(bytes([t]) + body)
                    else:
                        was_fwd = legacy_super_down.pop(keysym, True)
                        swallow_rfb_keyup.clear()
                        if was_fwd:
                            await fwd(bytes([t]) + body)
                    continue

                if down:
                    await tracker.ensure_bryce_focus()
                    # Super/Cmd + Z -> Ctrl+Z
                    if legacy_super_down and not ctrl_down and keysym in (XK_Z_LOWER, XK_Z_UPPER):
                        for mk in list(legacy_super_down):
                            if legacy_super_down[mk]:
                                await fwd(pack_rfb_key(False, mk))
                                legacy_super_down[mk] = False
                        await fwd(pack_rfb_key(True, XK_CTRL_L))
                        await asyncio.sleep(0.01)
                        await fwd(pack_rfb_key(True, XK_Z_LOWER))
                        await asyncio.sleep(0.01)
                        await fwd(pack_rfb_key(False, XK_Z_LOWER))
                        await asyncio.sleep(0.01)
                        await fwd(pack_rfb_key(False, XK_CTRL_L))
                        swallow_rfb_keyup.add(keysym)
                        continue
                    # Control-char keysym (e.g. 0x001a for Ctrl+Z) -> Latin letter
                    if ctrl_down and 0x0001 <= keysym <= 0x001A:
                        mapped = 0x0060 + keysym
                        rewritten_rfb[keysym] = mapped
                        await fwd(pack_rfb_key(True, mapped))
                        continue
                else:
                    if keysym in swallow_rfb_keyup:
                        swallow_rfb_keyup.discard(keysym)
                        continue
                    if keysym in rewritten_rfb:
                        mapped = rewritten_rfb.pop(keysym)
                        await fwd(pack_rfb_key(False, mapped))
                        continue

                await fwd(bytes([t]) + body)
            elif t == 6:      # ClientCutText (negative signed length = ExtendedClipboard)
                hdr = await reader.readexactly(7)
                ln = abs(struct.unpack("!i", hdr[3:7])[0])
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
                if sub[0] == 0:   # QEMUExtendedKeyEvent
                    body = await reader.readexactly(10)
                    down, keysym, scancode = struct.unpack("!HII", body)

                    if scancode == SC_CTRL_L or keysym == XK_CTRL_L:
                        ctrl_down.add("L") if down else ctrl_down.discard("L")
                    elif scancode == SC_CTRL_R or keysym == XK_CTRL_R:
                        ctrl_down.add("R") if down else ctrl_down.discard("R")
                    elif scancode in (SC_META_L, SC_META_R):
                        if down:
                            meta_down[scancode] = [keysym, True]
                            await fwd(bytes([t]) + sub + body)
                        else:
                            state = meta_down.pop(scancode, None)
                            swallow_qemu_keyup.clear()
                            if state is None or state[1]:
                                await fwd(bytes([t]) + sub + body)
                        continue

                    if down:
                        await tracker.ensure_bryce_focus()
                        # macOS Cmd+Z (Meta scancode 0xdb/0xdc + Z) -> Ctrl+Z
                        is_z = (
                            keysym in (XK_Z_LOWER, XK_Z_UPPER)
                            or (scancode == SC_KEY_Z and not is_ascii_printable_or_special(keysym))
                        )
                        if meta_down and not ctrl_down and is_z:
                            for msc, mstate in list(meta_down.items()):
                                if mstate[1]:
                                    await fwd(pack_qemu_key(False, mstate[0], msc))
                                    mstate[1] = False
                            await fwd(pack_qemu_key(True, XK_CTRL_L, SC_CTRL_L))
                            await asyncio.sleep(0.01)
                            await fwd(pack_qemu_key(True, XK_Z_LOWER, SC_KEY_Z))
                            await asyncio.sleep(0.01)
                            await fwd(pack_qemu_key(False, XK_Z_LOWER, SC_KEY_Z))
                            await asyncio.sleep(0.01)
                            await fwd(pack_qemu_key(False, XK_CTRL_L, SC_CTRL_L))
                            swallow_qemu_keyup.add(scancode)
                            continue
                        # Non-Latin / IME / control-char Ctrl+<letter> -> Latin keysym via XT scancode
                        if (
                            ctrl_down
                            and scancode in XT_TO_LATIN_KEYSYM
                            and not is_ascii_printable_or_special(keysym)
                        ):
                            mapped = XT_TO_LATIN_KEYSYM[scancode]
                            rewritten_qemu[scancode] = mapped
                            await fwd(pack_qemu_key(True, mapped, scancode))
                            continue
                    else:
                        if scancode in swallow_qemu_keyup:
                            swallow_qemu_keyup.discard(scancode)
                            continue
                        if scancode in rewritten_qemu:
                            mapped = rewritten_qemu.pop(scancode)
                            await fwd(pack_qemu_key(False, mapped, scancode))
                            continue

                    await fwd(bytes([t]) + sub + body)
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
