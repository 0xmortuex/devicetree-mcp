"""The MCP tools end to end on real QEMU DTBs and the synthetic board."""

import gzip
import os

import pytest

from builders import BOARD, FIXTURES, make
from devicetree_mcp import loader
from devicetree_mcp import server as S
from devicetree_mcp.fdt import build
from devicetree_mcp.model import DTError


@pytest.fixture(autouse=True)
def fresh():
    S._trees.clear()
    yield
    S._trees.clear()


@pytest.fixture
def aarch64_path(tmp_path):
    p = tmp_path / "virt.dtb"
    with gzip.open(os.path.join(FIXTURES, "qemu-virt-aarch64.dtb.gz"), "rb") as fh:
        p.write_bytes(fh.read())
    return str(p)


RISCV = os.path.join(FIXTURES, "qemu-virt-riscv64.dtb")


def test_load_summary(aarch64_path):
    out = S.dt_load("a", path=aarch64_path)
    assert "59 nodes" in out and "2 CPU(s)" in out
    assert "memory: 0x40000000 (256 MiB)" in out
    assert "dt_validate: 2 error(s), 1 warning(s)" in out


def test_tree_collapses_identical_siblings(aarch64_path):
    S.dt_load("a", path=aarch64_path)
    out = S.dt_tree("a", depth=1)
    assert "virtio_mmio@a000000 .. virtio_mmio@a003e00: 32 x virtio-mmio" in out
    assert out.count("virtio_mmio@") == 2
    assert "pl011@9000000: PL011 UART, at 0x9000000 (4 KiB), IRQ SPI 1 (INTID 33) level-high on /intc@8000000" in out


def test_node_decodes_everything(aarch64_path):
    S.dt_load("a", path=aarch64_path)
    out = S.dt_node("a", "serial0")      # alias
    assert "path: /pl011@9000000" in out
    assert "clocks = /apb-pclk (24 MHz); /apb-pclk (24 MHz)" in out
    assert "interrupt-parent (inherited): /intc@8000000" in out
    assert "reg = 0x9000000 size 0x1000 (4 KiB)" in out
    gic = S.dt_node("a", "/intc@8000000")
    refs = gic.split("referenced by: ")[1]
    assert "/ (interrupt-parent)" in refs and "/pcie@10000000 (interrupt-map)" in refs


def test_memory_map_translates_through_soc():
    S.dt_load("r", path=RISCV)
    out = S.dt_memory_map("r")
    lines = out.splitlines()
    assert any("0x000010000000" in ln and "/soc/serial@10000000" in ln for ln in lines)
    addrs = [int(ln.split("-")[0], 16) for ln in lines[1:] if ln.strip().startswith("0x")]
    assert addrs == sorted(addrs)
    assert "OVERLAPS" not in out


def test_interrupt_map_shows_pci_swizzle_and_feeds(aarch64_path):
    S.dt_load("a", path=aarch64_path)
    out = S.dt_interrupt_map("a")
    assert "dev 00:01.0 INTA       -> SPI 4 (INTID 36) level-high on /intc@8000000" in out
    S.dt_load("r", path=RISCV)
    rv = S.dt_interrupt_map("r")
    assert "-> feeds /cpus/cpu@0/interrupt-controller, /cpus/cpu@1/interrupt-controller" in rv
    plic = rv.split("\n/soc/plic@c000000 (")[1]
    # numeric order, not "1, 10, 11, 2"
    assert plic.index("PLIC source 2 ") < plic.index("PLIC source 10")


def test_find():
    S.dt_load("r", path=RISCV)
    assert "/soc/serial@10000000" in S.dt_find("r", compatible="16550")
    out = S.dt_find("r", property="interrupt-controller")
    assert "/soc/plic@c000000" in out
    with pytest.raises(ValueError, match="at least one filter"):
        S.dt_find("r")


def test_diff(tmp_path):
    p1, p2 = tmp_path / "a.dtb", tmp_path / "b.dtb"
    p1.write_bytes(build(make(BOARD)))
    import copy
    spec = copy.deepcopy(BOARD)
    spec["children"]["uart@9000000"]["props"]["status"] = b"disabled\0"
    spec["children"]["intc@8000000"]["props"]["phandle"] = b"\0\0\0\x01"
    del spec["children"]["clk24"]
    p2.write_bytes(build(make(spec)))
    S.dt_load("a", path=str(p1))
    S.dt_load("b", path=str(p2))
    out = S.dt_diff("a", "b")
    assert "- /clk24" in out
    assert '~ /uart@9000000: + status = "disabled"' in out


def test_validate_tool(aarch64_path):
    S.dt_load("a", path=aarch64_path)
    out = S.dt_validate("a")
    assert out.startswith("2 error(s), 1 warning(s):")


def test_unknown_tree_is_instructive():
    with pytest.raises(DTError, match="call dt_load first"):
        S.dt_tree("nope")


def test_load_needs_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        S.dt_load("x")


def test_dts_without_dtc_says_how_to_install(tmp_path, monkeypatch):
    p = tmp_path / "board.dts"
    p.write_text("/dts-v1/;\n/ { };\n", encoding="utf-8")
    monkeypatch.setattr(loader, "find_dtc", lambda: None)
    with pytest.raises(DTError, match="apt install device-tree-compiler"):
        S.dt_load("x", path=str(p))
