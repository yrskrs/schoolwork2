#!/usr/bin/env python3
"""Advance a locally built image tag without evaluating or exposing .env."""
import ast
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

project=sys.argv[1]
key,path,constant,default={
 'schooltest5':('SCHOOLTEST_IMAGE','app/version.py','__version__','schooltest5-app'),
 'schoolwork2':('SCHOOLWORK_IMAGE','feed/changelog.py','SITE_VERSION','schoolwork2-app'),
}[project]
version=next(ast.literal_eval(node.value) for node in ast.parse(Path(path).read_text()).body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id==constant for target in node.targets))
assert re.fullmatch(r'\d+\.\d+\.\d+',version),'Invalid release version'
p=Path('.env');text=p.read_text()
matches=list(re.finditer(r'(?m)^'+key+r'=(.*)$',text))
values=shlex.split(matches[-1].group(1),comments=True) if matches else []
previous=values[0] if values else ''
if '@' in previous:raise SystemExit('A digest-pinned image cannot be replaced by a local build; configure a buildable image tag first.')
name=previous or default
if ':' in name.rsplit('/',1)[-1]:name=name.rsplit(':',1)[0]
image=name+':'+version
replacement=key+'='+image
updated=re.sub(r'(?m)^'+key+r'=.*$',replacement,text) if matches else text.rstrip('\n')+'\n'+replacement+'\n'
if updated!=text:
 backup=Path('.env.before-image-update');backup.write_text(text);backup.chmod(0o600)
 with tempfile.NamedTemporaryFile(mode='w',dir='.',prefix='.env.image-',delete=False) as stream:stream.write(updated);temporary=Path(stream.name)
 temporary.chmod(0o600);os.replace(temporary,p)
print(image)
