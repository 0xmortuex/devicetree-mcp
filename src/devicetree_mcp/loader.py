"""Load a tree from a .dtb, a .dts (compiled with dtc), or a QEMU machine."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from .fdt import parse
from .model import DTError, Tree

DTC_INSTALL = (
    "install the device-tree compiler: `apt install device-tree-compiler` (Debian/Ubuntu), "
    "`brew install dtc` (macOS), `pacman -S dtc` (MSYS2/Arch). Or pass a compiled .dtb instead."
)


def find_dtc() -> str | None:
    return shutil.which("dtc")


def compile_dts(path: str, include_dirs: list[str] | None = None) -> bytes:
    dtc = find_dtc()
    if dtc is None:
        raise DTError(f"{os.path.basename(path)} is devicetree source; compiling it needs dtc - {DTC_INSTALL}")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.dtb")
        # -@ keeps labels in /__symbols__, so &label lookups and labels in
        # explanations work for trees loaded from source.
        cmd = [dtc, "-@", "-I", "dts", "-O", "dtb", "-o", out]
        for d in include_dirs or []:
            cmd += ["-i", d]
        cmd.append(path)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise DTError(f"dtc failed to compile {path}:\n{proc.stderr.strip()}\n"
                          "(if it uses #include, preprocess it with cpp first, as the kernel build does)")
        with open(out, "rb") as fh:
            return fh.read()


def load_file(path: str, include_dirs: list[str] | None = None) -> Tree:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head == b"\xd0\x0d\xfe\xed":
        with open(path, "rb") as fh:
            return Tree(parse(fh.read()), os.path.abspath(path))
    return Tree(parse(compile_dts(path, include_dirs)), os.path.abspath(path))


def find_qemu(arch: str) -> str:
    exe = f"qemu-system-{arch}"
    found = shutil.which(exe)
    if found:
        return found
    if sys.platform == "win32":
        win = os.path.join(r"C:\Program Files\qemu", exe + ".exe")
        if os.path.isfile(win):
            return win
    raise DTError(f"{exe} not found - install QEMU or pass a .dtb path instead")


def load_qemu(arch: str, machine: str = "virt", extra_args: list[str] | None = None) -> Tree:
    """Ask QEMU for the DTB it would hand the guest (`-machine dumpdtb=`)."""
    qemu = find_qemu(arch)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "machine.dtb")
        cmd = [qemu, "-M", f"{machine},dumpdtb={out}", "-display", "none", *(extra_args or [])]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if not os.path.isfile(out):
            raise DTError(f"QEMU did not write a DTB (exit {proc.returncode}): "
                          f"{proc.stderr.strip()[-500:]}. Only device-tree machines "
                          f"(e.g. aarch64/riscv64 -M virt) can dump one.")
        with open(out, "rb") as fh:
            data = fh.read()
    label = f"qemu-system-{arch} -M {machine}" + (" " + " ".join(extra_args) if extra_args else "")
    return Tree(parse(data), label)
