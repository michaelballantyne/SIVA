# Medical CT/MR volume-rendering presets

3D Slicer's curated transfer-function presets for clinical CT and MR
visualization. These are the same presets used in OsiriX, Horos, and most
hospital workstations.

## What's here

After running `./download.sh`, `data/` will contain:

- `data/presets.xml` -- ~25 preset definitions in MRML format. Each preset is
  a `<VolumeProperty …>` element with:
  - `scalarOpacity="<HU> <opacity> ..."` -- piecewise-linear opacity ramp
  - `gradientOpacity="..."` -- edge-enhanced opacity
  - `colorTransfer="<HU> <r> <g> <b> ..."` -- piecewise color map
  - `shade`, `ambient`, `diffuse`, `specular`, ... -- lighting params
- `data/icons/CT-*.png`, `MR-*.png` -- 128x128 thumbnail of each preset
  applied to a reference dataset; useful for picking a starting preset.
- `data/Slicer-LICENSE.txt` -- the upstream Slicer BSD-style license.

## Preset list

CT presets cover anatomical regions and contrast scenarios:

  CT-AAA, CT-AAA2 (abdominal aortic aneurysm w/ contrast)
  CT-Bone, CT-Bones, CT-Cropped-Volume-Bone
  CT-Cardiac, CT-Cardiac2, CT-Cardiac3
  CT-Chest-Contrast-Enhanced, CT-Chest-Vessels
  CT-Coronary-Arteries, CT-Coronary-Arteries-2, CT-Coronary-Arteries-3
  CT-Pulmonary-Arteries, CT-Lung, CT-Air
  CT-Soft-Tissue, CT-Fat, CT-Liver-Vasculature, CT-MIP

MR presets cover common pulse sequences:

  MR-Default, MR-Angio, MR-T1, MR-T1-Brain, MR-T2-Brain, MR-MIP

## How to use these in VisLang

The MRML XML maps cleanly to `show()` parameters. Until an importer
exists, copy values by hand -- most presets are only ~5 control points
each.

### Conversion recipe

Each XML attribute starts with a count (number of floats), then the
control-point data. Strip the count, then chunk:

| MRML attribute | VisLang `show()` kwarg | Chunking |
|----------------|------------------------|----------|
| `scalarOpacity="N v0 o0 v1 o1 ..."` | `opacity_function=[(v0,o0), ...]` | 2 floats per point |
| `colorTransfer="N v0 r0 g0 b0 ..."` | `color_function=[(v0,r0,g0,b0), ...]` | 4 floats per point |
| `effectiveRange="lo hi"` | `scalar_range=(lo, hi)` | as-is |
| `shade="0|1"` | `shade=False|True` | bool |
| `specular`, `specularPower` | `specular=`, `specular_power=` | as-is |

Add `representation="Volume"` and `color_by="<scalar_field>"` (the latter
is required for the scalar bar to render).

### Required prerequisite: HU calibration

These presets are keyed on Hounsfield units. The data must be in HU before
they apply. Real DICOM is already in HU; raw VTK demo files often aren't.
For the headsq.vti example, see `datasets/headsq-calibration.md` for the
empirical Shift/Scale and the methodology to derive one for similar
quirky files.

### Notes

- **`gradientOpacity`** isn't directly exposed; pass
  `gradient_opacity=True` for a default edge ramp, or a list of
  `(gradient, opacity)` pairs for a custom curve.
- **Constant-color presets** (e.g. CT-MIP renders constant white) work
  with a 2-point `color_function` repeating the same RGB.

## Source

  https://github.com/Slicer/Slicer/tree/main/Modules/Loadable/VolumeRendering/Resources
