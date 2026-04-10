"""tracked_data — pandas-backed domain on top of tracked_core.

Proves that the tracked_core content-addressed caching pattern generalises
beyond the PyVista visualization domain.

Usage::

    from tracked_core.dag import DAG
    from tracked_data import execute_data_pipeline, inspect_data, tracked_read_csv

    dag = DAG()
    result = execute_data_pipeline(
        \"\"\"
        df = read_csv("data.csv")
        summary = df.groupby("region").agg({"revenue": "sum"})
        print(summary)
        \"\"\",
        dag,
    )
    print(result.stats)
"""

from tracked_core.dag import DAG  # noqa: F401 — re-exported for convenience
from .executor import execute_data_pipeline, inspect_data, tracked_read_csv

__all__ = ["DAG", "execute_data_pipeline", "inspect_data", "tracked_read_csv"]
