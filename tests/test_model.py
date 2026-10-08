import pytest

from builders import BOARD, cells, qemu_fixture, s, tree
from devicetree_mcp.model import DTError, describe_irq, interrupt_map, pci_unit, strings


@pytest.fixture(scope="module")
def board():
    return tree(BOARD)


def test_lookup_by_path_label_alias_phandle(board):
    uart = board.node("/uart@9000000")
    assert board.node("&uart0") is uart
    assert board.node("uart0") is uart
    assert board.node("serial0") is uart
    assert board.node("phandle:0x3") is uart
    assert board.node("/bus@40000000/timer") .name == "timer@2000"   # unit address optional


def test_lookup_errors_say_what_exists(board):
    with pytest.raises(DTError, match="Children: memory@80000000"):
        board.node("/nope")
    with pytest.raises(DTError, match="Known: gic, uart0"):
        board.node("&missing")


def test_reg_uses_parent_cells_and_defaults():
    t = tree({"children": {"dev@1": {"props": {"reg": cells(0, 1, 0x10)}}}})
    # Root has no #address-cells/#size-cells: spec defaults are 2 and 1.
    assert t.reg(t.node("/dev@1")) == [(1, 0x10)]


def test_reg_size_mismatch_is_explained():
    t = tree({"props": {"#address-cells": cells(2), "#size-cells": cells(2)},
              "children": {"dev@1": {"props": {"reg": cells(1, 2, 3)}}}})
    with pytest.raises(DTError, match=r"3 cells, not a multiple of #address-cells\(2\) \+ #size-cells\(2\)"):
        t.reg(t.node("/dev@1"))


def test_ranges_translation_and_missing_ranges(board):
    [r] = board.regions(board.node("/bus@40000000/timer@2000"))
    assert (r.child_addr, r.cpu_addr) == (0x2000, 0x40002000)
    assert "/bus@40000000" in r.note
    [iso] = board.regions(board.node("/isolated/thing@10"))
    assert iso.cpu_addr is None and "no `ranges`" in iso.note


def test_empty_ranges_is_identity():
    t = tree({"props": {"#address-cells": cells(1), "#size-cells": cells(1)},
              "children": {"soc": {"props": {"#address-cells": cells(1), "#size-cells": cells(1),
                                             "ranges": b""},
                                   "children": {"x@100": {"props": {"reg": cells(0x100, 4)}}}}}})
    assert t.regions(t.node("/soc/x@100"))[0].cpu_addr == 0x100


def test_interrupt_parent_is_inherited_and_gic_decoded(board):
    [irq] = board.interrupts(board.node("/bus@40000000/timer@2000"))
    assert irq.controller == "/intc@8000000"
    assert irq.meaning == "SPI 5 (INTID 37) edge-rising"


def test_gic_ppi_cpu_mask():
    gic = tree(BOARD).node("/intc@8000000")
    assert describe_irq(gic, [1, 14, 0x304]) == "PPI 14 (INTID 30) level-high, CPU mask 0x03"


def test_interrupts_extended_and_riscv():
    r = qemu_fixture("riscv64")
    irqs = r.interrupts(r.node("/soc/clint@2000000"))
    assert [i.meaning for i in irqs[:2]] == ["hart-local cause 3 (machine software)",
                                             "hart-local cause 7 (machine timer)"]
    assert r.interrupts(r.node("/soc/serial@10000000"))[0].meaning == "PLIC source 10"


def test_pci_interrupt_map_swizzle_matches_qemu_virt():
    a = qemu_fixture("aarch64")
    mask, entries = interrupt_map(a, a.node("/pcie@10000000"))
    assert mask == [0x1800, 0, 0, 7]
    assert len(entries) == 16
    # hw/arm/virt.c: irq = (pin + slot) % 4 + 3 SPIs.
    for e in entries:
        slot = (e.child_unit[0] >> 11) & 0x1F
        pin = e.child_spec[0] - 1
        assert e.parent_spec[1] == 3 + (pin + slot) % 4, e
    assert pci_unit([0x800]) == "00:01.0"


def test_strings_detection():
    assert strings(s("a", "b")) == ["a", "b"]
    assert strings(cells(0x41424300)) == ["ABC"]          # the bytes "ABC" + NUL
    assert strings(cells(1)) is None                       # 00 00 00 01: not a string
    assert strings(b"\x01\x02\x00") is None
    assert strings(b"") is None
