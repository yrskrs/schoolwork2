"""Synthetic cloud audit: run with venv/bin/python from the repository.

No private configuration or real account data is loaded. All provider responses
are mocked. Prints backend observations and writes an isolated browser fixture to
/tmp/schoolnet-cloud-probe-20261019.html. Browser execution must use a separate
Chrome profile: this fixture clears its own localStorage and mocks fetch().
"""
import json
import re
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if (ROOT / 'VERSION').read_text().strip() != '4.5.8':
    print('Historical probe for 4.5.8. For the fixed release run: venv/bin/python scripts/test_cloud_import_ui.py')
    raise SystemExit(0)
from django.conf import settings
settings.configure(USE_TZ=True, GOOGLE_DRIVE_API_KEY='')
from django.template import Context, Engine
from feed.cloud_storage import GoogleDriveClient, NextcloudClient

results = {}
with patch('feed.cloud_storage.imaplib.IMAP4_SSL', side_effect=TimeoutError('synthetic')):
    results['gmail_timeout_reported_success'] = GoogleDriveClient(email='synthetic@example.invalid', password='synthetic-only').test_connection()[0]
with patch('feed.cloud_storage.urllib.request.urlopen') as network:
    results['unreachable_folder_test'] = {
        'success': GoogleDriveClient(folder_link='https://drive.google.com/drive/folders/SyntheticMissingFolder123').test_connection()[0],
        'network_calls': network.call_count,
    }
with patch('feed.cloud_storage.urllib.request.urlopen', side_effect=HTTPError('https://www.googleapis.com/drive/v3/files', 503, 'synthetic unavailable', {}, None)):
    result = GoogleDriveClient(access_token='synthetic-only').list_directory()
    results['api_503_hidden'] = {'items': len(result['items']), 'contains_error': 'error' in result}
response = MagicMock()
response.__enter__.return_value = response
response.read.return_value = json.dumps({'files': [{'id':str(i), 'name':f'file{i}.txt','mimeType':'text/plain'} for i in range(100)],'nextPageToken':'synthetic-page-2'}).encode()
with patch('feed.cloud_storage.urllib.request.urlopen', return_value=response) as network:
    result = GoogleDriveClient(access_token='synthetic-only').list_directory()
    results['pagination'] = {'items':len(result['items']), 'network_calls':network.call_count, 'next_page_exposed':'nextPageToken' in result}
response.read.return_value = b'synthetic file body'
response.headers = {'Content-Type':'text/plain'}
with patch('feed.cloud_storage.urllib.request.urlopen', return_value=response) as network:
    NextcloudClient('https://cloud.example.invalid', 'synthetic', 'synthetic').download_file('test.txt')
    results['nextcloud_read_is_unbounded'] = response.read.call_args.args == ()
results['tls_disabled_by_path_substring'] = NextcloudClient('https://cloud.example.invalid/localhost', 'synthetic', 'synthetic', ignore_ssl=False)._get_ssl_context().verify_mode == ssl.CERT_NONE
print(json.dumps(results, indent=2))

source = (ROOT / 'templates/feed/components/cloud_picker_modal.html').read_text()
source = re.sub(r"\{% url '([^']+)' %\}", lambda match: '/teacher/cloud/' + ('browse/' if match[1] == 'teacher_cloud_browse' else 'download/'), source)
rendered = Engine().from_string(source).render(Context({'cloud_storage':SimpleNamespace(has_nextcloud=True,has_google_drive=True)}))
script = r'''
const auditResults = {};
let mode = 'normal';
let attached = [];
let requests = [];
window.alert = () => {};
window.addFilesToStore = files => { attached.push(...files.map(f => ({name:f.name,size:f.size}))); };
const entries = [
 {name:'alpha.txt',path:'alpha.txt',is_dir:false,size:12},
 {name:'beta.txt',path:'beta.txt',is_dir:false,size:12}
];
window.fetch = async (url) => {
 requests.push(url);
 const query = new URL(url,'http://synthetic.invalid').searchParams;
 if(url.includes('/download/')) {
   if(mode==='download-switch') await new Promise(r=>setTimeout(r,60));
   return {ok:true,headers:new Headers({'Content-Disposition':'attachment; filename="real-file.pdf"'}),blob:async()=>new Blob(['synthetic file'],{type:'application/pdf'})};
 }
 const drive = query.get('drive');
 if(mode==='race') await new Promise(r=>setTimeout(r,drive==='nextcloud'?100:10));
 if(mode==='xss') return {json:async()=>({success:false,error:'<img src="file:///tmp/nonexistent-schoolnet-audit" onerror="window.auditErrorHtmlExecuted=true">'})};
 return {json:async()=>({success:true,items:drive==='nextcloud'?entries:[{name:'google-only.txt',id:'synthetic-google',is_dir:false,size:12}],current_path:'',current_folder_id:'root',breadcrumbs:[]})};
};
const tick = async (ms=25) => await new Promise(r=>setTimeout(r,ms));
(async()=>{
 localStorage.clear();
 openCloudPickerModal(); await tick();
 document.querySelector('#cloud-files-tbody input').click();
 await attachSelectedCloudFiles();
 auditResults.normal_attach_ok = attached.length===1 && attached[0].name==='alpha.txt';

 attached=[]; openCloudPickerModal(); await tick();
 document.querySelector('#cloud-search-input').value='alpha'; filterCloudFilesList();
 auditResults.filtered_visible_rows=document.querySelectorAll('#cloud-files-tbody tr').length;
 toggleSelectAllCloudFiles(true); await attachSelectedCloudFiles();
 auditResults.filtered_select_all_files=attached.map(f=>f.name);

 mode='race'; openCloudPickerModal(); switchCloudDrive('google'); await tick(160);
 auditResults.race_google_tab_shows=document.querySelector('#cloud-files-tbody').innerText;
 mode='normal'; switchCloudDrive('nextcloud'); await tick(); closeCloudPickerModal();

 mode='xss'; openCloudPickerModal(); await tick(80);
 auditResults.error_html_executes=window.auditErrorHtmlExecuted===true;
 closeCloudPickerModal();

 mode='normal'; openCloudPickerModal(); await tick();
 toggleSelectAllCloudFiles(true); requests=[]; attached=[];
 mode='download-switch'; const download=attachSelectedCloudFiles();
 await tick(10); switchCloudDrive('google'); await download;
 auditResults.download_switch_requests=requests.filter(x=>x.includes('/download/'));
 mode='normal'; openCloudPickerModal(); await tick();
 document.querySelector('#google-direct-link-input').value='https://drive.google.com/file/d/SyntheticFile123456789/view';
 addGoogleDirectLink(); attached=[]; await attachSelectedCloudFiles();
 auditResults.direct_link_filename=attached[0]?.name;
 document.body.innerHTML='<pre id="audit-result"></pre>';
 document.querySelector('#audit-result').textContent=JSON.stringify(auditResults,null,2);
})().catch(e=>{document.body.innerHTML='<pre id="audit-result"></pre>';document.querySelector('#audit-result').textContent=JSON.stringify({harness_error:e.stack});});
'''
html = '<!doctype html><html><head><meta charset="utf-8"></head><body><script>' + script.split('const tick =')[0] + '</script>' + rendered + '<script>const tick =' + script.split('const tick =',1)[1] + '</script></body></html>'
Path('/tmp/schoolnet-cloud-probe-20261019.html').write_text(html)
