"""CDF v2 evidence-source adapters.

Public modules ``s1_user`` through ``s6_external`` extract typed source units and
hand them only to ``core.ingestion.api_v2``.
"""

__all__ = ["s1_user", "s2_session", "s3_run", "s4_code", "s5_metrics", "s6_external"]
