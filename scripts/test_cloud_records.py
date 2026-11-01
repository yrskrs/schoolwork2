import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from cloud_backup.records import RecordEntries,add_records,read_records


def capture(rows,key='id'):
    entries=RecordEntries();add_records(entries,'table',rows,key)
    values={}
    for name,source in entries:
        with source() as f:values[name]=f.read()
    return entries,values


class RecordBucketTests(unittest.TestCase):
    def test_thousands_of_records_form_small_stable_buckets(self):
        entries,files=capture([{'id':i,'value':'same'} for i in range(16000)])
        self.assertEqual(entries.record_count,16000)
        self.assertEqual(len(files),125)
        self.assertEqual(len(json.loads(next(iter(files.values())))),128)

    def test_insert_and_update_leave_other_buckets_unchanged(self):
        rows=[{'id':i,'value':str(i)} for i in range(256)]
        _,before=capture(rows)
        _,inserted=capture(rows+[{'id':256,'value':'new'}])
        self.assertTrue(all(inserted[name]==value for name,value in before.items()))
        rows[5]['value']='changed'
        _,changed=capture(rows)
        self.assertEqual(sum(before[name]!=value for name,value in changed.items()),1)

    def test_reader_accepts_legacy_records_and_new_buckets_with_dates(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);folder=root/'db'/'table';folder.mkdir(parents=True)
            (folder/'legacy.json').write_text(json.dumps({'id':1000}))
            _,files=capture([{'id':1,'date':datetime(2026,11,1)}])
            for name,value in files.items():(root/name).write_bytes(value)
            rows=read_records(folder)
            self.assertEqual({row['id'] for row in rows},{1,1000})
            self.assertEqual(next(row for row in rows if row['id']==1)['date'],'2026-11-01T00:00:00')

    def test_string_identifiers_are_stable_and_invalid_records_rejected(self):
        rows=[{'ref':str(i),'value':'record'} for i in range(100)]
        _,first=capture(rows,'ref');_,second=capture(list(reversed(rows)),'ref')
        self.assertEqual(first,second)
        with tempfile.TemporaryDirectory() as work:
            (Path(work)/'invalid.json').write_text('[1,2]')
            with self.assertRaises(ValueError):read_records(work)

if __name__=='__main__':unittest.main()
