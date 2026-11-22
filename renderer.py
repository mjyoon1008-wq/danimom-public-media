"""Reusable news-style carousel renderer. No account credentials or paid renderer."""
from __future__ import annotations
import argparse, hashlib, ipaddress, json, os, socket, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import io
from urllib.error import HTTPError

W,H=1080,1350
BG='#0E0B19'
STYLE_VERSION='editorial-serif-v3'
FONT_ROOT=Path(os.environ.get('DANIMOM_FONTS','/usr/share/fonts/opentype/noto'))

def validate_brief(b):
    if not isinstance(b,dict): raise ValueError('brief must be an object')
    if b.get('category') not in ('beauty','fashion','living','travel','relatable'): raise ValueError('unsupported category')
    if not isinstance(b.get('caption'),str) or not 1<=len(b['caption'])<=1900: raise ValueError('caption required; max 1900 characters')
    tags=b.get('hashtags',[])
    if not isinstance(tags,list) or not 1<=len(tags)<=8 or any(not isinstance(t,str) or not t.startswith('#') or any(c.isspace() for c in t) for t in tags): raise ValueError('1 to 8 distinct hashtags required')
    if len(set(tags))!=len(tags): raise ValueError('duplicate tags')
    if not isinstance(b.get('sources'),list) or not b['sources']: raise ValueError('sources required')
    if b.get('experience') not in ('information','firsthand'): raise ValueError('experience must be explicit')
    if b['experience']=='firsthand' and not b.get('firsthand_evidence'): raise ValueError('firsthand evidence required')
    cards=b.get('cards',[])
    if not isinstance(cards,list) or not 3<=len(cards)<=5: raise ValueError('3 to 5 cards required')
    for c in cards:
        for name in ('headline','body','image','credit','license_url'):
            if not isinstance(c.get(name),str) or not c[name].strip(): raise ValueError(name+' is required')
        if len(c['headline'].replace('\n',''))>36 or len(c['body'])>150: raise ValueError('card copy is too long')
        if c.get('rights_verified') is not True: raise ValueError('photo permission not verified')
        if c.get('rights_basis') not in ('cc0','cc-by','cc-by-sa','public-domain','written-permission'): raise ValueError('unapproved photo permission')
    return b

def safe_url(url):
    u=urlparse(url)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443): raise ValueError('HTTPS media URL required')
    for a in socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(a[4][0]).is_global: raise ValueError('non-public media host')
    return url

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return super().redirect_request(req,fp,code,msg,headers,safe_url(newurl))

def load_photo(location,base):
    if location.startswith('https://'):
        req=urllib.request.Request(safe_url(location),headers={'User-Agent':'DanimomCardRenderer/1.0'})
        with urllib.request.build_opener(SafeRedirect).open(req,timeout=30) as r:
            if not r.headers.get('Content-Type','').startswith('image/'): raise ValueError('URL is not an image')
            data=r.read(16*1024*1024+1)
        if len(data)>16*1024*1024: raise ValueError('image exceeds 16 MB')
        im=Image.open(io.BytesIO(data))
    else:
        p=(base/location).resolve()
        if not p.is_relative_to(base.resolve()): raise ValueError('local image outside brief directory')
        im=Image.open(p)
    im=ImageOps.exif_transpose(im).convert('RGB')
    if min(im.size)<600: raise ValueError('photo resolution too low')
    return im

def font(size,bold=True):
    # Korean face index 1; no heavy Gothic fallback if the required serif is missing
    return ImageFont.truetype(str(FONT_ROOT/'NotoSerifCJK-Regular.ttc'),size,index=1)

def card_text(value):
    # Strip sentence-final full stops, preserving decimal points in product details
    import re
    return re.sub(r'[.。]+(?=\s|$)','',value).strip()

def photo_canvas(photo):
    # Blurred source background fills the frame; foreground is contained, never cropped
    bg=ImageOps.fit(photo,(W,H),method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(48))
    fg=ImageOps.contain(photo,(W,1030),method=Image.Resampling.LANCZOS)
    box=((W-fg.width)//2,max(0,(1030-fg.height)//2))
    bg.paste(fg,box)
    return bg,(*box,box[0]+fg.width,box[1]+fg.height)

def draw_card(c,index,base,display_date):
    im,photo_box=photo_canvas(load_photo(c['image'],base))
    overlay=Image.new('RGBA',im.size,(0,0,0,0));d=ImageDraw.Draw(overlay)
    start=900 if index==0 else 750
    for y in range(start,H):
        alpha=round(232*((y-start)/(H-start))**.8)
        d.line((0,y,W,y),fill=(12,11,15,alpha))
    im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB');d=ImageDraw.Draw(im)
    d.text((W-185,35),'다니맘',font=font(29,False),fill='white')
    title=card_text(c['headline'])
    size,lines=fit(title,976,2,64 if index==0 else 52,38,False)
    title_y=H-225 if index==0 else 930
    for j,line in enumerate(lines):
        d.text((52,title_y+j*int(size*1.4)),line,font=font(size,False),fill='#EDB9B0' if j==len(lines)-1 else '#FFF6E7')
    body_visible=index>0
    if body_visible:
        size,lines=fit(card_text(c['body']),976,4,31,25,False)
        for j,line in enumerate(lines):
            d.text((52,1090+j*int(size*1.5)),line,font=font(size,False),fill='#F7F3EF')
    credit=c['credit']+' · '+display_date
    size,lines=fit(credit,976,2,18,14,False)
    for j,line in enumerate(lines):d.text((52,1285+j*22),line,font=font(size,False),fill='#D2CBC7')
    return im,{'card':index+1,'photo_box':photo_box,'source_crop':False,
               'cover_body_visible':False if index==0 else None,
               'headline':title,'body_visible':body_visible}

def wrap(text,size,width,bold=True):
    d=ImageDraw.Draw(Image.new('RGB',(1,1))); f=font(size,bold);lines=[]
    for para in text.split('\n'):
        cur=''
        for ch in para:
            if cur and d.textlength(cur+ch,font=f)>width:lines.append(cur.rstrip());cur=''
            cur+=ch
        if cur:lines.append(cur.rstrip())
    return lines

def fit(text,width,maxlines,start,minimum,bold=True):
    for size in range(start,minimum-1,-1):
        lines=wrap(text,size,width,bold)
        if len(lines)<=maxlines:return size,lines
    raise ValueError('text does not fit; rewrite copy rather than clip it')

def _render(b,base,out):
    validate_brief(b);out.mkdir(parents=True,exist_ok=True)
    previews=[];layouts=[]
    for i,c in enumerate(b['cards']):
        im,layout=draw_card(c,i,base,b.get('display_date','날짜는 본문 참고'))
        layouts.append(layout)
        im.save(out/f'card_{i+1:02}.jpg',quality=96,subsampling=0)
        previews.append(im.resize((360,450)))
    preview=Image.new('RGB',(360*len(previews),450))
    for i,p in enumerate(previews):preview.paste(p,(360*i,0))
    preview.save(out/'preview.jpg',quality=94)
    credits=list(dict.fromkeys(c['credit']+' | '+c['license_url'] for c in b['cards']))
    caption=b['caption']+'\n\n사진: '+'\n'.join(credits)+'\n자료: '+'\n'.join(b['sources'])+'\n\n'+' '.join(b['hashtags'])
    if len(caption)>2200:raise ValueError('final caption exceeds 2200 characters')
    (out/'caption.txt').write_text(caption,encoding='utf-8')
    digest=hashlib.sha256(json.dumps(b,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    (out/'manifest.json').write_text(json.dumps({'brief_hash':digest,'count':len(previews),'size':[W,H],
        'style_version':STYLE_VERSION,'font':'Noto Serif CJK KR Regular','layouts':layouts,
        'publish_status':'not_published','requires_editorial_verification':True},ensure_ascii=False,indent=2))
    return out

def render(b,base,out):
    try:
        return _render(b,base,out)
    except Exception as exc:
        out.mkdir(parents=True,exist_ok=True)
        report={'style_version':STYLE_VERSION,'render_status':'failed',
                'publish_status':'blocked','publication_approved':False,
                'error_type':type(exc).__name__,'reason':str(exc)}
        if isinstance(exc,HTTPError):
            report.update(http_status=exc.code,stage='photo_download',
                          retry_after=exc.headers.get('Retry-After') if exc.headers else None,
                          automatic_retry=False)
        (out/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('brief',type=Path);p.add_argument('--out',type=Path,default=Path('output'));a=p.parse_args()
    render(json.loads(a.brief.read_text(encoding='utf-8')),a.brief.resolve().parent,a.out)

