"""Exercise the delivered archive, including real missing/corrupt media.

Only changes media inside a fresh verification extraction; every mutation is
restored in finally. No source checkout or deployment is touched.
"""
from pathlib import Path
import argparse,hashlib,json,shutil,sys,zipfile
import verify_clean_export as V

ap=argparse.ArgumentParser();ap.add_argument('package',type=Path);ap.add_argument('--archive',type=Path,required=True)
a=ap.parse_args();root=a.package.resolve();archive=a.archive.resolve()
assert root.is_relative_to(Path(__file__).resolve().parents[1]/'workspace/submission')
if archive.exists():raise SystemExit('Refuse to overwrite archive')
archive.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts or '.pytest_cache' in p.parts:continue
        z.write(p,p.relative_to(root).as_posix())
extracted=archive.parent/(archive.stem+'-unpacked-check')
if extracted.exists():raise SystemExit('Refuse to overwrite verification directory')
extracted.mkdir()
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for n in z.namelist():
        assert (extracted/n).resolve().is_relative_to(extracted)
    z.extractall(extracted)
normal=V.verify(extracted)
negative=[]
for rel in [V.MEDIA[0],V.MEDIA[2]]:
    p=extracted/rel;original_hash=V.sha(p)
    moved=p.with_suffix(p.suffix+'.removed-test')
    assert not moved.exists()
    p.rename(moved)
    try:
        r=V.verify(extracted,skip_tests=True)
        passed=(not r['passed'] and any('missing' in x and rel in x for x in r['problems']))
        negative.append({'case':'missing','asset':rel,'correctly_rejected':passed})
    finally:moved.rename(p)
    with p.open('r+b') as f:
        first=f.read(1);f.seek(0);f.write(bytes([first[0]^1]))
    try:
        r=V.verify(extracted,skip_tests=True)
        passed=(not r['passed'] and any('mismatch' in x and rel in x for x in r['problems']))
        negative.append({'case':'corrupted','asset':rel,'correctly_rejected':passed})
    finally:
        with p.open('r+b') as f:f.write(first)
    assert V.sha(p)==original_hash
restored=V.verify(extracted,skip_tests=True)
report={'archive':archive.name,'archive_sha256':V.sha(archive),'archive_bytes':archive.stat().st_size,
        'clean_extraction':normal,'media_negative_tests':negative,'restored_integrity':restored,
        'passed':normal['passed'] and restored['passed'] and all(x['correctly_rejected'] for x in negative)}
dest=archive.parent/(archive.stem+'-acceptance.json')
dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'archive':str(archive),'passed':report['passed'],
    'test_summary':(normal.get('public_suite') or {}).get('stdout','').strip().splitlines()[-1:],
    'json_value_assertions':normal['json_value_assertions'],'media_negative_tests':negative,
    'archive_sha256':report['archive_sha256'],'archive_bytes':report['archive_bytes']},ensure_ascii=False,indent=2))
raise SystemExit(0 if report['passed'] else 1)
