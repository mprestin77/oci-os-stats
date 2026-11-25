#!/usr/bin/env python3
"""Read-only OCI Object Storage statistics. Python 3.9+; pip install oci.

Examples:
  python oci_os_stats.py --per-compartment --per-bucket
  python oci_os_stats.py --compartment-id OCID --include-subcompartments --per-bucket
  python oci_os_stats.py --regions us-ashburn-1 --per-bucket --json
  python oci_os_stats.py --per-compartment --per-bucket --csv stats.csv

Uses ~/.oci/config (API-key authentication). Defaults: all accessible active
compartments, subscribed regions, last complete hour ending 5 minutes ago.
Requests/sec is a window average, not peak IOPS. Missing metrics remain null.
Compartment names use parent/child paths. Measured zeros display as 0.
Buckets starting with oci-logs._flowlogs are excluded from reports and totals.
No logging configuration is changed. No byte-throughput metric is assumed.

Read permissions needed across your selected scope (replace StatsReaders):
  Allow group StatsReaders to inspect compartments in tenancy
  Allow group StatsReaders to read buckets in tenancy
  Allow group StatsReaders to read objectstorage-namespaces in tenancy
  Allow group StatsReaders to read metrics in tenancy

Averages use the whole selected wall-clock window. Absent streams are N/A;
partial totals sum known bucket values and are marked with an asterisk.
missing time samples in an existing stream cannot be distinguished from silence.
ClientErrors reports all HTTP 4xx responses combined. Access logging is not
required; no Logging API calls are made.
Exit codes: 0 collection completed, 2 partial result with API warnings.
Credentials/configuration and other unrecoverable errors stop the run.
--json includes counts for each available operation; operation counters may
overlap, so AllRequests is queried independently and never computed from them.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import math
import sys

METRICS = ('AllRequests', 'ClientErrors', 'GetRequests', 'HeadRequests',
           'ListRequests', 'PutRequests', 'PostRequests', 'DeleteRequests',
           'CopyRequests', 'RenameRequests', 'ArchiveRequests')


def parse_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError('Timestamp must include Z or a UTC offset')
    return result.astimezone(timezone.utc)


def arguments():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--profile', default='DEFAULT')
    p.add_argument('--config-file', default='~/.oci/config')
    p.add_argument('--regions', nargs='+', help='Default: every subscribed region')
    p.add_argument('--compartment-id', help='Default: all active accessible compartments')
    p.add_argument('--include-subcompartments', action='store_true', help='Include descendants of --compartment-id in collection')
    p.add_argument('--per-compartment', action='store_true', help='Print direct totals per compartment')
    p.add_argument('--per-bucket', action='store_true', help='Print totals per regional bucket')
    p.add_argument('--rollup-compartments', action='store_true', help='Compartment rows include collected descendants; rows overlap')
    p.add_argument('--start', type=parse_time, help='Inclusive RFC3339 timestamp, aligned to a minute')
    p.add_argument('--end', type=parse_time, help='Exclusive RFC3339 timestamp, aligned to a minute')
    p.add_argument('--hours', type=float, default=1, help='Window length when --start is omitted (default: 1)')
    p.add_argument('--debug', action='store_true', help='Print available metric definitions and query diagnostics to stderr')
    p.add_argument('--csv', metavar='FILE', help='Also export the selected report rows to a CSV file')
    p.add_argument('--json', action='store_true', help='Write machine-readable JSON to stdout')
    a = p.parse_args()
    if a.include_subcompartments and not a.compartment_id:
        p.error('--include-subcompartments requires --compartment-id; without a scope, all compartments are collected')
    if a.rollup_compartments and not a.per_compartment:
        p.error('--rollup-compartments requires --per-compartment')
    if not math.isfinite(a.hours) or a.hours <= 0:
        p.error('--hours must be positive and finite')
    a.end = a.end or (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(second=0, microsecond=0)
    a.start = a.start or a.end - timedelta(minutes=round(a.hours * 60))
    if a.start >= a.end or a.end - a.start > timedelta(days=7):
        p.error('Window must be positive and no longer than 7 days')
    if any(t.second or t.microsecond for t in (a.start, a.end)):
        p.error('--start and --end must be aligned to whole minutes')
    return a


def descendants(root, parents):
    found = {root}
    while True:
        expanded = found | {cid for cid, parent in parents.items() if parent in found}
        if expanded == found:
            return found
        found = expanded


def compartment_paths(tenant, names, parents):
    """Build paths from tenancy root, omitting the root label for children."""
    paths = {tenant: names[tenant]}
    for cid in names:
        if cid == tenant:
            continue
        parts = []
        current = cid
        seen = set()
        while current and current != tenant:
            if current in seen:
                parts.append('[cycle]')
                break
            seen.add(current)
            # Preserve an unavailable ancestor's OCID rather than invent a name.
            parts.append(names.get(current, current))
            current = parents.get(current)
        paths[cid] = '/'.join(reversed(parts))
    return paths


def summarize(rows, seconds):
    """Sum observed values; expose incomplete coverage alongside each metric."""
    metrics = {}
    coverage = {}
    for name in METRICS:
        values = [r['metrics'].get(name) for r in rows]
        known = [v for v in values if v is not None]
        metrics[name] = sum(known) if known else None
        coverage[name] = {'with_data': len(known), 'bucket_rows': len(rows)}
    return {'bucket_rows': len(rows), 'metrics': metrics,
            'requests_per_second': None if metrics['AllRequests'] is None else metrics['AllRequests'] / seconds,
            'throughput_bytes_per_second': None,
            'metric_coverage': coverage}


def report_rows(buckets, compartments, parents, selected, seconds, rollup):
    result = []
    for cid in sorted(selected, key=lambda c: (compartments[c], c)):
        scope = descendants(cid, parents) if rollup else {cid}
        rows = [r for r in buckets if r['compartment_id'] in scope]
        result.append({'compartment_id': cid, 'compartment': compartments[cid],
                       'includes_descendants': rollup, **summarize(rows, seconds)})
    return result


def format_metric(row, value, metric, rate=False):
    if value is None:
        return 'N/A'
    coverage = row['metric_coverage'][metric]
    partial = coverage['with_data'] < coverage['bucket_rows']
    if value == 0:
        text = '0'
    elif rate and 0 < value < 1:
        text = '<1'
    else:
        text = f'{value:,.0f}'
    return text + ('*' if partial else '')


def write_csv(path, report):
    """Export the same selected rows and display values as the table."""
    fields = ['scope', 'region', 'compartment', 'compartment_id', 'bucket',
              'bucket_id', 'requests', 'avg_requests_per_second', 'total_4xx',
              'request_buckets_with_data', 'error_buckets_with_data', 'bucket_rows',
              'start_utc', 'end_utc']
    rows = [('total', report['summary'])]
    rows.extend(('compartment', row) for row in report.get('compartments', []))
    rows.extend(('bucket', row) for row in report.get('buckets', []))
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scope, row in rows:
            writer.writerow({
                'scope': scope, 'region': row.get('region', '*'),
                'compartment': row.get('compartment', '*'),
                'compartment_id': row.get('compartment_id', ''),
                'bucket': row.get('bucket', '*'),
                'bucket_id': row.get('bucket_id') or '',
                'requests': format_metric(row, row['metrics']['AllRequests'], 'AllRequests'),
                'avg_requests_per_second': format_metric(row, row['requests_per_second'], 'AllRequests', rate=True),
                'total_4xx': format_metric(row, row['metrics']['ClientErrors'], 'ClientErrors'),
                'request_buckets_with_data': row['metric_coverage']['AllRequests']['with_data'],
                'error_buckets_with_data': row['metric_coverage']['ClientErrors']['with_data'],
                'bucket_rows': row['bucket_rows'],
                'start_utc': report['start'], 'end_utc': report['end'],
            })


def print_table(title, rows, show_bucket_id=False):
    print('\n' + title)
    print('REGION\tCOMPARTMENT\tBUCKET' + ('\tBUCKET_ID' if show_bucket_id else '') + '\tREQUESTS\tAVG_REQ/S\t4XX')
    for r in rows:
        cells = [r.get('region', '*'), r.get('compartment', '*'), r.get('bucket', '*')]
        if show_bucket_id:
            cells.append(r.get('bucket_id') or 'N/A')
        cells.extend([format_metric(r, r['metrics']['AllRequests'], 'AllRequests'),
                      format_metric(r, r['requests_per_second'], 'AllRequests', rate=True),
                      format_metric(r, r['metrics']['ClientErrors'], 'ClientErrors')])
        print('\t'.join(cells))


def main():
    a = arguments()
    try:
        import oci
    except ImportError:
        sys.exit('Install the OCI SDK first: python -m pip install oci')
    config = oci.config.from_file(a.config_file, a.profile)
    tenant = config['tenancy']
    warnings = []

    def warn(message):
        warnings.append(message)
        print('WARNING: ' + message, file=sys.stderr)

    def client(cls, region=None):
        return cls(dict(config, **({'region': region} if region else {})),
                   retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)

    def all_items(method, *args, **kwargs):
        return oci.pagination.list_call_get_all_results(method, *args, **kwargs).data

    def debug(message):
        if a.debug:
            print('DEBUG: ' + message, file=sys.stderr)

    debug(f'UTC window {a.start.isoformat()} to {a.end.isoformat()}')
    identity = client(oci.identity.IdentityClient)
    # Listing at tenancy level prevents assuming that a parent query is recursive.
    comps = all_items(identity.list_compartments, tenant, compartment_id_in_subtree=True,
                      access_level='ACCESSIBLE')
    names = {tenant: '(root)'}
    parents = {}
    for c in comps:
        if c.lifecycle_state == 'ACTIVE':
            names[c.id] = c.name
            parents[c.id] = c.compartment_id
    names = compartment_paths(tenant, names, parents)
    if a.compartment_id and a.compartment_id not in names:
        sys.exit('Selected compartment is not in the accessible active compartment inventory')
    selected = set(names)
    if a.compartment_id:
        selected = descendants(a.compartment_id, parents) if a.include_subcompartments else {a.compartment_id}
    regions = a.regions or [r.region_name for r in identity.list_region_subscriptions(tenant).data
                            if r.status == 'READY']
    if not regions:
        sys.exit('No regions selected')
    records = {}

    def bucket(region, cid, bid, bname):
        # ListBuckets returns a name but no OCID. Start with a provisional
        # name key; promote it to the Monitoring resourceID when available.
        key = (region, cid, 'id', bid) if bid else (region, cid, 'name', bname)
        provisional = (region, cid, 'name', bname)
        if key not in records:
            if bid and provisional in records:
                records[key] = records.pop(provisional)
                records[key]['bucket_id'] = bid
            else:
                records[key] = {'region': region, 'compartment_id': cid,
                                'compartment': names.get(cid, cid), 'bucket_id': bid,
                                'bucket': bname, 'metrics': {}}
        return records[key]

    for region in dict.fromkeys(regions):
        print('Collecting ' + region, file=sys.stderr)
        monitor = client(oci.monitoring.MonitoringClient, region)
        storage = client(oci.object_storage.ObjectStorageClient, region)
        namespace = storage.get_namespace(compartment_id=tenant).data
        for cid in sorted(selected):
            try:
                inventory = all_items(storage.list_buckets, namespace, cid)
                for item in inventory:
                    bucket(region, cid, None, item.name)
            except oci.exceptions.ServiceError as exc:
                warn(f'{region}/{cid}: bucket inventory failed ({exc.code}); scope incomplete')
            if a.debug:
                try:
                    definitions = all_items(monitor.list_metrics, cid,
                        oci.monitoring.models.ListMetricsDetails(namespace='oci_objectstorage'))
                    available = sorted({(d.name, str(d.resource_group)) for d in definitions})
                    debug(f'{region}/{names[cid]} available metric names/resource groups: {available}')
                    for definition in definitions[:3]:
                        debug(f'{region}/{names[cid]} definition dimensions: {definition.dimensions}')
                except oci.exceptions.ServiceError as exc:
                    warn(f'{region}/{cid}: metric definition discovery failed ({exc.code})')
            for metric in METRICS:
                try:
                    details = oci.monitoring.models.SummarizeMetricsDataDetails(
                        namespace='oci_objectstorage', query=f'{metric}[1m].sum()',
                        start_time=a.start, end_time=a.end, resolution='1m')
                    streams = monitor.summarize_metrics_data(cid, details).data
                    point_count = sum(len(stream.aggregated_datapoints) for stream in streams)
                    debug(f'{region}/{names[cid]}/{metric}: streams={len(streams)}, datapoints={point_count}')
                    # Multiple streams with the same bucket identity are summed.
                    for stream in streams:
                        dims = stream.dimensions
                        stamps = [p.timestamp for p in stream.aggregated_datapoints]
                        debug(f'{region}/{names[cid]}/{metric}: dimensions={dims}, '
                              f'first={min(stamps) if stamps else None}, last={max(stamps) if stamps else None}')
                        bid = dims.get('resourceID') or dims.get('resourceId')
                        if not bid:
                            warn(f'{region}/{cid}/{metric}: stream has no bucket resourceID; skipped')
                            continue
                        points = [p.value for p in stream.aggregated_datapoints
                                  if a.start <= p.timestamp < a.end and p.value is not None]
                        if points:
                            row = bucket(region, cid, bid, dims.get('resourceDisplayName', bid))
                            row['metrics'][metric] = row['metrics'].get(metric, 0) + sum(points)
                except oci.exceptions.ServiceError as exc:
                    warn(f'{region}/{cid}/{metric}: HTTP {exc.status} {exc.code}; metric unavailable')
        # BucketSummary omits OCIDs. Fill remaining IDs from GetBucket only
        # when bucket output is requested; Monitoring IDs require no extra call.
        if a.per_bucket:
            for row in records.values():
                if (row['region'] != region or row['bucket_id'] is not None
                        or row['bucket'].startswith('oci-logs._flowlogs')):
                    continue
                try:
                    row['bucket_id'] = storage.get_bucket(namespace, row['bucket']).data.id
                except oci.exceptions.ServiceError as exc:
                    warn(f"{region}/{row['compartment']}/{row['bucket']}: "
                         f"cannot retrieve bucket ID ({exc.code}); read buckets permission may be required")
    seconds = (a.end - a.start).total_seconds()
    buckets = []
    for row in records.values():
        if row['bucket'].startswith('oci-logs._flowlogs'):
            continue
        buckets.append({**row, **summarize([row], seconds)})
    buckets.sort(key=lambda r: (r['region'], r['compartment'], r['bucket']))
    summary = summarize(buckets, seconds)
    if summary['metrics']['AllRequests'] is None:
        warn('No usable AllRequests datapoints returned for this window and scope. '
             'Try --hours 24 --debug; check UTC window, region, metric definitions, and API permissions.')
    comp_rows = report_rows(buckets, names, parents, selected, seconds, a.rollup_compartments)
    output = {'start': a.start.isoformat(), 'end': a.end.isoformat(),
              'regions': regions, 'selected_compartment_ids': sorted(selected),
              'summary': summary,
              'notes': ['Buckets with the oci-logs._flowlogs prefix are excluded from rows and totals.',
                        'Rows include inventoried buckets and buckets present in returned metrics.',
                        'Bucket IDs use Monitoring or GetBucket; null means no ID could be retrieved.',
                        '* marks a partial total: only buckets with returned data are summed; see metric_coverage in JSON.',
                        'Missing metrics are null, not zero. Coverage counts refer to inventoried or observed bucket rows.',
                        'Printed values are rounded to whole numbers; nonzero rates below 1 show <1. JSON retains precision.',
                        'Rates are window averages. Object Storage requests are not block-volume IOPS.',
                        'Throughput is unavailable: no byte-transfer counter is assumed.',
                        'Compartment rollups overlap; do not sum rolled-up rows.'],
              'warnings': warnings}
    if a.per_bucket:
        output['buckets'] = buckets
    if a.per_compartment:
        output['compartments'] = comp_rows
    if a.csv:
        write_csv(a.csv, output)
        print(f'CSV written to {a.csv}', file=sys.stderr)
    if a.json:
        print(json.dumps(output, indent=2))
    else:
        print(f'Window: {a.start.isoformat()} to {a.end.isoformat()} (end exclusive)')
        print_table('TOTAL FOR SELECTED SCOPE', [summary])
        if a.per_compartment:
            print_table('COMPARTMENTS' + (' (DESCENDANT ROLLUPS)' if a.rollup_compartments else ' (DIRECT)'), comp_rows)
        if a.per_bucket:
            print_table('BUCKETS', buckets, show_bucket_id=True)
        for note in output['notes']:
            print('Note: ' + note)
        print('Use --json for operation counts, bucket IDs, and metric coverage.')
    return 2 if warnings else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
