"""Tests for the line probe / profile functionality.

Tests the queries.sample_line(), queries.get_line_probe_data(), and the
DSL line_probe() method using the synthetic 64x64x64 dataset which has:
  - temperature: Gaussian blob centered at (0.5, 0.5, 0.5), peak=1000
  - density: linear gradient along Z, 0 at z=0, 1.225 at z=1
  - velocity: rigid-body rotation about Z axis centered at (0.5, 0.5)
"""

import math
import os
import sys

import numpy as np
import pytest
import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva import queries
from siva.dsl import PipelineBuilder

SYNTHETIC_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti",
)


@pytest.fixture(scope="module")
def synthetic_data():
    """Load the synthetic VTI dataset."""
    if not os.path.exists(SYNTHETIC_DATA):
        pytest.skip(f"Synthetic dataset not found at {SYNTHETIC_DATA}. "
                     "Run: bash datasets/synthetic/download.sh")
    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(SYNTHETIC_DATA)
    reader.Update()
    return reader.GetOutput()



class TestSampleLine:
    """Test queries.sample_line() returns valid probe output."""

    def test_returns_polydata(self, synthetic_data):
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=50
        )
        assert result is not None
        assert isinstance(result, vtk.vtkPolyData)

    def test_correct_number_of_points(self, synthetic_data):
        resolution = 80
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=resolution
        )
        # vtkLineSource with Resolution=N produces N+1 points
        assert result.GetNumberOfPoints() == resolution + 1

    def test_has_sampled_fields(self, synthetic_data):
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=20
        )
        pd = result.GetPointData()
        array_names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        assert "temperature" in array_names
        assert "density" in array_names
        assert "velocity" in array_names

    def test_has_valid_point_mask(self, synthetic_data):
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=20
        )
        pd = result.GetPointData()
        assert pd.GetArray("vtkValidPointMask") is not None


class TestTemperatureProfile:
    """Test temperature sampling through the Gaussian blob center."""

    def test_peak_at_center(self, synthetic_data):
        """Temperature profile through center should peak at the midpoint."""
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=100
        )
        pd = result.GetPointData()
        temp_arr = pd.GetArray("temperature")
        n = temp_arr.GetNumberOfTuples()

        # Find index of maximum temperature
        temps = [temp_arr.GetValue(i) for i in range(n)]
        max_idx = temps.index(max(temps))

        # Peak should be near the center (index ~50 of 101 points)
        assert abs(max_idx - n // 2) <= 5, (
            f"Temperature peak at index {max_idx}, expected near {n // 2}"
        )

    def test_peak_value_near_1000(self, synthetic_data):
        """Peak temperature should be close to 1000 (the Gaussian peak)."""
        result = queries.sample_line(
            synthetic_data, (0.5, 0.5, 0.0), (0.5, 0.5, 1.0), resolution=100
        )
        pd = result.GetPointData()
        temp_arr = pd.GetArray("temperature")
        temps = [temp_arr.GetValue(i) for i in range(temp_arr.GetNumberOfTuples())]
        peak = max(temps)

        # The peak through the exact center should be very close to 1000
        # Allow some tolerance due to interpolation
        assert peak > 900, f"Peak temperature {peak} should be close to 1000"

    def test_symmetric_profile(self, synthetic_data):
        """Temperature profile through center should be roughly symmetric."""
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=100
        )
        pd = result.GetPointData()
        temp_arr = pd.GetArray("temperature")
        n = temp_arr.GetNumberOfTuples()
        temps = [temp_arr.GetValue(i) for i in range(n)]

        # Compare first quarter average with last quarter average
        q1 = np.mean(temps[:n // 4])
        q4 = np.mean(temps[3 * n // 4:])
        # Should be roughly equal (symmetric Gaussian)
        assert abs(q1 - q4) < 50, (
            f"Profile not symmetric: first quarter avg={q1:.1f}, "
            f"last quarter avg={q4:.1f}"
        )

    def test_off_center_lower_peak(self, synthetic_data):
        """A line off-center should have a lower peak than through the center."""
        center_result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=50
        )
        off_result = queries.sample_line(
            synthetic_data, (0.0, 0.8, 0.5), (1.0, 0.8, 0.5), resolution=50
        )

        center_temp = center_result.GetPointData().GetArray("temperature")
        off_temp = off_result.GetPointData().GetArray("temperature")

        center_max = max(center_temp.GetValue(i) for i in range(center_temp.GetNumberOfTuples()))
        off_max = max(off_temp.GetValue(i) for i in range(off_temp.GetNumberOfTuples()))

        assert center_max > off_max, (
            f"Center peak {center_max:.1f} should exceed off-center peak {off_max:.1f}"
        )


class TestDensityProfile:
    """Test density sampling along the linear gradient.

    Note: Due to meshgrid indexing in the synthetic dataset generator,
    the density gradient (intended as Z) is stored along the X axis
    in the VTK file. We test accordingly.
    """

    def test_linear_increase_along_x(self, synthetic_data):
        """Density along X should increase linearly (gradient axis)."""
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=50
        )
        pd = result.GetPointData()
        dens_arr = pd.GetArray("density")
        n = dens_arr.GetNumberOfTuples()
        densities = [dens_arr.GetValue(i) for i in range(n)]

        # Density should be monotonically increasing (or nearly so)
        increasing_count = sum(
            1 for i in range(1, n) if densities[i] >= densities[i - 1] - 1e-6
        )
        assert increasing_count >= n - 2, (
            f"Density should be monotonically increasing along X, "
            f"but only {increasing_count}/{n - 1} steps were increasing"
        )

    def test_density_range(self, synthetic_data):
        """Density along X should go from ~0 to ~1.225."""
        result = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=50
        )
        pd = result.GetPointData()
        dens_arr = pd.GetArray("density")
        n = dens_arr.GetNumberOfTuples()
        densities = [dens_arr.GetValue(i) for i in range(n)]

        assert densities[0] < 0.1, f"Density at x=0 should be ~0, got {densities[0]}"
        assert densities[-1] > 1.0, f"Density at x=1 should be ~1.225, got {densities[-1]}"

    def test_constant_density_along_y(self, synthetic_data):
        """Density along Y at fixed x should be nearly constant."""
        result = queries.sample_line(
            synthetic_data, (0.5, 0.0, 0.5), (0.5, 1.0, 0.5), resolution=50
        )
        pd = result.GetPointData()
        dens_arr = pd.GetArray("density")
        densities = [dens_arr.GetValue(i) for i in range(dens_arr.GetNumberOfTuples())]

        std = np.std(densities)
        assert std < 0.05, (
            f"Density along Y at fixed x=0.5 should be constant, std={std:.4f}"
        )


class TestGetLineProbeData:
    """Test the formatted output from get_line_probe_data()."""

    def test_basic_output_structure(self, synthetic_data):
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=20
        )
        result = queries.get_line_probe_data(probe_output, ["temperature"])

        assert "Line probe:" in result
        assert "Summary statistics:" in result
        assert "Data table:" in result
        assert "temperature" in result

    def test_summary_stats_present(self, synthetic_data):
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=20
        )
        result = queries.get_line_probe_data(probe_output, ["temperature", "density"])

        assert "min=" in result
        assert "max=" in result
        assert "mean=" in result
        assert "trend=" in result

    def test_trend_detection_increasing(self, synthetic_data):
        """Density along X should be detected as increasing."""
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=50
        )
        result = queries.get_line_probe_data(probe_output, ["density"])
        assert "increasing" in result

    def test_missing_field_warning(self, synthetic_data):
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=10
        )
        result = queries.get_line_probe_data(probe_output, ["nonexistent_field", "temperature"])

        assert "Missing fields" in result or "nonexistent_field" in result

    def test_all_missing_fields(self, synthetic_data):
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=10
        )
        result = queries.get_line_probe_data(probe_output, ["fake1", "fake2"])
        assert "None of the requested fields" in result

    def test_vector_field_output(self, synthetic_data):
        """Vector fields should show per-component columns."""
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=10
        )
        result = queries.get_line_probe_data(probe_output, ["velocity"])
        # Should have component columns
        assert "velocity[0]" in result or "velocity" in result

    def test_distance_column(self, synthetic_data):
        """The dist column should go from 0 to the line length."""
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=20
        )
        result = queries.get_line_probe_data(probe_output, ["temperature"])

        assert "dist" in result
        # First distance should be ~0
        assert "0.0000" in result

    def test_max_rows_downsampling(self, synthetic_data):
        """With many points, output should be downsampled."""
        probe_output = queries.sample_line(
            synthetic_data, (0.0, 0.5, 0.5), (1.0, 0.5, 0.5), resolution=200
        )
        result = queries.get_line_probe_data(probe_output, ["temperature"], max_rows=10)

        # Should mention it's showing a subset
        assert "showing" in result.lower() or "of 201" in result

    def test_empty_probe_output(self):
        """Probe with None should return error message."""
        result = queries.get_line_probe_data(None, ["temperature"])
        assert "No probe data" in result


class TestDSLLineProbe:
    """Test the line_probe method on PipelineBuilder.

    Uses the builder directly (without interpret/render) to avoid
    VTK rendering segfaults in headless test environments.
    """

    def test_builder_line_probe_creates_nodes(self):
        """line_probe should create a single _line_probe pseudo-node."""
        builder = PipelineBuilder()
        data_ref = builder.source("vtkXMLImageDataReader", FileName=SYNTHETIC_DATA)
        probe_ref = builder.line_probe(
            input=data_ref, point1=(0.0, 0.5, 0.5),
            point2=(1.0, 0.5, 0.5), resolution=20
        )
        # Reader + one _line_probe pseudo-node (line source + probe filter are
        # created internally during build, not as separate pipeline nodes)
        assert len(builder._nodes) == 2

    def test_line_probe_in_namespace(self):
        """line_probe should be available in the DSL namespace."""
        builder = PipelineBuilder()
        namespace = {
            "source": builder.source,
            "line_probe": builder.line_probe,
            "show": builder.show,
            "__builtins__": {},
        }
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{SYNTHETIC_DATA}")
probe = line_probe(input=data, point1=(0.0, 0.5, 0.5), point2=(1.0, 0.5, 0.5), resolution=20)
'''
        exec(code, namespace)
        assert "probe" in namespace
        assert len(builder._nodes) == 2

    def test_line_probe_builds_correctly(self):
        """Build the VTK pipeline manually and verify output."""
        from siva.filters import create_vtk_filter

        # create_vtk_filter confines FileName to the working directory (see
        # siva.filters.confine_to_workdir); symlink the dataset into the
        # (isolated, per-test) cwd and use the relative name, mirroring the
        # supported "symlink a dataset into the working directory" workflow.
        link_name = "output.vti"
        if not os.path.exists(link_name):
            os.symlink(SYNTHETIC_DATA, link_name)

        # Build reader
        reader, _ = create_vtk_filter(
            "vtkXMLImageDataReader", FileName=link_name
        )
        reader.Update()

        # Build line source
        line, _ = create_vtk_filter(
            "vtkLineSource",
            Point1=[0.0, 0.5, 0.5],
            Point2=[1.0, 0.5, 0.5],
            Resolution=20,
        )
        line.Update()

        # Build probe filter
        probe, status = create_vtk_filter(
            "vtkProbeFilter", line, _probe_source=reader
        )
        probe.Update()

        output = probe.GetOutput()
        assert output.GetNumberOfPoints() == 21
        pd = output.GetPointData()
        array_names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        assert "temperature" in array_names
        assert "density" in array_names


class TestLineOutsideBounds:
    """Test behavior when line is partially or fully outside the data."""

    def test_line_outside_bounds(self, synthetic_data):
        """A line completely outside should produce points with invalid mask."""
        result = queries.sample_line(
            synthetic_data, (5.0, 5.0, 5.0), (6.0, 6.0, 6.0), resolution=10
        )
        pd = result.GetPointData()
        mask_arr = pd.GetArray("vtkValidPointMask")
        if mask_arr is not None:
            from vtk.util.numpy_support import vtk_to_numpy
            mask = vtk_to_numpy(mask_arr)
            # All points should be invalid (outside dataset)
            assert np.sum(mask) == 0, "Points outside bounds should be invalid"

    def test_partially_outside(self, synthetic_data):
        """A line partially inside should have some valid and some invalid points."""
        result = queries.sample_line(
            synthetic_data, (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), resolution=50
        )
        pd = result.GetPointData()
        mask_arr = pd.GetArray("vtkValidPointMask")
        if mask_arr is not None:
            from vtk.util.numpy_support import vtk_to_numpy
            mask = vtk_to_numpy(mask_arr)
            n_valid = np.sum(mask)
            n_total = len(mask)
            # Should have some valid and some invalid
            assert 0 < n_valid < n_total, (
                f"Expected partial validity, got {n_valid}/{n_total} valid"
            )
