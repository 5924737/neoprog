"""Packaged-app entry point for the CH341A SPI flash tool.

This is the PyInstaller entry point (see ch341a-tool.spec / build.sh) - the
GUI itself lives in ch341a/app.py. This file only adds what a standalone
Linux desktop build needs: a private, distinctly-named copy of the shared
Flet runtime (so GNOME's dock shows this app's own icon instead of a
generic "flet" one) and a self-installing .desktop launcher + icon, using
the same approach as this workspace's other Flet-based tools (CamWGF,
ssh-wg-wire).
"""

import base64
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import flet as ft

from ch341a.app import main as flet_main

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

# Stable per-user storage for the icon - outside the project/dist folder -
# so it survives that folder being moved, renamed, or deleted.
STABLE_DIR = Path.home() / ".local" / "share" / "ch341a-tool"

# Distinct GTK application-id patched into this app's private copy of the
# shared Flet runtime, so it and other Flet apps in this workspace (CamWGF,
# ssh-wg-wire) don't collide as "the same" single-instance app over D-Bus.
FLET_APP_ID = "org.ch341atool"

# The name ensure_private_flet_runtime() gives its private runtime copy's
# executable. GTK derives the window's actual WM_CLASS/app-id - what GNOME
# uses to tell this app's window apart from other Flet apps' in the
# dock/taskbar - from that executable's own filename, not from FLET_APP_ID
# above, so this is what StartupWMClass in the .desktop entry must match.
FLET_EXE_NAME = "ch341a-tool"

# Icon bytes embedded directly in this file (base64) so the app never
# depends on icon.png existing as a separate file alongside it - everything
# needed to set up its own icon/shortcut ships in one file.
_ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAEN0lEQVR42u3dMWsUQRiA4dlh/oyNIASsrIRUaQSbaCFBrC5BywTTRsE2PyB/IGXagJ2V"
    "2NiIhWAtYh/h7C10cze3tzPf89Sit5t8736zQR129xepIcsE8ze08kGLQYfJvn8HATD0CMNsYlAMPcSNQTH4MJsYDL0HwODDjEJQDD7EDUEx+BA3BNnw"
    "QzMhaCYAhh8amKli8CHukSAbfoi7DWTDD3EjkA0/xI1ANvwQNwLZ8EPcCGTDD3EjkA0/xI1ANvwQNwLZ8EPcCGT3CuIaGwBPf+hwC8iGH+JGIBt+iBsB"
    "7wDAOwBPf4i4BdgAwAbg6Q8RtwAbANgAPP0h4hZgAwAbgKc/RNwCbABgAwCiB8D6D8GOATYAsAEAkQNg/YeAxwAbANgAAAEABAAQACBAAPwEAGJa2gDA"
    "EQAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEABAAQAEAAAAEAAXALQAAA"
    "AQAEABAAQACATpWIF312vEg7d+90fY1PD0/Tj5+//vvrHu89TC+ePDIJI3z99j0dvn5nAwAEABAAQAAAAQAEABAAQAAAAQAEABAAQAAAAQAEABAAYArF"
    "Lajj7flFev/hY3Of+/LqOl1eXU/yZz24fy+dvnxe9ff89PlLOn5z7hvQBgAIACAAgAAAAgAIACAAgACAALgFIACAAAACAAgAIABAp/x14EpOjg7SydHB"
    "Vj/Dzc3vtPfslS8GNgBAAAABAAQAEABAAEAA3AIQAEAAAAEABAAQAEAAAAEABABon38PoJJW/3twbACAAAACAAgAIACAAAACAAgAIACAAAACAAgAIACA"
    "AAACAAgAIACAAAACAAgAIACAAAACAAgAIACAAAACAAgAIACAAAACAAgAIACAAAB/G3b3F0u3AWwAgAAAAgAIACAAgAAAAgAIACAAgAAAAgAIACAAgAAA"
    "AgAIACAAgAAAAgAIACAAgAAAAgAIACAAgAAAAgAIACAAgAAAAgAIACAAgAAAAgAIAAgAIACAAAACAAgAIACAAACdBWBwGyCkwQYAjgCAAAACAAgAECQA"
    "fhIAsQw2ALABAALgGACh1n8bANgAAAFwDIBQ678NAGwA/y4E0OfT3wYANoBxpQD6evrbAMAGcLtiAH08/W0AYANYrRxA20//sRuACECHw3+bI4AIQGfD"
    "7x0AeAdQtyhAG0//VTYAEYBOhn/VI4AIQAfDv847ABGAxod/nQCIADQ+/OsGQASg4eGvEQARgEaHv1YARAAaHP6aARABaGz4U0qpbOiDLX2NYL6Dv4kN"
    "wDYADQ3/JgMgAtDALJWJPrgjAczwIVomvhAhgBltz2VLFyYEMINjc9nyhQoBbPF9WZnRhYsBhj5YAMQAQy8Ao2+UMGDQK/oDf5JtKT6to4sAAAAASUVO"
    "RK5CYII="
)


def _stable_icon_path(filename: str, b64_data: str) -> Path:
    """Writes the given embedded icon to a stable per-user path (outside the
    project/dist folder), named after a hash of its own bytes. GNOME Shell
    caches an app icon per file *path* for the session, so silently
    overwriting the same filename after changing the icon's design leaves
    the stale cached image showing - hashing the name means a changed icon
    is a genuinely new path the shell has never cached."""
    data = base64.b64decode(b64_data)
    digest = hashlib.sha1(data).hexdigest()[:10]
    stem, suffix = os.path.splitext(filename)
    path = STABLE_DIR / f"{stem}-{digest}{suffix}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def ensure_linux_shortcut() -> None:
    """On every run of a packaged Linux build, (re)install the app icon and
    .desktop launcher entry. The icon's stable copy lives outside the
    project/dist folder (~/.local/share/ch341a-tool) so it survives that
    folder being moved, renamed, or deleted; Exec points at the actual
    running executable, so the shortcut keeps working wherever it lives.
    Always rewritten (not just-once) so a changed StartupWMClass/icon path
    actually reaches the .desktop file. Best-effort: any failure here must
    never block the app from starting."""
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return
    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_file = desktop_dir / "ch341a-tool.desktop"
    try:
        stable_icon = _stable_icon_path("icon.png", _ICON_PNG_B64)
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=CH341A Tool\n"
            "Comment=Чтение/запись SPI flash через программатор CH341A\n"
            f"Exec={sys.executable}\n"
            f"Path={APP_DIR}\n"
            f"Icon={stable_icon}\n"
            "Terminal=false\n"
            "Categories=Development;Electronics;\n"
            # Matches the executable name ensure_private_flet_runtime() gives
            # this app's private runtime copy - GTK derives the running
            # window's WM_CLASS/app-id from that filename, not from any
            # string baked into the binary. Without a matching entry here,
            # GNOME can't map the running window to this launcher and falls
            # back to a generic icon.
            f"StartupWMClass={FLET_EXE_NAME}\n",
            encoding="utf-8",
        )
        desktop_file.chmod(0o755)

        subprocess.run(
            ["update-desktop-database", str(desktop_dir)],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def ensure_private_flet_runtime(app_id: str, exe_name: str, private_dir: Path) -> None:
    """Prepares a private, renamed copy of the shared Flet Linux desktop
    runtime and chdirs into it so flet_desktop picks it up.

    Upstream ships one runtime binary for every Flet app - named "flet" no
    matter which app runs it - and GTK derives a window's WM_CLASS/app-id
    from that executable's own name, not from any string baked into the
    binary. So GNOME can't tell one Flet app's window apart from another's,
    and shows a generic icon for at least one of them. flet_desktop's own
    launcher looks for an executable under "<cwd>/build/linux/" before
    falling back to the shared cache - the same mechanism `flet build linux`
    output uses - so dropping a distinctly named copy there and chdir-ing
    into `private_dir` gets each app its own WM_CLASS without needing the
    Flutter SDK. The copy's compiled-in GApplication id is also patched from
    the upstream default ("com.appveyor.flet") to `app_id`, so two different
    Flet apps running at once don't collide as "the same" single-instance
    application over D-Bus. Best-effort - skipped once already prepared, and
    must never block startup.
    """
    if not sys.platform.startswith("linux"):
        return
    build_dir = private_dir / "build" / "linux"
    if (build_dir / exe_name).exists():
        os.chdir(private_dir)
        return
    tmp_dir = private_dir.parent / f"{private_dir.name}.tmp"
    try:
        import flet_desktop

        source = flet_desktop.ensure_client_cached() / "flet"
        needle = b"com.appveyor.flet"
        new_id = app_id.encode("ascii")
        binary_data = (source / "flet").read_bytes()
        if len(new_id) > len(needle) or binary_data.count(needle) != 1:
            return
        patched = binary_data.replace(needle, new_id.ljust(len(needle), b"\x00"))

        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_build_dir = tmp_dir / "build" / "linux"
        tmp_build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, tmp_build_dir, dirs_exist_ok=True)
        old_binary = tmp_build_dir / "flet"
        new_binary = tmp_build_dir / exe_name
        old_binary.write_bytes(patched)
        old_binary.rename(new_binary)
        new_binary.chmod(0o755)

        private_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(private_dir, ignore_errors=True)
        tmp_dir.rename(private_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    os.chdir(private_dir)


if __name__ == "__main__":
    ensure_private_flet_runtime(FLET_APP_ID, FLET_EXE_NAME, STABLE_DIR / "flet-view")
    ensure_linux_shortcut()
    ft.run(flet_main)
