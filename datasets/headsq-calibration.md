# headsq.vti — empirical Hounsfield calibration

`paraview-examples/headsq.vti` ships as 12-bit unsigned data with no embedded
rescale slope or intercept. To use it with HU-keyed presets (Slicer, OsiriX,
clinical windowing) the raw values need to be shifted and scaled into
Hounsfield units. The dataset's metadata doesn't tell us what shift/scale
the original authors used, so this is empirical.

## Calibration

```python
hu = filter("vtkImageShiftScale", input=data,
            Shift=-1055.5, Scale=1.0520, OutputScalarType=4)
# vtkImageShiftScale applies output = (input + Shift) * Scale
```

Anchored on two histogram modes:

| Anchor | Raw value | Target HU |
|--------|-----------|-----------|
| In-body air mode | 105 | -1000 |
| Soft-tissue mode | 1084 | 30 (gray/white-matter average) |

Solving the two-anchor system:
- `Scale = 1030 / (1084 - 105) = 1.0520`
- `Shift = -1000/Scale - 105 ≈ -1055.5`

The Scale ≠ 1 because the dataset's air-to-tissue raw span (979) is ~5%
narrower than the canonical 1000 raw units / 1000 HU of a real CT. A
single-shift calibration with `Scale=1.0` cannot put both air and tissue
at canonical HU values; the scale correction is what makes both anchors
land correctly.

## Why "tissue mode = brain at HU 30" rather than "tissue mode = water at HU 0"

The soft-tissue peak (raw [1059, 1109)) accounts for ~13% of voxels — about
800k voxels. That's close to the expected brain volume in a head CT
(~700k voxels at 2 mm³/voxel). So the dominant tissue is brain parenchyma,
not water. Brain gray + white matter average is HU ~30, and that's the
anchor used here.

If you anchor on water (HU=0) instead, brain ends up at HU=0 too, which
makes brain-window views look wrong — but the air anchor and the bone
range will still come out reasonable.

## Independent verification

Anchoring is one thing; whether the Scale is right is another. Tissues
that *weren't* used in the calibration should land at canonical HU values
if the calibration is correct:

| Tissue | Canonical HU | Observed in calibrated data |
|--------|-------------|-----------------------------|
| Subcutaneous fat | -100 to -50 | shoulder at -119 to -33 (5.5% of voxels) |
| Vitreous humor (eye) | 5 to 15 | 5-voxel cluster at (93–95, 173–177, 36), mean **8.6** |
| Sphenoid orbital wall | 400 to 1500 | (97, 173, 38) → **404** |
| Cortical bone tail | extends to ~1900 | bone density drops sharply at ~1700-1900 |
| Dental enamel | 2500 to 3000 | tail reaches ~3197 |

Vitreous humor is the strongest check because its HU is biologically tight
(it's essentially pure water in a sealed compartment). Landing at 9 HU
within the 5-15 canonical range constrains the Scale to within ~1%.

## Caveats

This is a Claude-derived calibration, not an authoritative spec from the
dataset's authors. It seemed about right after cross-checking against
several anatomical landmarks (above), but:

- The "brain mode = HU 30" assumption is a defensible choice; HU 25 or 35
  are also defensible and would shift Scale by ~1.5%.
- The Scale=1.0520 correction is unusual — most CTs have Scale=1.0. If
  there's an authoritative rescale in the original DICOM that ParaView
  dropped, those values would be more correct than these.
- The mode-finding used 410-bin histograms; sub-bin precision could
  shift Shift by a few HU in either direction.

For most uses (Slicer presets, clinical windowing, isosurface thresholds
in HU) this calibration is close enough. For research-grade quantitative
work, treat it as approximate.
