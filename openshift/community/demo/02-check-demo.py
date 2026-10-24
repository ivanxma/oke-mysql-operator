#!/usr/bin/env python3
"""Check the rendered MP4, subtitle timing and referenced demo assets."""
import json
import re
import subprocess
from pathlib import Path
import imageio_ffmpeg

folder=Path(__file__).resolve().parent
story=json.loads((folder/'storyboard.json').read_text())
chapters=json.loads((folder/'chapters.json').read_text())
assert len(story['scenes'])==len(chapters),'Chapter count differs from storyboard.'
for scene in story['scenes']:
    if scene['kind']=='console':assert (folder/scene['image']).is_file(),scene['image']
assert 'illustrative' in story['mode'],'Recording mode must identify the replay.'
for name in ('mysql-clusterset-demo.srt','mysql-clusterset-demo.vtt','transcript.txt','mysql-clusterset-demo-poster.png'):
    assert (folder/name).is_file(),name
video=folder/'mysql-clusterset-two-regions.mp4';ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(video)],capture_output=True,text=True)
info=probe.stderr
assert 'Video: h264' in info,'Expected browser-compatible H.264 video.'
assert '2880x1620' in info,'Unexpected video dimensions.'
assert 'Subtitle: mov_text' in info,'Embedded captions are missing.'
match=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)',info)
assert match,'Video duration unavailable.'
duration=int(match[1])*3600+int(match[2])*60+float(match[3])
assert abs(duration-sum(c['duration'] for c in chapters))<0.2,'Video/chapter duration mismatch.'
print(f'PASS: {duration:.2f}s, H.264, 2880x1620, embedded captions.',flush=True)
print('Decoding all video/audio frames...',flush=True)
subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-i',str(video),'-map','0:v:0','-map','0:a:0?','-f','null','-'],check=True)
print('PASS: complete media decode and referenced assets.',flush=True)
