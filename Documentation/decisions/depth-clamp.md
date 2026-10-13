# Depth-clamp rendering policy

The adopted DEPTH_CLAMP policy uses the project's SDL3 patch on backends and
devices that support depth clip and clamp. The state check requires each mapped
tuple to set the depth-clamp property and match the reference renderer's state.

Devices without the capability require shader emulation. The state check reports
that fallback as owed until it is implemented and measured. Rendering differences
on those devices require recorded-stream measurements before classification.

The patch lives under `patches/sdl3/` and is marked as modified upstream source.
The machine-readable policy is `depth-clamp-policy.json` in this directory.
