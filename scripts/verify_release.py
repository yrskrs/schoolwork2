"""Fail if web/worker differ or their runtime files do not match this checkout."""
import json
import subprocess
from source_fingerprint import source_fingerprint


def capture(args):
    return subprocess.check_output(args,text=True).strip()


def main():
    containers=capture(['docker','compose','ps','-q','app','ai_worker']).splitlines()
    if len(containers)!=2:
        raise SystemExit('Web and worker must both be running.')
    images={capture(['docker','inspect','--format','{{.Image}}',item]) for item in containers}
    if len(images)!=1:
        raise SystemExit('Web and worker use different image IDs.')
    expected=source_fingerprint()
    for item in containers:
        actual=capture(['docker','exec',item,'python','scripts/source_fingerprint.py'])
        if actual!=expected:
            raise SystemExit('A running service does not match the current source fingerprint.')
    print('Verified: web and worker use one image and match the current source.')


if __name__=='__main__':main()
