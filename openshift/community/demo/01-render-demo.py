#!/usr/bin/env python3
"""Render a labelled instructional replay; never executes storyboard commands."""
import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

HERE=Path(__file__).resolve().parent
BG='#f5f4f2';INK='#312d2a';TEAL='#2d6665';RED='#c74634'
DARK='#111c26';BAR='#233240';MUTED='#aebcc6';WHITE='#edf2f5';GOLD='#f2c66d'

def font(path,size):
    return ImageFont.truetype(str(path),size) if path else ImageFont.truetype('DejaVuSansMono.ttf',size)

def wrap(text,f,width,draw):
    lines=[]
    for paragraph in text.split('\n'):
        if draw.textlength(paragraph,font=f)<=width:
            lines.append(paragraph);continue
        current=''
        for word in paragraph.split():
            trial=(current+' '+word).strip()
            if draw.textlength(trial,font=f)>width and current:lines.append(current);current=word
            else:current=trial
        lines.append(current)
    return lines

def text(draw,xy,value,f,fill,width=None,spacing=1.25):
    lines=wrap(value,f,width,draw) if width else value.split('\n')
    x,y=xy
    for line in lines:draw.text((x,y),line,font=f,fill=fill);y+=int(f.size*spacing)
    return y

def seconds(ffmpeg,path):
    r=subprocess.run([ffmpeg,'-hide_banner','-i',str(path)],capture_output=True,text=True)
    m=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)',r.stderr)
    if not m:raise RuntimeError('Audio duration unavailable; use a working installed voice or omit --voice.')
    return int(m[1])*3600+int(m[2])*60+float(m[3])

def stamp(value,sep=','):
    ms=round(value*1000);s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02}{sep}{ms:03}'

def concat_path(p):return "'"+str(p.resolve()).replace("'","'\\''")+"'"

class Renderer:
    def __init__(self,story,font_path,mono_path):
        self.story=story;self.w=story['width'];self.h=story['height'];self.images={}
        self.title=font(font_path,52);self.body=font(font_path,30);self.small=font(font_path,25)
        self.heading=font(font_path,35);self.mono=font(mono_path or font_path,30);self.caption=font(font_path,35)
    def panel(self,d,x,y,w,h,name,panel,reveal):
        d.rounded_rectangle((x,y,x+w,y+h),radius=16,fill=DARK)
        d.rounded_rectangle((x,y,x+w,y+70),radius=16,fill=BAR)
        text(d,(x+24,y+18),name,self.heading,WHITE)
        status=panel.get('status','READY');active='ACTIVE' in status or 'PRIMARY' in status
        color=RED if active else (GOLD if status in ('WAITING','SWITCHING') else '#6ed0bd')
        d.rectangle((x+20,y+88,x+w-20,y+144),fill='#24313a')
        text(d,(x+34,y+98),status,self.body,color)
        line_y=y+176
        visible=panel.get('lines',[])[:reveal]
        for line in visible:
            fill='#a2e2cd' if line.startswith(('$','>','mysql>')) else WHITE
            if 'HEALTHY' in line or '[PASS]' in line:fill='#6ed0bd'
            if 'PRIMARY' in line:fill='#ffac98'
            if 'REPLICA' in line:fill='#84d3ed'
            for sub in wrap(line,self.mono,w-44,d):
                if line_y+self.mono.size>y+h-24:raise RuntimeError('Terminal text overflow: '+name)
                text(d,(x+24,line_y),sub,self.mono,fill);line_y+=39
        if line_y<y+h-35:d.rectangle((x+24,line_y+7,x+38,line_y+31),fill='#6ed0bd')
    def frame(self,scene,index,reveal,total_time,elapsed):
        im=Image.new('RGB',(self.w,self.h),BG);d=ImageDraw.Draw(im)
        d.rectangle((48,42,132,51),fill=RED)
        text(d,(48,74),scene['title'],self.title,INK,width=self.w-96)
        mode='REAL OKD CONSOLE · READ-ONLY · PRIVATE FIELDS MASKED' if scene['kind']=='console' else 'GUIDED TERMINAL REPLAY · ILLUSTRATIVE OUTPUT · NO LIVE ROLE CHANGE'
        text(d,(48,154),mode,self.small,TEAL,width=self.w-96)
        if scene['kind'] in ('terminal','title'):
            pw=(self.w-144)//3
            for n,name in enumerate(['LONDON · uk-london-1','CLUSTERSET CONTROL','FRANKFURT · eu-frankfurt-1']):
                value=scene[['left','center','right'][n]]
                self.panel(d,48+n*(pw+24),230,pw,1110,name,value,reveal)
        elif scene['kind']=='console':
            path=HERE/scene['image']
            if path not in self.images:self.images[path]=Image.open(path).convert('RGB')
            shot=self.images[path].copy();shot.thumbnail((2050,1150),Image.Resampling.LANCZOS)
            x=48+(2050-shot.width)//2;y=230+(1150-shot.height)//2
            im.paste(shot,(x,y));d=ImageDraw.Draw(im)
            d.rectangle((x,y,x+shot.width,y+shot.height),outline='#c9c3ba',width=3)
            # Annotate the console content area without changing resource values.
            focus=(x+int(shot.width*.225),y+int(shot.height*.13),x+int(shot.width*.98),y+int(shot.height*.89))
            d.rectangle(focus,outline=TEAL,width=4)
            yy=310
            for i,point in enumerate(scene['points']):
                text(d,(2160,yy),f'{i+1:02}',self.heading,RED)
                yy=text(d,(2240,yy),point,self.heading,INK,width=570)+46
        else:
            yy=300
            for i,point in enumerate(scene['points']):
                text(d,(160,yy),f'{i+1:02}',self.title,RED)
                yy=text(d,(300,yy+5),point,self.title,INK,width=2400)+66
        d.rectangle((48,1380,self.w-48,1536),fill='#e9e5df')
        text(d,(76,1404),scene['caption'],self.caption,INK,width=self.w-152)
        d.rectangle((48,1560,self.w-48,1570),fill='#d3cec6')
        d.rectangle((48,1560,48+int((self.w-96)*min(1,elapsed/total_time)),1570),fill=RED)
        text(d,(48,1585),'MySQL ClusterSet on community OKD · London / Frankfurt',self.small,TEAL)
        text(d,(self.w-550,1585),f'Chapter {index+1:02} / {len(self.story["scenes"]):02}',self.small,TEAL)
        return im

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=HERE)
    p.add_argument('--font',type=Path);p.add_argument('--mono-font',type=Path)
    p.add_argument('--voice',help='Optional installed macOS say voice; e.g. Daniel')
    p.add_argument('--work-dir',type=Path)
    p.add_argument('--preview-only',action='store_true')
    a=p.parse_args();story=json.loads((HERE/'storyboard.json').read_text())
    work=a.work_dir or Path(tempfile.mkdtemp(prefix='okd-clusterset-demo-'))
    work.mkdir(parents=True,exist_ok=True);a.output_dir.mkdir(parents=True,exist_ok=True)
    ff=imageio_ffmpeg.get_ffmpeg_exe();render=Renderer(story,a.font,a.mono_font)
    durations=[];audio=[]
    for i,scene in enumerate(story['scenes']):
        dur=scene['duration']
        if a.voice and not a.preview_only:
            say=shutil.which('say')
            if not say:raise RuntimeError('--voice requires the macOS say command.')
            speech=work/f'speech-{i:02}.aiff';script=work/f'speech-{i:02}.txt';script.write_text(scene['narration'])
            subprocess.run([say,'-v',a.voice,'-r','155','-o',str(speech),'-f',str(script)],check=True)
            dur=max(dur,seconds(ff,speech)+2)
            audio.append(speech)
        durations.append(math.ceil(dur*25)/25)
    total=sum(durations);elapsed=0;parts=[];captions=[];chapters=[];frame_number=0
    for i,(scene,dur) in enumerate(zip(story['scenes'],durations)):
        chapters.append(dict(start=round(elapsed,3),title=scene['title'],kind=scene['kind'],duration=dur))
        words=scene['narration'].split();chunks=[words[n:n+18] for n in range(0,len(words),18)]
        for j,chunk in enumerate(chunks):
            start=elapsed+dur*j/len(chunks);end=elapsed+dur*(j+1)/len(chunks)
            captions.append((start,end,' '.join(chunk)))
        steps=1 if scene['kind'] not in ('terminal','title') else max(len(scene[k]['lines']) for k in ('left','center','right'))
        if a.preview_only:steps=1
        for step in range(steps):
            reveal=999 if a.preview_only else step+1
            segment=dur if steps==1 else (dur*.68/(steps-1) if step<steps-1 else dur*.32)
            image=render.frame(scene,i,reveal,total,elapsed+dur*(step+1)/steps)
            target=work/f'frame-{frame_number:04}.png';image.save(target);frame_number+=1
            parts.append('file '+concat_path(target)+'\nduration '+f'{segment:.4f}'+'\n')
        full=render.frame(scene,i,999,total,elapsed+dur);full.save(work/f'chapter-{i+1:02}.png')
        if i==10:full.save(a.output_dir/'mysql-clusterset-demo-poster.png')
        print(f'Prepared chapter {i+1}/{len(durations)}: {scene["title"]}',flush=True)
        elapsed+=dur
    (a.output_dir/'chapters.json').write_text(json.dumps(chapters,indent=2)+'\n')
    srt='\n'.join(f'{i+1}\n{stamp(s)} --> {stamp(e)}\n{t}\n' for i,(s,e,t) in enumerate(captions))
    (a.output_dir/'mysql-clusterset-demo.srt').write_text(srt)
    vtt='WEBVTT\n\n'+'\n'.join(f'{stamp(s,".")} --> {stamp(e,".")}\n{t}\n' for s,e,t in captions)
    (a.output_dir/'mysql-clusterset-demo.vtt').write_text(vtt)
    (a.output_dir/'transcript.txt').write_text('\n\n'.join(f'{i+1:02}. {s["title"]}\n{s["narration"]}' for i,s in enumerate(story['scenes']))+'\n')
    if a.preview_only:print('Preview frames:',work);return
    (work/'video.ffconcat').write_text('ffconcat version 1.0\n'+''.join(parts)+'file '+concat_path(target)+'\n')
    args=[ff,'-y','-hide_banner','-loglevel','warning','-f','concat','-safe','0','-i',str(work/'video.ffconcat')]
    if audio:
        clips=[]
        for i,(src,dur) in enumerate(zip(audio,durations)):
            dst=work/f'audio-{i:02}.wav'
            subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-i',str(src),'-af','apad','-t',str(dur),'-ar','48000','-ac','1',str(dst)],check=True)
            clips.append('file '+concat_path(dst)+'\n')
        (work/'audio.ffconcat').write_text('ffconcat version 1.0\n'+''.join(clips))
        args+=['-f','concat','-safe','0','-i',str(work/'audio.ffconcat')]
    args+=['-i',str(a.output_dir/'mysql-clusterset-demo.srt'),'-map','0:v:0']
    if audio:args+=['-map','1:a:0']
    args+=['-map',f'{2 if audio else 1}:s:0','-c:v','libx264','-preset','veryfast','-crf','20','-r','24','-fps_mode','cfr','-pix_fmt','yuv420p','-threads','4']
    if audio:args+=['-c:a','aac','-b:a','128k']
    video=a.output_dir/'mysql-clusterset-two-regions.mp4'
    args+=['-c:s','mov_text','-metadata','title=MySQL ClusterSet across two OKD clusters','-metadata','comment=Guided illustrative terminal replay plus redacted read-only console captures. No live creation or switchover.','-movflags','+faststart','-t',str(total),str(video)]
    print('Encoding video...',flush=True);subprocess.run(args,check=True)
    print('Video:',video);print('Duration:',round(total,2),'seconds');print('Work directory:',work)

if __name__=='__main__':main()
