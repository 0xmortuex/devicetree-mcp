# Backlog

Real, finishable improvements - pick ONE, ship it end-to-end with tests.

## Features
- [ ] Binding validation: run `dt-validate` (dt-schema) when installed and fold its findings
      into dt_validate, each with the binding file path.
- [ ] Source locations: dtc `-O dts -A` (annotate) output maps nodes/properties back to
      .dts/.dtsi file:line; show them in dt_node and dt_validate.
- [ ] Overlays: apply a `.dtbo` (`__fixups__`/`__local_fixups__`) onto a base tree and diff.
- [ ] More interrupt controllers: ARM GICv3 ITS MSI maps (`msi-map`), RISC-V APLIC/IMSIC,
      Broadcom/TI legacy controllers.
- [ ] `dt_explain_driver(compatible)`: which Linux/Zephyr driver binds it (needs an index).
- [ ] Load from a running Linux: `/sys/firmware/fdt` or `/proc/device-tree` (via a path).

## Quality
- [ ] Fuzz the parser with mutated DTBs (truncation, offsets past the end, huge lengths).
- [ ] Verify against U-Boot and Raspberry Pi DTBs (non-QEMU, overlays, aliases-heavy).
