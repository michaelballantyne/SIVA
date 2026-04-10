"""Tests for tracked_data — pandas domain on top of tracked_core.

Proves that the tracked_core content-addressed caching pattern generalises
beyond the PyVista visualization domain.

All tests are self-contained: they create a temporary CSV file with
synthetic data and do not require any external datasets.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# Ensure tracked_core and tracked_data are importable from source
_LIB_DIR = Path(__file__).resolve().parent.parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_core.dag import DAG
from tracked_core.proxy import TrackedProxy
from tracked_data import execute_data_pipeline, inspect_data, tracked_read_csv


# ---------------------------------------------------------------------------
# Shared fixture: a temporary CSV with 100 rows
# ---------------------------------------------------------------------------

def make_test_csv() -> str:
    """Write a temp CSV and return its path."""
    path = tempfile.mktemp(suffix=".csv")
    df = pd.DataFrame({
        "name": [f"item_{i}" for i in range(100)],
        "region": ["North", "South", "East", "West"] * 25,
        "revenue": [i * 100 for i in range(100)],
        "cost": [i * 50 + 10 for i in range(100)],
    })
    df.to_csv(path, index=False)
    return path


@pytest.fixture()
def csv_path(tmp_path):
    """Provide a temporary CSV file for the test and clean up afterwards."""
    p = tmp_path / "test.csv"
    df = pd.DataFrame({
        "name": [f"item_{i}" for i in range(100)],
        "region": ["North", "South", "East", "West"] * 25,
        "revenue": [i * 100 for i in range(100)],
        "cost": [i * 50 + 10 for i in range(100)],
    })
    df.to_csv(p, index=False)
    return str(p)


# ---------------------------------------------------------------------------
# 1. test_read_csv_cached — same file twice → second call is a cache hit
# ---------------------------------------------------------------------------

def test_read_csv_cached(csv_path):
    dag = DAG()
    dag.begin_run()

    p1 = tracked_read_csv(csv_path, dag)
    stats_after_first = {"hits": dag.hits, "misses": dag.misses}

    p2 = tracked_read_csv(csv_path, dag)
    stats_after_second = {"hits": dag.hits, "misses": dag.misses}

    dag.end_run()

    assert stats_after_first["misses"] == 1
    assert stats_after_first["hits"] == 0
    assert stats_after_second["hits"] == 1
    assert stats_after_second["misses"] == 1  # no new miss

    # Both proxies wrap the same underlying DataFrame
    assert p1._hash == p2._hash
    assert isinstance(p1._real, pd.DataFrame)
    assert len(p1._real) == 100


# ---------------------------------------------------------------------------
# 2. test_query_cached — same query on same data → cache hit
# ---------------------------------------------------------------------------

def test_query_cached(csv_path):
    dag = DAG()

    code = 'df = read_csv(path)\nfiltered = df.query("revenue > 5000")'
    result1 = execute_data_pipeline(code.replace("path", f'"{csv_path}"'), dag)
    assert result1.stats["misses"] == 2  # read + query

    result2 = execute_data_pipeline(code.replace("path", f'"{csv_path}"'), dag)
    assert result2.stats["hits"] == 2
    assert result2.stats["misses"] == 0


# ---------------------------------------------------------------------------
# 3. test_groupby_agg — groupby + agg work through proxy
# ---------------------------------------------------------------------------

def test_groupby_agg(csv_path):
    dag = DAG()

    code = f"""
df = read_csv("{csv_path}")
grouped = df.groupby("region").agg({{"revenue": "sum"}})
print(grouped.to_string())
"""
    result = execute_data_pipeline(code, dag)

    assert result.stats["misses"] >= 1
    assert "North" in result.output or "South" in result.output or "East" in result.output


# ---------------------------------------------------------------------------
# 4. test_filter_change_partial_miss — change query, read cached, query re-runs
# ---------------------------------------------------------------------------

def test_filter_change_partial_miss(csv_path):
    dag = DAG()

    code1 = f'df = read_csv("{csv_path}")\nfiltered = df.query("revenue > 5000")'
    result1 = execute_data_pipeline(code1, dag)
    assert result1.stats["misses"] == 2  # read + query

    # Change the query — read is a hit, query is a miss
    code2 = f'df = read_csv("{csv_path}")\nfiltered = df.query("revenue > 3000")'
    result2 = execute_data_pipeline(code2, dag)
    assert result2.stats["hits"] == 1    # read_csv hit
    assert result2.stats["misses"] == 1  # new query → miss


# ---------------------------------------------------------------------------
# 5. test_full_pipeline — read → query → groupby → agg
# ---------------------------------------------------------------------------

def test_full_pipeline(csv_path):
    dag = DAG()

    code = f"""
df = read_csv("{csv_path}")
high = df.query("cost > 2000")
summary = high.groupby("region").agg({{"revenue": "sum", "cost": "mean"}})
print(len(high))
print(summary)
"""
    result = execute_data_pipeline(code, dag)

    assert result.stats["misses"] >= 3  # read, query, groupby+agg
    assert result.output.strip() != ""
    assert "df" in result.names
    assert "high" in result.names
    assert "summary" in result.names


# ---------------------------------------------------------------------------
# 6. test_inspect_data — inspect cached DataFrame
# ---------------------------------------------------------------------------

def test_inspect_data(csv_path):
    dag = DAG()

    pipeline = f'df = read_csv("{csv_path}")'
    execute_data_pipeline(pipeline, dag)

    inspect_code = "print(len(df))\nprint(df.head(2).to_string())"
    inspect_result = inspect_data(inspect_code, dag)

    assert "100" in inspect_result.output
    assert "name" in inspect_result.output
    assert "region" in inspect_result.output
    assert "revenue" in inspect_result.output


# ---------------------------------------------------------------------------
# 7. test_blacklist_blocks_to_csv — to_csv raises AttributeError
# ---------------------------------------------------------------------------

def test_blacklist_blocks_to_csv(csv_path):
    dag = DAG()

    dag.begin_run()
    proxy = tracked_read_csv(csv_path, dag)
    dag.end_run()

    with pytest.raises(AttributeError, match="blocked"):
        proxy.to_csv("/tmp/should_not_write.csv")


# ---------------------------------------------------------------------------
# 8. test_describe — df.describe() works through proxy
# ---------------------------------------------------------------------------

def test_describe(csv_path):
    dag = DAG()

    code = f"""
df = read_csv("{csv_path}")
stats = df.describe()
print(stats.to_string())
"""
    result = execute_data_pipeline(code, dag)

    assert result.stats["misses"] >= 2  # read + describe
    assert "revenue" in result.output
    assert "cost" in result.output
    # Second run should hit cache
    result2 = execute_data_pipeline(code, dag)
    assert result2.stats["hits"] >= 2
    assert result2.stats["misses"] == 0
