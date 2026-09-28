"""Replace the old C repair/material cards with the actual skill recording.

Preserve the original main-run/JEV and 21→3 evidence sections. Two distinct
run IDs remain visible. Final duration stays exactly four minutes.
"""
from pathlib import Path
import subprocess,json,hashlib
OUT=Path(__file__).resolve().parent
FF=Path.home()/'anaconda3/envs/deepseekocr/ffmpeg/bin/ffmpeg.exe'
OLD=OUT.parent/'aegis-dgx-spark-demo-20260927.mp4'
SKILL=OUT/'aegis-skills-real-execution-20260929.mp4'
FINAL=OUT.parent/'aegis-final-demo-20260929.mp4'

def run(args):
    p=subprocess.run([str(FF),'-y','-hide_banner','-loglevel','error',*args],capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr.decode('utf-8',errors='replace'))

clip_duration=44.233333
last_start=222+(clip_duration-44)
pieces=[]
for name,source,start,length in [('intro-main-jev-red.mp4',OLD,0,178),('live-skill.mp4',SKILL,0,clip_duration),('closing.mp4',OLD,last_start,240-last_start)]:
    dest=OUT/name
    run(['-ss',str(start),'-i',str(source),'-t',str(length),'-vf','fps=25,format=yuv420p',
         '-c:v','libx264','-preset','veryfast','-crf','21','-g','50','-c:a','aac','-ar','48000','-ac','2','-b:a','160k',str(dest)])
    pieces.append(dest)
lst=OUT/'concat.txt';lst.write_text(''.join("file '"+p.as_posix()+"'\n" for p in pieces),encoding='utf-8')
run(['-f','concat','-safe','0','-i',str(lst),'-c','copy','-t','240','-movflags','+faststart',str(FINAL)])
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
m={'final':FINAL.name,'sha256':sha(FINAL),'base_sha256':sha(OLD),'skill_sha256':sha(SKILL),
   'segments':[{'source':p.name,'seconds':s} for p,s in [(OLD,178),(SKILL,clip_duration),(OLD,240-last_start)]],
   'runs':['run-20260927-140612-399b','run-20260928-155647-3290'],
   'boundary':'Historical main run and a newly CLI-started skill run; no next model trained.'}
(OUT/'final-video-manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(m,ensure_ascii=False))
