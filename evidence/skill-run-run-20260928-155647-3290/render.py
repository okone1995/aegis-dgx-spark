from pathlib import Path
import json
import subprocess

OUT=Path(__file__).resolve().parent
FF=Path.home()/'anaconda3/envs/deepseekocr/ffmpeg/bin/ffmpeg.exe'
PROBE=FF.with_name('ffprobe.exe')
raw=next(OUT.glob('*-raw.webm'))
ass='''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,25,&H00FFFFFF,&H00FFFFFF,&H00181008,&H80181008,0,0,0,0,100,100,0,0,3,1,0,8,30,30,16,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:10.00,0:00:31.50,Default,,0,0,0,,{\\an9\\pos(1880,48)}等待过程 4× 加速 · 实际运行约 90 秒
Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,{\\an9\\pos(1880,48)}真实 CLI 启动 · 同轮实时后台
Dialogue: 0,0:00:31.50,0:00:45.00,Default,,0,0,0,,{\\an9\\pos(1880,48)}verified_restored · 收据核验 17/17
'''
(OUT/'speed.ass').write_text(ass,encoding='utf-8')
filters="[0:v]setpts='if(lt(T,10),PTS,if(lt(T,96),(10+(T-10)/4)/TB,(31.5+(T-96))/TB))',fps=30,ass=speed.ass[v];[1:a]apad[audio]"
dest=OUT/'aegis-skills-real-execution-20260929.mp4'
subprocess.run([str(FF),'-y','-i',str(raw),'-i',str(OUT/'narration.wav'),'-filter_complex',filters,
    '-map','[v]','-map','[audio]','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
    '-c:a','aac','-b:a','128k','-t','44.22','-movflags','+faststart',str(dest)],cwd=OUT,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
p=subprocess.run([str(PROBE),'-v','error','-show_entries','format=duration:stream=codec_name,width,height','-of','json',str(dest)],capture_output=True,text=True,check=True)
(OUT/'render-metadata.json').write_text(p.stdout,encoding='utf-8')
print(p.stdout)
