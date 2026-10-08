import copy

from builders import BOARD, cells, qemu_fixture, s, tree
from devicetree_mcp.validate import check


def _msgs(t):
    return [str(f) for f in check(t)]


def _board(**patch):
    spec = copy.deepcopy(BOARD)
    for path, props in patch.items():
        node = spec
        for part in [p for p in path.split("/") if p]:
            node = node["children"][part]
        node.setdefault("props", {}).update(props)
    return tree(spec)


def test_clean_board_and_real_riscv_have_no_findings():
    assert _msgs(tree(BOARD)) == []
    assert _msgs(qemu_fixture("riscv64")) == []


def test_real_qemu_aarch64_findings():
    msgs = _msgs(qemu_fixture("aarch64"))
    # QEMU 11's virt emits next-level-cache = <0>; phandle 0 is reserved.
    assert any("cpu@0: next-level-cache = <0x0>" in m and "reserved" in m for m in msgs)
    assert any(m.startswith("warning: /pcie@10000000: unit address @10000000") for m in msgs)


def test_unit_address_mismatch():
    spec = copy.deepcopy(BOARD)
    spec["children"]["uart@9000000"]["props"]["reg"] = cells(0x9001000, 0x1000)
    [m] = [x for x in _msgs(tree(spec)) if "uart@9000000" in x]
    assert "rename the node to uart@9001000" in m


def test_dangling_phandle_and_missing_clock_cells():
    t = _board(**{"/uart@9000000": {"clocks": cells(0x99)}})
    assert any("clocks[0] points at phandle 0x99" in m for m in _msgs(t))
    spec = copy.deepcopy(BOARD)
    del spec["children"]["clk24"]["props"]["#clock-cells"]
    assert any("has no #clock-cells" in m for m in _msgs(tree(spec)))


def test_interrupt_cell_count():
    t = _board(**{"/uart@9000000": {"interrupts": cells(0, 1)}})
    assert any("interrupts has 2 cells, not a multiple of /intc@8000000's #interrupt-cells = 3" in m
               for m in _msgs(t))


def test_no_interrupt_parent():
    spec = copy.deepcopy(BOARD)
    del spec["props"]["interrupt-parent"]
    assert any("no interrupt-parent on it or any ancestor" in m for m in _msgs(tree(spec)))


def test_overlap_is_detected_against_a_wide_region():
    spec = copy.deepcopy(BOARD)
    spec["children"]["big@9000000"] = {"props": {"reg": cells(0x9000000, 0x100000)}}
    spec["children"]["late@9080000"] = {"props": {"reg": cells(0x9080000, 0x10)}}
    msgs = _msgs(tree(spec))
    assert any("late@9080000" in m and "overlaps /big@9000000" in m for m in msgs), msgs


def test_duplicate_unit_address_and_phandle():
    spec = copy.deepcopy(BOARD)
    spec["children"]["serial@9000000"] = {"props": {"reg": cells(0x9000000, 0x10), "phandle": cells(3)}}
    msgs = _msgs(tree(spec))
    assert any("same unit address as /uart@9000000" in m for m in msgs)
    assert any("phandle 0x3 is also used by" in m for m in msgs)


def test_reg_without_unit_address():
    spec = copy.deepcopy(BOARD)
    spec["children"]["blob"] = {"props": {"reg": cells(0x1000, 4), "compatible": s("x")}}
    assert any("has reg but no unit address" in m for m in _msgs(tree(spec)))
