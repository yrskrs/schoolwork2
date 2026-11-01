"""Stable small database buckets avoid one cloud request per database row.

Integer PK ranges retain their boundaries after insert/delete; string PKs use a
stable hash prefix. Readers also accept the original one-record JSON layout.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from .files import bytes_source


class RecordEntries(list):
    record_count = 0


def add_records(entries, label, rows, primary_key='id'):
    buckets=defaultdict(list)
    for row in sorted(rows,key=lambda row:str(row[primary_key])):
        key=row[primary_key]
        bucket=f'n{key//128:012d}' if isinstance(key,int) else 'h'+hashlib.sha256(str(key).encode()).hexdigest()[:2]
        buckets[bucket].append(row)
    for bucket,records in sorted(buckets.items()):
        data=json.dumps(records,ensure_ascii=False,sort_keys=True,separators=(',',':'),
                        default=lambda value:value.isoformat()).encode()
        entries.append((f'db/{label}/{bucket}.json',bytes_source(data)))
    entries.record_count+=len(rows)


def read_records(directory, recursive=False):
    paths=Path(directory).rglob('*.json') if recursive else Path(directory).glob('*.json')
    rows=[]
    for path in sorted(paths):
        value=json.loads(path.read_text())
        values=[value] if isinstance(value,dict) else value
        if not isinstance(values,list) or any(not isinstance(row,dict) for row in values):
            raise ValueError('Некоректний формат записів резервної копії')
        rows.extend(values)
    return rows
