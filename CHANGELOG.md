# Changelog

## 0.1.0 - 2026-10-08

First release.

- Pure-Python DTB parser and encoder (Devicetree Specification v0.4 §5): header checks,
  memory reservation map, structure/strings blocks, NOP tokens, clear errors (a `.dts`
  passed as a DTB is told to compile it).
- `.dts` via `dtc -@` when installed (labels kept); explicit install instructions otherwise.
- QEMU-generated trees via `-machine dumpdtb=` (`qemu_arch`, `qemu_machine`, `qemu_args`).
- Tools: dt_load, dt_tree, dt_node, dt_find, dt_memory_map, dt_interrupt_map,
  dt_validate, dt_diff, dt_trees.
- Decoding: reg through every `ranges` to CPU addresses; interrupt-parent inheritance,
  interrupts-extended; GICv2/v3 SPI/PPI with INTID and CPU masks; PLIC; RISC-V hart-local
  causes; interrupt-map with PCI bus:dev.fn and INTA-D; phandle lists with #*-cells;
  clock frequencies; labels, aliases, and referrers.
- Validation: cell-count mismatches, unit address vs reg, dangling and reserved phandles,
  unroutable interrupts, duplicate unit addresses/phandles, overlapping regions.
