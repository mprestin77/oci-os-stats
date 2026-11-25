# OCI Object Storage Statistics

A read-only Python utility for reporting OCI Object Storage request activity across accessible compartments in explicitly selected regions. Print cumulative totals for the selected scope, add reports per compartment or bucket, and export the results as JSON or CSV.

The script uses OCI Monitoring metrics. It does not download objects, enable access logging, or change OCI resources.

## Requirements

- Python 3.9 or later.
- The [OCI Python SDK](https://docs.oracle.com/en-us/iaas/tools/python/latest/).
- An OCI configuration file with working API-key authentication.
- Read access to the compartments, buckets, namespaces, and metrics being collected.

```bash
python3 -m pip install oci
python3 oci_os_stats.py --help
```

By default, credentials are loaded from the `DEFAULT` profile in `~/.oci/config`. Keep private keys and configuration files out of Git. This version supports API-key profiles; it does not implement session-token, instance-principal, or resource-principal authentication flags.

## Quick start

Print the cumulative report for all accessible active compartments, including the root compartment, in the explicitly selected region:

```bash
python3 oci_os_stats.py --regions us-ashburn-1
```

Add separate reports per compartment and per bucket:

```bash
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment --per-bucket
```

The default window is one hour, ending five minutes before the current time and rounded down to a whole minute. The delay allows time for metric arrival; it is not a delivery guarantee.

## Report flags

| Flag | Effect |
| --- | --- |
| `--per-compartment` | Add one row per selected compartment, combining its directly contained buckets across selected regions. |
| `--per-bucket` | Add one row per bucket and region, including the bucket OCID when available. |
| `--rollup-compartments` | Include collected descendants in each compartment row. Requires `--per-compartment`. |

The cumulative report is always included. Combine the flags to print multiple reports in one run.

```bash
# Direct totals per compartment
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment

# Individual buckets
python3 oci_os_stats.py --regions us-ashburn-1 --per-bucket

# Parent totals include collected child compartments
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment --rollup-compartments
```

**Do not sum rolled-up compartment rows:** parent and child rows overlap. The cumulative report counts each collected bucket once.

## Select compartments and regions

**`--regions` is mandatory.** Supply one or more OCI region identifiers on every collection command. Omitting it, or supplying it without a value, exits with an argument error before authentication or OCI API calls. `--help` works without regions. Duplicate region arguments are collected once.

The script never calls `ListRegionSubscriptions` and never automatically scans all subscribed regions. The caller does not need `TENANCY_INSPECT` / `inspect tenancies` for region discovery. Compartment discovery is still performed, and clients use the region configured in your OCI profile for the initial IAM compartment lookup; Object Storage and Monitoring collection run only in your requested regions.

Without `--compartment-id`, the script collects all accessible active compartments plus the tenancy root. With `--compartment-id`, it collects only that compartment unless `--include-subcompartments` is supplied.

```bash
# One compartment only
python3 oci_os_stats.py --regions us-ashburn-1 \
  --compartment-id ocid1.compartment.oc1..REPLACE_ME \
  --per-bucket

# A compartment and all accessible active descendants
python3 oci_os_stats.py --regions us-ashburn-1 \
  --compartment-id ocid1.compartment.oc1..REPLACE_ME \
  --include-subcompartments \
  --per-compartment --rollup-compartments --per-bucket

# Collect from two explicitly named regions
python3 oci_os_stats.py \
  --regions us-ashburn-1 us-phoenix-1 \
  --per-compartment --per-bucket
```

`--include-subcompartments` controls **collection scope**. `--rollup-compartments` controls **aggregation of collected data**; it does not expand collection scope.

Compartment names display as paths such as `Production/Analytics`. A report is tenancy-wide only if your permissions cover the entire tenancy and you include all relevant regions.

## Select a time window

```bash
# Previous 24 hours, ending five minutes ago
python3 oci_os_stats.py --regions us-ashburn-1 --hours 24 --per-bucket

# Explicit UTC window; replace dates as needed
python3 oci_os_stats.py --regions us-ashburn-1 \
  --start 2026-11-24T00:00:00Z \
  --end 2026-11-25T00:00:00Z \
  --per-compartment --per-bucket
```

Timestamps must include `Z` or a UTC offset and align to whole minutes. Start is inclusive; end is exclusive. `--hours` supplies the duration only when `--start` is omitted. With `--end` alone, the default duration is one hour.

The script uses one-minute intervals and resolution and limits the window duration to seven days. OCI also limits one-minute resolution by lookback from the current time: keep the requested start within the last seven days. Old historical windows can fail even if their duration is shorter than seven days.

## Output and calculations

The terminal report contains:

| Column | Meaning |
| --- | --- |
| `REGION` | Bucket region; `*` for totals across selected regions. |
| `COMPARTMENT` | Compartment path; `*` in the cumulative row. |
| `BUCKET` | Bucket name; `*` in aggregate rows. |
| `BUCKET_ID` | Bucket OCID, shown in the bucket table. |
| `REQUESTS` | Sum of returned `AllRequests` observations in the window. |
| `AVG_REQ/S` | Requests divided by the full selected window in seconds. |
| `4XX` | Sum of returned `ClientErrors` observations, covering all HTTP 4xx responses. |

Request rate is a **window average**, not a peak measurement or block-storage IOPS. `AllRequests` is queried independently; it is never calculated by adding operation counters, which may overlap.

JSON includes available counts for `GetRequests`, `HeadRequests`, `ListRequests`, `PutRequests`, `PostRequests`, `DeleteRequests`, `CopyRequests`, `RenameRequests`, and `ArchiveRequests`. These are OCI operation metrics, not a guaranteed exhaustive breakdown by raw HTTP verb.

### Missing and partial data

- `N/A` in tables and `null` in JSON mean no usable data was returned. They do not mean zero.
- A measured zero displays as `0`.
- An asterisk, such as `150*`, marks an aggregate containing buckets without data for that metric. The value sums only known observations.
- `metric_coverage` in JSON reports `with_data` and `bucket_rows` for each metric. This is bucket-level coverage, not a guarantee of every minute or request being present.
- Counts and rates in terminal and CSV output are rounded to whole numbers. Nonzero rates below one display as `<1`. JSON retains numeric precision.

Oracle notes that empty buckets emit no metrics. Missing time samples in an existing stream cannot be distinguished from inactivity. API warnings can also make collection incomplete; inspect warnings as well as coverage.

**Buckets whose names start with `oci-logs._flowlogs` are excluded from all rows and totals in this version.** Other logging buckets are not automatically excluded.

### JSON and CSV

```bash
# Numeric operation counts and coverage metadata
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment --per-bucket --json > stats.json

# Terminal tables plus a CSV export
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment --per-bucket --csv stats.csv

# Produce both formats
python3 oci_os_stats.py --regions us-ashburn-1 --per-compartment --per-bucket \
  --json --csv stats.csv > stats.json
```

Progress and warnings go to stderr. JSON goes to stdout. CSV contains cumulative rows and the selected compartment/bucket rows, identified by the `scope` column. Its values use the same rounding and missing-data markers as the terminal; use JSON for precise calculations.

## Throughput

This version does **not** measure byte throughput. OCI's documented `oci_objectstorage` metrics do not include transferred-byte counters. `StoredBytes` measures stored capacity; changes in that value are not upload or download throughput.

The JSON field `throughput_bytes_per_second` is therefore `null`. Measuring bytes/sec requires additional telemetry from applications, SDK clients, or a proxy with traffic visibility. This version has no custom byte-metric options.

## Investigating 404 errors

`ClientErrors` combines all HTTP 4xx responses. It cannot isolate 404s or identify the caller. A missing `ClientErrors` stream does not prove that no errors occurred.

To attribute 404s, enable read and/or write access logs on the relevant buckets, then search them in OCI Logging. Logging is configured per bucket and does not inherit through compartments. This script does not enable logging or query access logs.

In Logging Search, select the relevant log groups across compartments and regions. Keep the generated `search ...` scope and append:

```sql
| where type = 'com.oraclecloud.objectstorage.*'
| where data.statusCode = 404
| summarize count() as errors by
    data.compartmentName,
    data.bucketName,
    data.principalId,
    data.principalName,
    data.requestAction
| sort by errors desc
```

Access logs are available only after logging starts ingesting; they are delivered on a best-effort basis. A search returning no results is not proof that unlogged buckets had no errors. Standard OCI Logging storage charges can apply.

## IAM permissions

### Policy for a complete tenancy report

For API-key authentication, an administrator can create a policy in the tenancy root using the following statements. Replace `StatsReaders` with the group containing the user whose API key is configured in your OCI profile:

```text
Allow group StatsReaders to inspect compartments in tenancy
Allow group StatsReaders to read buckets in tenancy
Allow group StatsReaders to {OBJECTSTORAGE_NAMESPACE_READ} in tenancy
Allow group StatsReaders to read metrics in tenancy
```

For identity-domain groups, use your domain-qualified group name, for example `Allow group 'Default'/'StatsReaders' to read metrics in tenancy`. Use the actual identity domain and group names from your tenancy.

| Grant | API operations used by this script | When needed |
| --- | --- | --- |
| `inspect compartments in tenancy` | `ListCompartments` with subtree discovery | Compartment inventory and parent/child paths. Discovery runs even when `--compartment-id` is supplied. |
| `read buckets in tenancy` | `ListBuckets`, and optionally `GetBucket` | Bucket inventory; `GetBucket` retrieves missing bucket IDs when `--per-bucket` is selected. `read buckets` includes bucket inspection. |
| `{OBJECTSTORAGE_NAMESPACE_READ} in tenancy` | `GetNamespace` with `compartment_id=tenant` | Namespace discovery at the start of each selected region. |
| `read metrics in tenancy` | `SummarizeMetricsData`; `ListMetrics` with `--debug` | Request/error counters and optional metric-definition diagnostics. `read metrics` includes metric inspection. |

**Namespace permission detail:** the current script explicitly supplies the tenancy OCID to `GetNamespace`. Oracle requires `OBJECTSTORAGE_NAMESPACE_READ` when that optional parameter is present. The explicit permission grant above covers that call without granting namespace-update rights. A plain `read objectstorage-namespaces` statement does not supply this permission in Oracle's current policy reference.

These policies grant metadata and metric access. The collector does not need object-content access (`read objects`), bucket-write access, or access to Logging. Credentials must also be valid; policy statements do not configure or upload an API key.

### Limit access to one compartment tree

For a restricted report, keep the discovery grants at tenancy scope and restrict bucket and metric access to your intended compartment. Replace `Production` with a valid compartment name or path:

```text
Allow group StatsReaders to inspect compartments in tenancy
Allow group StatsReaders to {OBJECTSTORAGE_NAMESPACE_READ} in tenancy
Allow group StatsReaders to read buckets in compartment Production
Allow group StatsReaders to read metrics in compartment Production
```

Compartment IAM grants apply to descendants. Select the same scope in the script to avoid querying unrelated compartments:

```bash
python3 oci_os_stats.py --regions us-ashburn-1 \
  --compartment-id ocid1.compartment.oc1..REPLACE_ME \
  --include-subcompartments --per-compartment --per-bucket
```

Omit `--include-subcompartments` to collect only directly contained buckets. Specifying `--compartment-id` does not bypass the script's initial tenancy-level compartment discovery.

If bucket IDs from `GetBucket` are not needed, `inspect buckets` is sufficient for `ListBuckets`. With `--per-bucket`, a fallback `GetBucket` call can still produce a warning if the bucket ID was not available from Monitoring. Use `read buckets` for the full bucket-report behavior documented here.

### What happens when permissions are missing?

| Failure | Result |
| --- | --- |
| Compartment discovery or namespace lookup fails | The current script stops; these setup calls are not handled as recoverable compartment errors. |
| Bucket listing fails in one compartment | A warning is recorded. Collection continues, including attempts to query that compartment's metrics. |
| `GetBucket` fails | A warning is recorded. A bucket ID unavailable from Monitoring remains missing. |
| A metric query fails | A warning is recorded. Collection continues; affected values are unavailable rather than zero. |
| A compartment is absent from the accessible inventory | It may not appear in the report. Selecting it explicitly fails the inventory check. |

Runs with recorded warnings still produce reports and exit with status `2`. Warnings are printed to stderr and included in JSON. Some inaccessible compartments may be omitted without an individual access-denied warning. A complete tenancy report requires access across the entire intended scope.

The partial-total `*` marker and `metric_coverage` describe known bucket rows only. They cannot detect buckets hidden by missing permissions. An unmarked total or status `0` therefore does not prove tenancy-wide completeness.

### Optional permissions for investigating 404s in Logging

These are separate from running the statistics script. To search enabled bucket access logs, an administrator can grant:

```text
Allow group StatsReaders to read log-groups in tenancy
Allow group StatsReaders to read log-content in tenancy
```

Scope these grants to the compartments containing the **log groups**, which can differ from the bucket compartments, when tenancy-wide log access is unnecessary. They permit viewing/searching existing logs; they do not enable bucket logging. Broad compartment searches that include Audit logs can also require Audit access; select the bucket access-log groups explicitly to avoid including Audit.

**Region discovery permission is not required:** the collector no longer calls `ListRegionSubscriptions`, so do not add `inspect tenancies in tenancy` solely to run this script. Existing unrelated grants can remain in place.

## All command-line options

| Option | Default / behavior |
| --- | --- |
| `--profile NAME` | OCI API-key profile; default `DEFAULT`. |
| `--config-file PATH` | OCI configuration file; default `~/.oci/config`. |
| `--regions REGION [REGION ...]` | **Required.** Collect only from the regions explicitly listed; no default or subscription discovery. |
| `--compartment-id OCID` | Default: all accessible active compartments, including root. |
| `--include-subcompartments` | Include descendants; requires `--compartment-id`. |
| `--per-compartment` | Add compartment statistics. |
| `--per-bucket` | Add regional bucket statistics and bucket IDs. |
| `--rollup-compartments` | Include collected descendants in compartment totals; requires `--per-compartment`. |
| `--start TIMESTAMP` | Inclusive, minute-aligned start. |
| `--end TIMESTAMP` | Exclusive end; defaults to five minutes ago, rounded down to a minute. |
| `--hours NUMBER` | Duration when start is omitted; default `1`. |
| `--json` | Print JSON instead of terminal tables. |
| `--csv FILE` | Also export selected rows to CSV. |
| `--debug` | Print metric definitions, dimensions, and query diagnostics to stderr. |
| `-h`, `--help` | Show usage. |

## Troubleshooting

```bash
python3 oci_os_stats.py --regions us-ashburn-1 --hours 24 --per-bucket --debug
```

- **No request data:** check the UTC window, selected regions, bucket compartments, available metric definitions, and IAM permissions.
- **Missing operation or error metrics:** inspect debug output. Leave missing values as unknown rather than replacing them with zero.
- **Missing bucket ID:** the script uses Monitoring's `resourceID`, falling back to `GetBucket` when bucket output is requested. Check bucket read permissions.
- **Partial results:** inspect stderr and the JSON `warnings` field. The collector handles selected API failures as warnings; other failures stop execution.
- **Large tenancy:** collection is sequential and queries eleven metrics for each region/compartment pair. Narrow the scope if needed; service query limits still apply.

Exit status `0` means collection completed without recorded warnings; it does not certify metric completeness. Status `2` means the script produced results with API/data warnings. Fatal errors stop the run with a nonzero status, and Ctrl+C exits with `130`.

## References

- [Object Storage metrics](https://docs.oracle.com/en-us/iaas/Content/Object/Reference/objectstoragemetrics.htm)
- [OCI Monitoring Python SDK](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/monitoring/client/oci.monitoring.MonitoringClient.html)
- [Monitoring query time ranges](https://docs.oracle.com/en-us/iaas/Content/Monitoring/Tasks/query-metric-time-range.htm)
- [Object Storage access-log fields](https://docs.oracle.com/en-us/iaas/Content/Logging/Reference/details_for_object_storage.htm)
- [Logging search](https://docs.oracle.com/en-us/iaas/Content/Logging/Concepts/searchinglogs.htm)
- [OCI Logging pricing](https://www.oracle.com/manageability/pricing/)
- [IAM permissions for compartments and tenancy discovery](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/iampolicyreference.htm)
- [Object Storage IAM permission reference](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/objectstoragepolicyreference.htm)
- [Monitoring IAM permission reference](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/monitoringpolicyreference.htm)
