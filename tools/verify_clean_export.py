"""Verify shipped hashes/media/evidence and the public offline suite.

Hashes protect integrity against this manifest, not trusted-signature identity.
Private/GPU integrations are outside this declared offline acceptance suite.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,sys
MEDIA=('workspace/video/aegis-final-demo-20260929.mp4',
       'workspace/video/skills-live/aegis-skills-real-execution-20260929.mp4',
       'workspace/ppt-final/aegis-dgx-spark-judge-pitch-rsi-20260929.pptx')
DOC_ASSETS=('docs/assets/aegis-hero.svg','docs/assets/demo-console.png')
REQUIRED=('README.md','RELEASE.md','LICENSE','SECURITY-AND-USE.md','THIRD-PARTY.md',
          'REVIEW-README.md','REVIEW-PACKET.md','docs/PROJECT-DOC-20260928.md',
          'docs/ten-days.md','tests/test_submission_quality.py','bench/train/train_lora.py',
          *DOC_ASSETS)
EVIDENCE_NAMES=('alerts.json','artifacts.json','audit-log.jsonl','events.jsonl',
 'evidence-receipt.json','findings.json','flow.jsonl','judge.jsonl','learning-candidates.jsonl',
 'run.json','runtime-attestation.json','summary.json','verification.json',
 'patches/F-001.candidate.php','patches/F-001.original.php','patches/F-001.patch')
PUBLIC_TESTS=['tests/test_hunt.py','tests/test_jevtrain.py','tests/test_learning_queue.py',
 'tests/test_submission_quality.py','skills/aegis-hunt/tests','skills/aegis-evolve/tests',
 'skills/aegis-jevtrain/tests','skills/aegis-self-repair/tests']

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()

def identity_patterns():
    """Generic paths plus optional private markers, without deployment identities.

    Split literal path prefixes so export-time text scrubbing cannot modify the
    regex source. AEGIS_EXPORT_PRIVATE_MARKERS accepts a JSON list of strings;
    marker values are supplied by an operator and never printed in diagnostics.
    """
    patterns=[r'[A-Za-z]:[\\/]+Users[\\/]+(?!<)[A-Za-z0-9_.-]+',
              r'/[a-z]/'+r'Users/(?!<)[A-Za-z0-9_.-]+',
              r'/'+r'home/(?!<)[A-Za-z0-9_.-]+']
    user=os.environ.get('USERNAME','')
    if user:patterns.append(r'\b'+re.escape(user)+r'\b')
    encoded=os.environ.get('AEGIS_EXPORT_PRIVATE_MARKERS','')
    if encoded:
        try:markers=json.loads(encoded)
        except ValueError:raise ValueError('private marker setting must be a JSON list') from None
        if not isinstance(markers,list) or any(not isinstance(m,str) or not m for m in markers):
            raise ValueError('private marker setting must contain non-empty strings')
        patterns.extend(re.escape(marker) for marker in markers)
    return [re.compile(pattern,re.I) for pattern in patterns]

def shipped_files(root):
    """Ignore installed root environments/Git and generated Python caches only."""
    for directory,dirs,names in os.walk(root):
        here=Path(directory)
        dirs[:]=[name for name in dirs if name not in ('__pycache__','.pytest_cache')
                 and (here!=root or name not in ('.git','.venv'))]
        for name in names:yield here/name

def number_assertions(root):
    """Check direct JSON claims/ties; do not pretend to check omitted raw data."""
    import yaml
    count=0;problems=[]
    def selected(doc,selector):
        for part in re.findall(r'[^.\[]+|\[\d+\]',selector):
            doc=doc[int(part[1:-1])] if part.startswith('[') else doc[part]
        return doc
    try:claims=yaml.safe_load((root/'claims.yaml').read_text(encoding='utf-8'))
    except (OSError,ValueError):return 0,['claims file invalid']
    for claim in claims:
        if claim.get('kind')!='json':continue
        entries=[claim]+([claim['tie']] if claim.get('tie') else [])
        for entry in entries:
            try:
                got=selected(json.loads((root/entry['file']).read_text(encoding='utf-8')),entry['selector'])
                want=entry['expected']
                equal=(round(float(got),max(len(str(want).split('.')[-1]),1))==want
                       if isinstance(want,float) and isinstance(got,(int,float)) else got==want)
                count+=1
                if not equal:problems.append('claim value mismatch: '+claim['claim_id'])
            except (OSError,ValueError,KeyError,TypeError,IndexError):problems.append('unreadable claim: '+claim['claim_id'])
    return count,problems

def check(root):
    problems=[]
    try:
        manifest=json.loads((root/'FINAL-BUILD-MANIFEST.json').read_text(encoding='utf-8'))
        files=manifest['files']
        if not isinstance(files,dict) or not files:raise ValueError('empty inventory')
    except (OSError,ValueError,KeyError):return ['missing/invalid full hash manifest'],{}
    for rel in (*REQUIRED,*MEDIA):
        if not (root/rel).is_file():problems.append('missing required asset: '+rel)
        if rel not in files:problems.append('asset absent from manifest: '+rel)
    for rel,entry in files.items():
        p=(root/rel).resolve()
        if not p.is_relative_to(root):problems.append('outside inventory path: '+rel);continue
        if not p.is_file():problems.append('missing file: '+rel);continue
        if p.stat().st_size!=entry.get('bytes') or sha(p)!=entry.get('sha256'):
            problems.append('hash/size mismatch: '+rel)
    actual={p.relative_to(root).as_posix() for p in shipped_files(root) if p.is_file()
            and p.name not in ('FINAL-BUILD-MANIFEST.json','VALIDATION.json')}
    if actual-set(files):problems.append('unlisted files: '+', '.join(sorted(actual-set(files))[:8]))
    ev=root/'evidence/main-run-run-20260927-140612-399b'
    for rel in EVIDENCE_NAMES:
        if not (ev/rel).is_file():problems.append('missing main-run evidence: '+rel)
    for rel in manifest.get('claims_result_files',[]):
        if not (root/rel).is_file():problems.append('missing claimed result: '+rel)
    try:pats=identity_patterns()
    except ValueError as exc:return sorted(set(problems+[str(exc)])),manifest
    for p in shipped_files(root):
        if (not p.is_file()
                or p.suffix not in {'.md','.py','.json','.jsonl','.yaml','.yml','.txt','.html','.sh','.js','.mjs','.php','.patch','.svg'}
                or p.name=='verify_clean_export.py'):continue
        txt=p.read_text(encoding='utf-8',errors='replace')
        if any(pat.search(txt) for pat in pats):problems.append('unscrubbed host identity: '+p.relative_to(root).as_posix())
        if re.search(r'(?i)sk-[A-Za-z0-9_-]{24,}',txt):problems.append('possible API secret: '+p.relative_to(root).as_posix())
    return sorted(set(problems)),manifest

def verify(root,skip_tests=False):
    root=root.resolve();problems,manifest=check(root)
    count,number_problems=number_assertions(root)
    problems.extend(number_problems)
    report={'artifact_checks':not problems,'problems':problems,'public_suite':None,
            'json_value_assertions':count,
            'scope':'public offline suite and direct JSON claims; omitted raw/computed/private/GPU assertions are separate'}
    if not problems and not skip_tests:
        env={k:v for k,v in os.environ.items() if not k.startswith('AEGIS_') and k not in ('PYTHONPATH','PYTHONHOME')}
        env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        p=subprocess.run([sys.executable,'-m','pytest',*PUBLIC_TESTS,'-q','-p','no:cacheprovider','--import-mode=importlib'],
            cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
        report['public_suite']={'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
        if p.returncode:report['problems'].append('public suite failed')
    report['passed']=not report['problems'];return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);ap.add_argument('--skip-tests',action='store_true')
    args=ap.parse_args();result=verify(args.root,args.skip_tests)
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['passed'] else 1)
