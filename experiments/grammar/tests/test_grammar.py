"""Tests for the 3D visualization grammar.

Tests the spec layer (pure data, no VTK) and the compiler (VTK integration).
Compiler tests require xvfb-run in headless environments.
"""

import os
import sys
import tempfile
import pytest

# Add the grammar package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vislang_grammar import (
    data, show, layer, encode, scale_color, scale_opacity, near,
    rep_volume, rep_isosurface, rep_streamlines, rep_glyphs,
    rep_surface, rep_outline,
    where, derive, gradient, slice_grid, clip, subsample,
    DataRef, TransformChain, Transform, RepSpec, Encoding,
    ScaleColor, ScaleOpacity, ShowResult, LayerSpec,
)


# =========================================================================
# Spec layer tests — pure data, no VTK
# =========================================================================


class TestDataRef:
    def test_create(self):
        d = data("bonsai.vti")
        assert isinstance(d, DataRef)
        assert d.filename == "bonsai.vti"

    def test_pipe_to_transform(self):
        d = data("file.vts")
        result = d | where("theta", between=[500, 2000])
        assert isinstance(result, TransformChain)
        assert result.source is d
        assert len(result.transforms) == 1
        assert result.transforms[0].kind == "where"

    def test_pipe_to_rep(self):
        d = data("file.vts")
        result = d | rep_surface()
        assert isinstance(result, RepSpec)
        assert result.source is d
        assert result.kind == "surface"

    def test_pipe_type_error(self):
        d = data("file.vts")
        with pytest.raises(TypeError, match="Cannot pipe"):
            d | "not_a_transform"


class TestTransformChain:
    def test_chain_transforms(self):
        d = data("file.vts")
        chain = d | where("theta", between=[500, 2000]) | subsample(every_nth=10)
        assert isinstance(chain, TransformChain)
        assert len(chain.transforms) == 2
        assert chain.transforms[0].kind == "where"
        assert chain.transforms[1].kind == "subsample"

    def test_chain_to_rep(self):
        d = data("file.vts")
        rep = d | where("theta", between=[500, 2000]) | rep_isosurface("theta", at=800)
        assert isinstance(rep, RepSpec)
        assert rep.kind == "isosurface"
        assert rep.source.filename == "file.vts"
        assert len(rep.transforms) == 1

    def test_long_chain(self):
        d = data("file.vts")
        rep = (d
               | where("theta", between=[500, 2000])
               | subsample(every_nth=5)
               | rep_surface())
        assert len(rep.transforms) == 2


class TestTransforms:
    def test_where(self):
        t = where("temperature", between=[300, 800])
        assert t.kind == "where"
        assert t.params["field"] == "temperature"
        assert t.params["lo"] == 300
        assert t.params["hi"] == 800

    def test_derive(self):
        t = derive("velocity", from_components=["u", "v", "w"])
        assert t.kind == "derive"
        assert t.params["components"] == ["u", "v", "w"]

    def test_derive_wrong_count(self):
        with pytest.raises(ValueError, match="exactly 3"):
            derive("vel", from_components=["u", "v"])

    def test_gradient(self):
        t = gradient("density")
        assert t.kind == "gradient"
        assert t.params["field"] == "density"

    def test_slice_grid(self):
        t = slice_grid(k=0)
        assert t.kind == "slice_grid"
        assert t.params["k"] == 0

    def test_slice_grid_needs_one(self):
        with pytest.raises(ValueError, match="exactly one"):
            slice_grid()
        with pytest.raises(ValueError, match="exactly one"):
            slice_grid(k=0, j=5)

    def test_clip(self):
        t = clip(normal=(0, 0, 1), origin=(0, 0, 50))
        assert t.params["normal"] == (0, 0, 1)

    def test_subsample(self):
        t = subsample(every_nth=20)
        assert t.params["every_nth"] == 20


class TestRepresentations:
    def test_rep_volume(self):
        r = rep_volume("density")
        assert r.kind == "volume"
        assert r.params["field"] == "density"
        assert r.source is None  # unbound

    def test_rep_isosurface_single(self):
        r = rep_isosurface("theta", at=800)
        assert r.params["at"] == [800]  # normalized to list

    def test_rep_isosurface_multi(self):
        r = rep_isosurface("theta", at=[500, 800, 1200])
        assert r.params["at"] == [500, 800, 1200]

    def test_rep_streamlines(self):
        r = rep_streamlines("velocity", seeds=near("theta", [500, 2000], n=40))
        assert r.kind == "streamlines"
        assert r.params["seeds"]["kind"] == "near"

    def test_rep_glyphs(self):
        r = rep_glyphs("velocity", shape="arrow", every_nth=10)
        assert r.params["shape"] == "arrow"
        assert r.params["every_nth"] == 10

    def test_rep_surface(self):
        r = rep_surface()
        assert r.kind == "surface"

    def test_rep_outline(self):
        r = rep_outline()
        assert r.kind == "outline"


class TestEncoding:
    def test_default(self):
        e = encode()
        assert e.color is None
        assert e.opacity is None
        assert e.shade is False

    def test_with_scale_color(self):
        sc = scale_color("density", range=[20, 200], colormap="terrain")
        e = encode(color=sc)
        assert isinstance(e.color, ScaleColor)
        assert e.color.field == "density"

    def test_with_rgb_color(self):
        e = encode(color=(1.0, 0.5, 0.0))
        assert e.color == (1.0, 0.5, 0.0)

    def test_with_scale_opacity(self):
        so = scale_opacity("density", control_points=[(0, 0), (100, 0.5), (200, 1.0)])
        e = encode(opacity=so)
        assert isinstance(e.opacity, ScaleOpacity)
        assert len(e.opacity.control_points) == 3

    def test_with_float_opacity(self):
        e = encode(opacity=0.5)
        assert e.opacity == 0.5

    def test_full_encoding(self):
        e = encode(
            color=scale_color("theta", [500, 2000], "hot"),
            opacity=scale_opacity("theta", [(500, 0), (2000, 0.8)]),
            shade=True,
            legend="Temperature (K)",
            specular=0.4,
        )
        assert e.shade is True
        assert e.legend == "Temperature (K)"
        assert e.specular == 0.4


class TestScales:
    def test_scale_color(self):
        sc = scale_color("density", range=[20, 200], colormap="terrain")
        assert sc.field == "density"
        assert sc.range == [20, 200]
        assert sc.colormap == "terrain"

    def test_scale_color_defaults(self):
        sc = scale_color("density")
        assert sc.range is None
        assert sc.colormap == "cool_to_warm"

    def test_scale_opacity_points(self):
        so = scale_opacity("density", control_points=[(0, 0), (200, 1.0)])
        assert so.control_points == [(0, 0), (200, 1.0)]
        assert so.preset is None

    def test_scale_opacity_preset(self):
        so = scale_opacity("density", preset="fire")
        assert so.preset == "fire"

    def test_scale_opacity_gradient(self):
        so = scale_opacity("density", [(0, 0), (255, 1)], gradient_modulation=True)
        assert so.gradient_modulation is True


class TestShow:
    def test_show_creates_result(self):
        d = data("file.vts")
        result = show(d | rep_surface(), encode(color=(1, 0, 0)))
        assert isinstance(result, ShowResult)
        assert result.rep.kind == "surface"
        assert result.encoding.color == (1, 0, 0)

    def test_show_default_encoding(self):
        d = data("file.vts")
        result = show(d | rep_surface())
        assert isinstance(result.encoding, Encoding)

    def test_show_type_error(self):
        d = data("file.vts")
        with pytest.raises(TypeError, match="RepSpec"):
            show(d)  # DataRef, not RepSpec — forgot the rep


class TestLayer:
    def test_layer_composes(self):
        d = data("file.vts")
        scene = layer(
            show(d | rep_surface(), encode(color=(1, 0, 0))),
            show(d | rep_outline(), encode(color=(1, 1, 1))),
        )
        assert isinstance(scene, LayerSpec)
        assert len(scene.shows) == 2

    def test_layer_type_error(self):
        with pytest.raises(TypeError, match="ShowResult"):
            layer("not a show result")


class TestNear:
    def test_near(self):
        s = near("theta", [500, 2000], n=40)
        assert s["kind"] == "near"
        assert s["field"] == "theta"
        assert s["n"] == 40


# =========================================================================
# End-to-end pipeline composition test — still no VTK
# =========================================================================


class TestFullPipelineSpec:
    """Test that a full grammar pipeline assembles correctly as a spec."""

    def test_bonsai_pipeline(self):
        """The bonsai CT example from the design document."""
        bonsai = data("bonsai.vti")
        density_color = scale_color("density", range=[20, 200], colormap="terrain")

        scene = layer(
            show(bonsai | rep_volume("density"),
                 encode(color=density_color,
                        opacity=scale_opacity("density",
                            [(0, 0.0), (50, 0.2), (200, 0.8)]),
                        shade=True,
                        legend="Density")),

            show(bonsai | rep_isosurface("density", at=80),
                 encode(color=(0.6, 0.4, 0.2), opacity=0.3)),

            show(bonsai | rep_outline(),
                 encode(color=(1, 1, 1), opacity=0.2)),
        )

        assert isinstance(scene, LayerSpec)
        assert len(scene.shows) == 3

        # Volume layer
        vol = scene.shows[0]
        assert vol.rep.kind == "volume"
        assert vol.rep.source.filename == "bonsai.vti"
        assert vol.encoding.shade is True
        assert vol.encoding.legend == "Density"

        # Isosurface layer
        iso = scene.shows[1]
        assert iso.rep.kind == "isosurface"
        assert iso.rep.params["at"] == [80]
        assert iso.encoding.color == (0.6, 0.4, 0.2)

        # Outline layer
        out = scene.shows[2]
        assert out.rep.kind == "outline"
        assert out.encoding.opacity == 0.2

    def test_fire_pipeline(self):
        """The fire simulation example from the design document."""
        fire = data("output.30000.vts")

        scene = layer(
            show(fire
                 | where("theta", between=[500, 2000])
                 | rep_volume("theta"),
                 encode(color=scale_color("theta", [500, 2000], "hot"),
                        opacity=scale_opacity("theta",
                            [(500, 0), (800, 0.05), (1200, 0.3), (2000, 0.6)]),
                        shade=True, legend="Theta (K)")),

            show(fire
                 | derive("velocity", from_components=["u", "v", "w"])
                 | rep_streamlines("velocity",
                                   seeds=near("theta", [500, 2000], n=40),
                                   tube_radius=1.5),
                 encode(color=scale_color("velocity", [0, 30], "wind"),
                        opacity=0.8, legend="Wind (m/s)")),

            show(fire
                 | slice_grid(k=0)
                 | rep_surface(),
                 encode(color=scale_color("theta", [290, 400], "terrain"))),
        )

        assert len(scene.shows) == 3
        # Volume: 1 transform (where)
        assert len(scene.shows[0].rep.transforms) == 1
        # Streamlines: 1 transform (derive)
        assert len(scene.shows[1].rep.transforms) == 1
        # Surface: 1 transform (slice_grid)
        assert len(scene.shows[2].rep.transforms) == 1


# =========================================================================
# Compiler tests — require VTK
# =========================================================================


def _create_test_imagedata(tmp_path):
    """Create a small synthetic vtkImageData and write to .vti file."""
    import vtk
    import numpy as np

    img = vtk.vtkImageData()
    img.SetDimensions(16, 16, 16)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)

    # Add a "density" field
    n = 16 * 16 * 16
    density = vtk.vtkFloatArray()
    density.SetName("density")
    density.SetNumberOfTuples(n)
    for i in range(n):
        z = i // (16 * 16)
        density.SetValue(i, float(z * 16))  # 0 to 240
    img.GetPointData().AddArray(density)
    img.GetPointData().SetActiveScalars("density")

    filepath = os.path.join(str(tmp_path), "test.vti")
    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(filepath)
    writer.SetInputData(img)
    writer.Write()
    return filepath


class TestCompiler:
    """Tests that require the VTK compiler. Run with xvfb-run."""

    @pytest.fixture
    def test_data(self, tmp_path):
        return _create_test_imagedata(tmp_path)

    def test_surface(self, test_data):
        from vislang_grammar import compile_scene
        d = data(test_data)
        result = show(d | rep_surface(), encode(color=(0.8, 0.7, 0.5)))
        compiled = compile_scene(result)
        assert len(compiled) == 1
        actor, bar = compiled[0]
        assert bar is None
        # Actor should be a vtkActor
        import vtk
        assert isinstance(actor, vtk.vtkActor)

    def test_outline(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        d = data(test_data)
        result = show(d | rep_outline(), encode(color=(1, 1, 1), opacity=0.3))
        compiled = compile_scene(result)
        actor, bar = compiled[0]
        assert isinstance(actor, vtk.vtkActor)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.3)

    def test_isosurface(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        d = data(test_data)
        result = show(
            d | rep_isosurface("density", at=120),
            encode(color=(1, 0, 0), opacity=0.5)
        )
        compiled = compile_scene(result)
        actor, bar = compiled[0]
        assert isinstance(actor, vtk.vtkActor)

    def test_layer_compilation(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        d = data(test_data)
        scene = layer(
            show(d | rep_surface(), encode(color=(0.5, 0.5, 0.5))),
            show(d | rep_outline(), encode(color=(1, 1, 1), opacity=0.2)),
        )
        compiled = compile_scene(scene)
        assert len(compiled) == 2
        assert isinstance(compiled[0][0], vtk.vtkActor)
        assert isinstance(compiled[1][0], vtk.vtkActor)

    def test_with_renderer(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        renderer = vtk.vtkRenderer()
        d = data(test_data)
        scene = layer(
            show(d | rep_surface(), encode(color=(0.5, 0.5, 0.5))),
            show(d | rep_outline(), encode(color=(1, 1, 1))),
        )
        compile_scene(scene, renderer=renderer)
        assert renderer.GetActors().GetNumberOfItems() == 2

    def test_volume(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        d = data(test_data)
        result = show(
            d | rep_volume("density"),
            encode(
                color=scale_color("density", range=[0, 240], colormap="cool_to_warm"),
                opacity=scale_opacity("density", [(0, 0.0), (120, 0.3), (240, 0.8)]),
                shade=True,
            )
        )
        compiled = compile_scene(result)
        vol, bar = compiled[0]
        assert isinstance(vol, vtk.vtkVolume)

    def test_with_where_transform(self, test_data):
        from vislang_grammar import compile_scene
        import vtk
        d = data(test_data)
        result = show(
            d | where("density", between=[100, 200]) | rep_surface(),
            encode(color=(1, 0, 0))
        )
        compiled = compile_scene(result)
        actor, bar = compiled[0]
        assert isinstance(actor, vtk.vtkActor)

    def test_with_scale_color(self, test_data):
        from vislang_grammar import compile_scene
        d = data(test_data)
        result = show(
            d | rep_surface(),
            encode(
                color=scale_color("density", range=[0, 240], colormap="cool_to_warm"),
                legend="Density"
            )
        )
        compiled = compile_scene(result)
        actor, bar = compiled[0]
        assert bar is not None  # scalar bar created

    def test_unbound_rep_error(self):
        from vislang_grammar import compile_scene
        # RepSpec without data source
        result = show(rep_surface(), encode())
        with pytest.raises(ValueError, match="no data source"):
            compile_scene(result)

    def test_bad_extension_error(self):
        from vislang_grammar import compile_scene
        d = data("file.xyz")
        result = show(d | rep_surface(), encode())
        with pytest.raises(ValueError, match="Cannot infer VTK reader"):
            compile_scene(result)
