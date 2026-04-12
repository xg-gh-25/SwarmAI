# Data Retriever Proxy — Client Library
# Use this from the Global EC2 to call the proxy in CN region.

"""
Usage:
    from client import DataProxy

    proxy = DataProxy(
        url="https://xxxxx.lambda-url.cn-northwest-1.on.aws/",
        api_key="your-api-key"
    )

    # Health check
    result = proxy.health()

    # Call GCR Sales Data API
    result = proxy.api("GET", "/api/v1/forecast/cycles", query={"year": "2026"})

    # Query Athena
    result = proxy.athena("SELECT * FROM fact_estimated_revenue LIMIT 10", database="rl_quicksight_reporting", region="cn-north-1")
"""

import json
import requests


class DataProxy:
    """Client for the Data Retriever Proxy Lambda."""

    def __init__(self, url: str, api_key: str, timeout: int = 60):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, payload: dict) -> dict:
        resp = requests.post(
            self.url,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
            },
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict:
        """Check proxy health + connectivity to API Gateway."""
        return self._call({"action": "health"})

    def api(self, method: str, path: str, query: dict = None, body: dict = None) -> dict:
        """
        Call GCR Sales Data API via proxy.

        Args:
            method: HTTP method (GET, POST, PUT)
            path: API path (e.g. /api/v1/forecast/cycles)
            query: Query string parameters
            body: Request body (JSON)
        """
        payload = {
            "action": "api",
            "method": method,
            "path": path,
        }
        if query:
            payload["query"] = query
        if body:
            payload["body"] = body
        return self._call(payload)

    def athena(self, query: str, database: str = "sales_share", region: str = None,
               max_wait: int = 60, alias: str = None) -> dict:
        """
        Execute a read-only SQL query against Athena.

        Args:
            query: SQL SELECT statement
            database: Athena database name (default: sales_share)
            region: AWS region (default: cn-northwest-1, also supports cn-north-1)
            max_wait: Max seconds to wait for query completion (default: 60)
            alias: Optional shrimp alias — if provided, query executes as
                   claw-{alias}-role with LF row-level filtering enforced
        """
        payload = {
            "action": "athena",
            "query": query,
            "database": database,
            "max_wait": max_wait,
        }
        if region:
            payload["region"] = region
        if alias:
            payload["alias"] = alias
        return self._call(payload)

    # ── Convenience methods for GCR Sales Data API ──────────────

    def get_forecast_cycles(self, year: str) -> dict:
        """Get forecast cycles for a year."""
        return self.api("GET", "/api/v1/forecast/cycles", query={"year": year})

    def get_forecast_view(self, cycle_id: str, region: str = "OVERALL",
                          login: str = None, hierarchy: dict = None,
                          parent_hierarchy: list = None) -> dict:
        """
        Get forecast view (comprehensive monthly/quarterly/yearly, Core+GenAI).

        Args:
            cycle_id: fcst_YYYY_MM format
            region: OVERALL or CHINA_REGION
            login: User alias (optional)
            hierarchy: Single hierarchy dict, e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
            parent_hierarchy: List of hierarchy dicts to get children,
                              e.g. [{"hierarchyId": "GCR"}] returns SH_L2 BUs
        """
        query = {"region": region}
        if login:
            query["login"] = login
        body = {}
        if hierarchy:
            body["hierarchy"] = hierarchy
        if parent_hierarchy:
            body["parentHierarchy"] = parent_hierarchy
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/view",
                        query=query, body=body or None)

    def get_forecast_revenue(self, cycle_id: str, login: str = None,
                             hierarchy: dict = None) -> dict:
        """
        Get forecast revenue (Core + GenAI yearly + monthly).

        NOTE: cycleId must be revenue_YYYY_MM format (not fcst_YYYY_MM).
        This endpoint may return 404 — not all environments deploy it.

        Args:
            cycle_id: revenue_YYYY_MM format
            login: User alias (optional)
            hierarchy: e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
        """
        query = {}
        if login:
            query["login"] = login
        body = None
        if hierarchy:
            body = hierarchy  # @httpPayload — body IS the Hierarchy struct
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/revenue/query",
                        query=query or None, body=body)

    def get_forecast_baseline(self, cycle_id: str, region: str = "OVERALL",
                              login: str = None, hierarchy: dict = None,
                              parent_hierarchy: dict = None) -> dict:
        """
        Get forecast baseline (target, YTD FBR, ROY estimates, gap).

        Args:
            cycle_id: fcst_YYYY_MM format
            region: OVERALL or CHINA_REGION
            login: User alias (optional)
            hierarchy: e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
            parent_hierarchy: e.g. {"hierarchyId": "GCR"} to get children
        """
        query = {"region": region}
        if login:
            query["login"] = login
        body = {}
        if hierarchy:
            body["hierarchy"] = hierarchy
        if parent_hierarchy:
            body["parentHierarchy"] = parent_hierarchy
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/baseline/query",
                        query=query, body=body or None)

    def get_baseline_breakdown(self, cycle_id: str, region: str = "OVERALL",
                               login: str = None,
                               hierarchy: dict = None) -> dict:
        """
        Get baseline breakdown (Revenue + FlowIn + FlowOut, monthly/quarterly/yearly).

        NOTE: This endpoint may return 404 — not all environments deploy it.

        Args:
            cycle_id: fcst_YYYY_MM format
            region: OVERALL or CHINA_REGION
            login: User alias (required)
            hierarchy: e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
        """
        query = {"region": region}
        if login:
            query["login"] = login
        body = {}
        if hierarchy:
            body["hierarchy"] = hierarchy
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/baseline/breakdown",
                        query=query, body=body or None)

    def list_accounts(self, cycle_id: str, login: str,
                      hierarchy: dict = None, max_results: int = 100,
                      next_token: str = None) -> dict:
        """
        List accounts with pagination.

        Args:
            cycle_id: fcst_YYYY_MM format
            login: User alias (required)
            hierarchy: e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
            max_results: 1-100 (default 100)
            next_token: Pagination token from previous response
        """
        query = {"login": login, "maxResults": str(max_results)}
        if next_token:
            query["nextToken"] = next_token
        body = {}
        if hierarchy:
            body["hierarchy"] = hierarchy
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/baseline/accounts",
                        query=query, body=body or None)

    def get_forecast_input(self, cycle_id: str, login: str = None,
                           hierarchy: dict = None,
                           parent_hierarchy: dict = None) -> dict:
        """
        Get forecast input data (monthly forecast by Core/GenAI, with overrides).

        Args:
            cycle_id: fcst_YYYY_MM format
            login: User alias (optional)
            hierarchy: e.g. {"hierarchyId": "GCR/RFHC/CROSS"}
            parent_hierarchy: e.g. {"hierarchyId": "GCR"} to get children
        """
        query = {}
        if login:
            query["login"] = login
        body = {}
        if hierarchy:
            body["hierarchy"] = hierarchy
        if parent_hierarchy:
            body["parentHierarchy"] = parent_hierarchy
        return self.api("POST", f"/api/v1/forecast/{cycle_id}/input/query",
                        query=query or None, body=body or None)

    def get_tableau_data(self, view_id: str, file_type: str = "Csv",
                         filters: dict = None) -> dict:
        """Export Tableau view data."""
        body = {"fileType": file_type}
        if filters:
            body["filters"] = filters
        return self.api("POST", f"/tableau/{view_id}", body=body)

    # ── Sales Hierarchy Permission & Data Filter ──────────────────

    # Canonical level ordering (low number = higher in tree = broader access)
    _SH_LEVELS = ["SH_L1", "SH_L2", "SH_L3", "SH_L4", "SH_L5", "SH_L6", "SH_L7", "TERRITORY"]
    _LEVEL_RANK = {lvl: i for i, lvl in enumerate(_SH_LEVELS)}
    _LEVEL_TO_FIELD = {
        "SH_L1": "sh_l1", "SH_L2": "sh_l2", "SH_L3": "sh_l3",
        "SH_L4": "sh_l4", "SH_L5": "sh_l5", "SH_L6": "sh_l6",
        "SH_L7": "sh_l7", "TERRITORY": "territory",
    }

    def get_hierarchy_permissions(self, alias: str) -> dict:
        """
        Query sales_hierarchy_node_v2 (LIVE version) to find all nodes
        where `alias` appears as primary_owner / secondary_owner /
        gso_alias / observer, then compute the minimal covering set
        and generate a SQL WHERE clause.

        Uses Athena CONTAINS() for array<string> columns (secondary_owner,
        observer) so only matching rows are returned from the server.

        Args:
            alias: Amazon login alias (e.g. 'danffer')

        Returns:
            {
                "alias": "danffer",
                "full_access": False,
                "nodes": [                       # minimal covering set
                    {"level": "SH_L2", "name": "SMB", "roles": ["primary_owner"]},
                    {"level": "SH_L3", "name": "IND GFD", "roles": ["primary_owner"]},
                    {"level": "SH_L3", "name": "NWCD", "roles": ["primary_owner"]},
                ],
                "filter_expression": "sh_l2 = 'SMB' OR sh_l3 IN ('IND GFD', 'NWCD')",
                "live_version_id": "v202603-..."
            }

            If the user has SH_L1 access, full_access=True and
            filter_expression=None (no restriction needed).
        """
        from collections import defaultdict

        _DB = "gcr_permission_stream"
        _REGION = "cn-northwest-1"

        # Step 0: find LIVE version
        ver_result = self.athena(
            "SELECT version_id FROM sales_hierarchy_version "
            "WHERE identifier = 'LIVE' AND (is_deleted IS NULL OR is_deleted != 'true')",
            database=_DB, region=_REGION,
        )
        if not ver_result.get("rows"):
            raise RuntimeError("No LIVE version found in sales_hierarchy_version")
        live_vid = ver_result["rows"][0]["version_id"]

        # Step 1: server-side filter — only return nodes where alias has a role
        # primary_owner / gso_alias are string; secondary_owner / observer are array<string>
        match_sql = (
            "SELECT node_level, node_name, node_id, parent_node_id, "
            "primary_owner, secondary_owner, gso_alias, observer "
            "FROM sales_hierarchy_node_v2 "
            f"WHERE version_id = '{live_vid}' "
            "AND (is_deleted IS NULL OR is_deleted != 'true') "
            "AND ("
            f"primary_owner = '{alias}' "
            f"OR CONTAINS(secondary_owner, '{alias}') "
            f"OR gso_alias = '{alias}' "
            f"OR CONTAINS(observer, '{alias}')"
            ")"
        )
        match_result = self.athena(match_sql, database=_DB, region=_REGION)
        matched_rows = match_result.get("rows", [])

        if not matched_rows:
            return {
                "alias": alias,
                "full_access": False,
                "nodes": [],
                "filter_expression": "1=0",  # no access
                "live_version_id": live_vid,
            }

        # Step 2: determine roles for each matched node
        def _parse_roles(row):
            roles = []
            if row.get("primary_owner") == alias:
                roles.append("primary_owner")
            if row.get("gso_alias") == alias:
                roles.append("gso_alias")
            for col in ("secondary_owner", "observer"):
                val = row.get(col) or ""
                cleaned = val.replace("[", "").replace("]", "")
                if alias in [a.strip() for a in cleaned.split(",") if a.strip()]:
                    roles.append(col)
            return roles

        matched = []
        for row in matched_rows:
            matched.append({
                "level": row["node_level"],
                "name": row["node_name"],
                "node_id": row["node_id"],
                "parent_node_id": row.get("parent_node_id"),
                "roles": _parse_roles(row),
            })

        # Step 3: check for full access (SH_L1)
        if any(m["level"] == "SH_L1" for m in matched):
            return {
                "alias": alias,
                "full_access": True,
                "nodes": [
                    {"level": m["level"], "name": m["name"], "roles": m["roles"]}
                    for m in matched if m["level"] == "SH_L1"
                ],
                "filter_expression": None,
                "live_version_id": live_vid,
            }

        # Step 4: prune — remove nodes whose ancestor is also in the matched set
        # We need parent_node_id chain. For a small matched set we can walk
        # up using only the matched nodes themselves; but if a node's parent
        # isn't in matched we need the full tree's parent map.
        # Fetch parent map for all LIVE nodes (lightweight: only id + parent).
        tree_sql = (
            "SELECT node_id, parent_node_id FROM sales_hierarchy_node_v2 "
            f"WHERE version_id = '{live_vid}' "
            "AND (is_deleted IS NULL OR is_deleted != 'true')"
        )
        tree_result = self.athena(tree_sql, database=_DB, region=_REGION)
        parent_map = {
            r["node_id"]: r.get("parent_node_id")
            for r in tree_result.get("rows", [])
        }
        matched_ids = {m["node_id"] for m in matched}

        def _has_ancestor_in_set(node_id):
            visited = set()
            current = parent_map.get(node_id)
            while current and current not in visited:
                if current in matched_ids:
                    return True
                visited.add(current)
                current = parent_map.get(current)
            return False

        minimal = [m for m in matched if not _has_ancestor_in_set(m["node_id"])]
        minimal.sort(key=lambda m: (self._LEVEL_RANK.get(m["level"], 99), m["name"]))

        # Step 5: generate SQL WHERE expression
        by_level = defaultdict(list)
        for m in minimal:
            field = self._LEVEL_TO_FIELD.get(m["level"])
            if field:
                by_level[field].append(m["name"])

        clauses = []
        for level in self._SH_LEVELS:
            col = self._LEVEL_TO_FIELD.get(level)
            if col and col in by_level:
                names = sorted(by_level[col])
                if len(names) == 1:
                    clauses.append(f"{col} = '{names[0]}'")
                else:
                    in_list = ", ".join(f"'{n}'" for n in names)
                    clauses.append(f"{col} IN ({in_list})")

        filter_expr = " OR ".join(clauses) if clauses else "1=0"

        return {
            "alias": alias,
            "full_access": False,
            "nodes": [
                {"level": m["level"], "name": m["name"], "roles": m["roles"]}
                for m in minimal
            ],
            "filter_expression": filter_expr,
            "live_version_id": live_vid,
        }

    def lf_get_filter(self, alias: str) -> dict:
        """
        Get the data filter expression for a user via Lambda-side computation.

        Calls the Lambda lf_get_filter action which queries
        gcr_permission_stream.sales_hierarchy_node_v2 (LIVE version).

        Args:
            alias: Amazon user alias (e.g. 'zhangaz')

        Returns:
            {
                "alias": "zhangaz",
                "full_access": false,
                "nodes": [
                    {"level": "SH_L3", "name": "ISV & SUP", "roles": ["primary_owner"]}
                ],
                "filter_expression": "sh_l3 = 'ISV & SUP'",
                "live_version_id": "v202603-..."
            }

            filter_expression is None if user has full_access=true.
            filter_expression is "1=0" if user has no permissions.
        """
        return self._call({
            "action": "lf_get_filter",
            "alias": alias,
        })

    def lf_provision(self, alias: str, tables: list = None) -> dict:
        """
        Provision a shrimp's data access: IAM Role + Lake Formation filters.

        Creates claw-{alias}-role with appropriate trust policy and
        data access permissions, then sets up LF Data Cell Filters
        based on the user's sales hierarchy.

        Args:
            alias: Amazon login alias (e.g. 'zhangaz')
            tables: Optional list of table names to provision (default: all)

        Returns:
            {
                "alias": "zhangaz",
                "role_arn": "arn:aws-cn:iam::275763995903:role/claw-zhangaz-role",
                "full_access": false,
                "filter_expression": "sh_l3 = 'ISV & SUP'",
                "results": { ... }
            }
        """
        payload = {"action": "lf_provision", "alias": alias}
        if tables:
            payload["tables"] = tables
        return self._call(payload)

    def lf_revoke(self, alias: str, tables: list = None,
                  delete_role: bool = True) -> dict:
        """
        Revoke Lake Formation permissions, delete filters, and optionally
        delete the shrimp's IAM Role.

        Args:
            alias: Amazon login alias
            tables: Optional list of table names to revoke (default: all)
            delete_role: Whether to delete the IAM role (default: True)
        """
        payload = {"action": "lf_revoke", "alias": alias, "delete_role": delete_role}
        if tables:
            payload["tables"] = tables
        return self._call(payload)

    def lf_status(self, alias: str) -> dict:
        """
        Query the current provisioned state for a shrimp.

        Returns IAM Role existence, LF Data Cell Filters, grants,
        and the hierarchy permissions (what SHOULD be configured).

        Args:
            alias: Amazon login alias (e.g. 'zhangaz')
        """
        return self._call({"action": "lf_status", "alias": alias})

    # ── Report Publishing (unified entry point) ─────────────────

    VALID_REPORT_TYPES = ("pipeline", "usage_and_revenue")

    def os_query_report(self, cycle_year: str = None, cycle_week: str = None,
                        hierarchy_level: str = None, hierarchy_name: str = None,
                        report_type: str = None, sales_area: str = None,
                        presign: bool = True, presign_expires: int = 3600,
                        size: int = 10) -> dict:
        """
        Query reports from OpenSearch, with optional S3 presigned URLs.

        Supports both new hierarchy fields and legacy sales_area lookups.

        Args:
            cycle_year: e.g. "2026"
            cycle_week: e.g. "14"
            hierarchy_level: "gcr", "l2", "l3", "l4", "l5", "territory"
            hierarchy_name: Name at that level, e.g. "RFHC"
            report_type: e.g. "overall_report_usage_and_revenue"
            sales_area: Legacy field (used if hierarchy_name not provided)
            presign: Whether to generate S3 presigned URLs (default True)
            presign_expires: URL validity in seconds (default 3600)
            size: Max results (default 10, max 50)

        Returns:
            {
                "status": "ok",
                "total": 1,
                "reports": [
                    {
                        "cycle_year": "2026",
                        "cycle_week": "14",
                        "hierarchy_level": "l3",
                        "hierarchy_name": "RFHC",
                        "hierarchy_path": "GCR > MXD > RFHC",
                        "report_type": "overall_report_usage_and_revenue",
                        "report_metadata": { ... },
                        "presigned_url": "https://...",
                        ...
                    }
                ]
            }
        """
        payload = {"action": "os_query", "presign": presign,
                   "presign_expires": presign_expires, "size": size}
        if cycle_year:
            payload["cycle_year"] = cycle_year
        if cycle_week:
            payload["cycle_week"] = cycle_week
        if hierarchy_level:
            payload["hierarchy_level"] = hierarchy_level
        if hierarchy_name:
            payload["hierarchy_name"] = hierarchy_name
        if report_type:
            payload["report_type"] = report_type
        if sales_area:
            payload["sales_area"] = sales_area
        return self._call(payload)

    def dr_publish_report(self, cycle_year: str, cycle_week: str,
                          sales_area: str, report_type: str,
                          report_content: str, operator: str,
                          regions: str = "GCR") -> dict:
        """
        Unified entry point for publishing reports to Brainforce portal.

        Routes by report_type:
          - "pipeline"            → DDB KVCache (brainforce-prod)
          - "usage_and_revenue"   → OpenSearch usage_and_revenue (fast-prod)
        Both also register in OpenSearch report_generated_weeks.

        Args:
            cycle_year: e.g. "2026"
            cycle_week: e.g. "13"
            sales_area: e.g. "RFHC", "CMHK", "AUTO & MFG"
            report_type: "pipeline" or "usage_and_revenue"
            report_content: HTML report content
            operator: login of whoever approved the publish
            regions: "GCR" or "China Regions" (default "GCR", used by usage_and_revenue)
        """
        if report_type not in self.VALID_REPORT_TYPES:
            raise ValueError(
                f"Unsupported report_type: '{report_type}'. "
                f"Must be one of: {self.VALID_REPORT_TYPES}"
            )

        if report_type == "pipeline":
            return self._publish_pipeline(
                cycle_year, cycle_week, sales_area, report_content, operator
            )
        else:  # usage_and_revenue
            return self._publish_revenue(
                cycle_year, cycle_week, sales_area, regions, report_content, operator
            )

    # ── Internal: Pipeline → DDB KVCache ────────────────────────

    def _publish_pipeline(self, cycle_year, cycle_week, sales_area,
                          report_content, operator):
        """Write pipeline report to DDB KVCache + register."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        record_id = f"{cycle_year}__{cycle_week}__{sales_area}__HTML"
        item = {
            "agent": "bigenie",
            "record_id": record_id,
            "content": report_content,
            "create_time": now,
            "update_time": now,
        }

        return self._call({
            "action": "ddb_write",
            "table": "KVCache",
            "item": item,
            "operator": operator,
        })

    # ── Internal: Revenue → OpenSearch ──────────────────────────

    def _publish_revenue(self, cycle_year, cycle_week, sales_area,
                         regions, report_content, operator):
        """Write revenue report to OpenSearch + register."""
        report_type = "overall_report_usage_and_revenue"
        title = f"{cycle_year}_{cycle_week}_{sales_area}_{regions}_{report_type}"
        doc = {
            "cycle_year": cycle_year,
            "cycle_week": cycle_week,
            "sales_area": sales_area,
            "regions": regions,
            "report_type": report_type,
            "report_format_type": "html",
            "report_title": title,
            "report_content": report_content,
        }
        return self._call({
            "action": "os_write",
            "doc": doc,
            "operator": operator,
        })

    def os_publish_report_metadata(self, cycle_year: str, cycle_week: str,
                                   hierarchy_level: str, hierarchy_name: str,
                                   s3_key: str, report_metadata: dict,
                                   operator: str = "dataretriever",
                                   hierarchy_path: str = None,
                                   report_type: str = "overall_report_usage_and_revenue",
                                   report_title: str = None,
                                   s3_bucket: str = None) -> dict:
        """
        Write report metadata to OpenSearch (new-style, no HTML content).

        HTML lives in S3, only metadata + s3_key go to OpenSearch.
        Also writes sales_area + regions for backward compatibility.

        Args:
            cycle_year: e.g. "2026"
            cycle_week: e.g. "14"
            hierarchy_level: "gcr", "l2", "l3", "l4", "l5", "territory"
            hierarchy_name: Name at that level, e.g. "RFHC"
            s3_key: S3 object key for the HTML report
            report_metadata: Structured summary dict (KPI, movers, highlights, etc.)
            operator: Who published (default "dataretriever")
            hierarchy_path: Full path, e.g. "GCR > MXD > RFHC"
            report_type: Report type (default "overall_report_usage_and_revenue")
            report_title: Display title (auto-generated if not provided)
            s3_bucket: S3 bucket (ignored — Lambda uses its own env var)
        """
        if not report_title:
            report_title = f"CMHK Weekly Usage & Revenue Report — W{cycle_week} {hierarchy_name}"

        doc = {
            "cycle_year": cycle_year,
            "cycle_week": cycle_week,
            "hierarchy_level": hierarchy_level,
            "hierarchy_name": hierarchy_name,
            "report_type": report_type,
            "report_format_type": "html",
            "report_title": report_title,
            "s3_key": s3_key,
            "report_metadata": report_metadata,
            # Backward compatibility
            "sales_area": hierarchy_name,
            "regions": "GCR",
        }
        if hierarchy_path:
            doc["hierarchy_path"] = hierarchy_path

        return self._call({
            "action": "os_write",
            "doc": doc,
            "operator": operator,
        })

    # ── S3 Presigned Upload ─────────────────────────────────────

    def os_get_upload_url(self, s3_key: str, content_type: str = "text/html",
                          expires_in: int = 3600) -> dict:
        """
        Get a presigned PUT URL for uploading a report to S3.

        The URL points to the report-hub bucket in cn-northwest-1,
        generated by the Data Proxy Lambda.

        Args:
            s3_key: S3 object key, e.g. "reports/weekly-revenue-report/2026-W14/rfhc.html"
            content_type: MIME type (default "text/html")
            expires_in: URL validity in seconds (default 3600)

        Returns:
            {
                "status": "ok",
                "presigned_url": "https://...",
                "bucket": "report-hub-275763995903-cn-northwest-1",
                "s3_key": "reports/...",
                "content_type": "text/html",
                "expires_in": 3600
            }
        """
        return self._call({
            "action": "os_get_upload_url",
            "s3_key": s3_key,
            "content_type": content_type,
            "expires_in": expires_in,
        })

    def upload_report_via_presigned_url(self, local_path: str, s3_key: str,
                                        content_type: str = "text/html") -> str:
        """
        Upload a local file to S3 via presigned PUT URL.

        1. Asks Data Proxy for a presigned PUT URL
        2. PUTs file content directly to S3
        3. Returns the s3_key on success

        Args:
            local_path: Path to the local file to upload
            s3_key: S3 object key
            content_type: MIME type (default "text/html")

        Returns:
            The s3_key on success

        Raises:
            RuntimeError: If presigned URL generation or upload fails
        """
        # Step 1: get presigned PUT URL from Lambda
        result = self.os_get_upload_url(s3_key, content_type=content_type)
        presigned_url = result.get("presigned_url")
        if not presigned_url:
            raise RuntimeError(f"Failed to get presigned URL: {result}")

        # Step 2: PUT file content to S3
        with open(local_path, "rb") as f:
            data = f.read()

        resp = requests.put(
            presigned_url,
            data=data,
            headers={"Content-Type": content_type},
            timeout=120,
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"S3 PUT failed: HTTP {resp.status_code} — {resp.text[:500]}"
            )

        return s3_key
