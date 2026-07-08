"""Tests for confining reader file paths to the server's working directory.

The Monty sandbox (siva.sandbox) covers spec-code execution, but file paths
reach VTK unconfined at the compute phase -- a spec could otherwise name an
absolute path or a ``../``-escaping relative path and VTK would happily read
it. ``siva.filters.confine_to_workdir`` closes that gap; it is called from
``create_vtk_filter`` before any reader's path property is used.

Covers:

1. The confinement rule in isolation (no VTK/Xvfb/data needed):
   - relative path inside the working dir -> allowed
   - absolute path -> rejected
   - lexical escapes (``../x``, ``a/../../x``) -> rejected
   - an internal ``..`` that stays inside (``a/../b.vts``) -> allowed
   - a symlink placed *inside* the working dir pointing *outside* it ->
     allowed (the deliberate exception: the OS follows the symlink at open
     time; the check is purely lexical on the named path)
2. End-to-end: ``create_vtk_filter`` rejects an out-of-bounds ``FileName``
   before attempting to read it.
"""

import os

import pytest

from siva.filters import confine_to_workdir, create_vtk_filter


def test_relative_path_inside_workdir_allowed(tmp_path):
    (tmp_path / "data.vts").write_text("stub")
    assert confine_to_workdir("data.vts", workdir=str(tmp_path)) == "data.vts"


def test_relative_path_in_subdir_allowed(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "data.vts").write_text("stub")
    assert confine_to_workdir("sub/data.vts", workdir=str(tmp_path)) == "sub/data.vts"


def test_absolute_path_rejected(tmp_path):
    with pytest.raises(ValueError, match="outside the working directory"):
        confine_to_workdir("/etc/passwd", workdir=str(tmp_path))


def test_parent_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="outside the working directory"):
        confine_to_workdir("../escape.vts", workdir=str(tmp_path))


def test_nested_parent_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="outside the working directory"):
        confine_to_workdir("a/../../escape.vts", workdir=str(tmp_path))


def test_internal_dotdot_staying_inside_allowed(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b.vts").write_text("stub")
    # a/../b.vts normalizes to b.vts, which stays inside workdir.
    assert confine_to_workdir("a/../b.vts", workdir=str(tmp_path)) == "a/../b.vts"


def test_symlink_inside_workdir_pointing_outside_is_allowed(tmp_path):
    # The untrusted actor is the spec, which cannot create files or symlinks
    # (Monty has no `os`/filesystem access). A symlink inside the working
    # directory was placed by the trusted human, so following it out is an
    # authorization, not an escape -- confinement must not treat this as a
    # violation.
    outside_dir = tmp_path.parent / "outside_data"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "real.vts"
    outside_file.write_text("real data")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    link = workdir / "linked.vts"
    os.symlink(outside_file, link)

    # Should not raise: the named path ("linked.vts") lexically stays inside
    # the working directory even though it resolves elsewhere on disk.
    assert confine_to_workdir("linked.vts", workdir=str(workdir)) == "linked.vts"


def test_default_workdir_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.vts").write_text("stub")
    assert confine_to_workdir("data.vts") == "data.vts"
    with pytest.raises(ValueError, match="outside the working directory"):
        confine_to_workdir("/etc/passwd")


def test_create_vtk_filter_rejects_absolute_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="outside the working directory"):
        create_vtk_filter("vtkXMLImageDataReader", FileName="/etc/passwd")


def test_create_vtk_filter_rejects_escaping_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="outside the working directory"):
        create_vtk_filter("vtkXMLImageDataReader", FileName="../escape.vti")
