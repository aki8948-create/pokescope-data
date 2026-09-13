"""Daily public Pocket snapshot. Python standard library; no AI or paid services."""
import json, time, re, html, unicodedata
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
BASE='https://play.limitlesstcg.com'
ROOT=Path(__file__).resolve().parent
MAX_BYTES=50_000_000
last_request=0

def get(url, as_json=True):
    global last_request
    for attempt in range(5):
        time.sleep(max(0,1.1-(time.monotonic()-last_request)))
        last_request=time.monotonic()
        try:
            req=Request(url,headers={'User-Agent':'PokeScope/1.0 public tournament analysis','Accept':'application/json' if as_json else 'text/html'})
            with urlopen(req,timeout=40) as r:
                body=r.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:raise ValueError('Response exceeds size limit')
            return json.loads(body) if as_json else body.decode('utf-8')
        except HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==4:raise
            retry=e.headers.get('Retry-After','')
            delay=float(retry) if retry.replace('.','',1).isdigit() else 2**(attempt+2)
            if delay>180:raise RuntimeError('Rate limit too long; keep previous snapshot') from None
            time.sleep(delay+1)
        except (URLError,TimeoutError):
            if attempt==4:raise
            time.sleep(2**(attempt+2))

def date(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def norm(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',s).replace('’',"'")).lower()
def validate_cards(standings,catalog):
    for p in standings:
        for category in ('pokemon','trainer'):
            for c in (p.get('decklist') or {}).get(category,[]):
                key=f"{c['set']}:{int(c['number'])}"
                if key not in catalog or norm(catalog[key]['en'])!=norm(c['name']):
                    raise ValueError(f'Unregistered card: {key} {c["name"]}. Register Japanese name before publishing.')

def rule(event,known):
    identifier=event['id'];source=BASE+'/tournament/'+identifier
    # Existing human-reviewed restrictions remain authoritative.
    if identifier in known:return known[identifier]
    try:
        page=get(source,False)
        match=re.search(r'<tr class="format".*?</tr>',page,re.S)
        evidence=re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',match[0]))).strip() if match else ''
        if re.search(r'\bStandard format\b',evidence,re.I):
            return {'status':'standard','label':'通常','source':source,'formatEvidence':evidence,'reason':'大会ページのStandard format表示を確認'}
        if re.search(r'\b(Custom|Special|Restricted)\b',evidence,re.I) or event.get('specialRules'):
            return {'status':'special','label':'特殊','source':source,'formatEvidence':evidence,'conditions':['通常と異なる形式です。詳しい条件は未確認です。'],'note':'詳細確認前の大会です。通常ルールの集計には含めません。'}
    except (HTTPError,URLError,TimeoutError,ValueError,RuntimeError):pass
    return {'status':'unknown','label':'未確認','source':source,'reason':'大会ルールを確認できていません'}

def build():
    config=json.loads((ROOT/'packs.json').read_text())
    start=date(config['previous']['start']);boundary=date(config['current']['start']);end=datetime.now(timezone.utc)
    if not start<boundary<=end:raise ValueError('Invalid pack date window')
    if config['minimum_players']!=30:raise ValueError('Minimum participants must remain 30')
    catalog=json.loads((ROOT/'catalog.json').read_text());known=json.loads((ROOT/'rules.json').read_text())
    selected={};complete=False
    for page in range(1,101):
        rows=get(BASE+f'/api/tournaments?game=POCKET&limit=50&page={page}')
        if not isinstance(rows,list):raise ValueError('Invalid tournament list')
        if not rows:complete=True;break
        dates=[date(t['date']) for t in rows]
        for t,d in zip(rows,dates):
            if start<=d<=end and t.get('players',0)>=30:
                if not re.fullmatch(r'[A-Za-z0-9_-]+',t['id']):raise ValueError('Invalid tournament ID')
                selected[t['id']]=t
        print(f'Page {page}: {len(selected)} eligible tournaments',flush=True)
        if min(dates)<start or len(rows)<50:complete=True;break
    if not complete:raise ValueError('Listing incomplete; previous snapshot preserved')
    events=[];rules={}
    for i,identifier in enumerate(selected):
        prefix=BASE+'/api/tournaments/'+identifier
        details=get(prefix+'/details')
        if details.get('id')!=identifier:raise ValueError('Tournament ID mismatch')
        if details.get('game')!='POCKET' or not details.get('isPublic') or details.get('players',0)<30:continue
        if not start<=date(details['date'])<=end:continue
        standings=get(prefix+'/standings');pairings=get(prefix+'/pairings')
        if not isinstance(standings,list) or not isinstance(pairings,list):raise ValueError('Invalid results')
        # Not-yet-started tournaments do not contribute to result statistics.
        if not standings or not pairings:continue
        validate_cards(standings,catalog)
        event={k:details.get(k,[]) for k in ('id','name','date','players','phases','specialRules')}
        event['standings']=[{k:p.get(k) for k in ('player','placing','deck','decklist')} for p in standings]
        event['pairings']=pairings
        events.append(event);rules[identifier]=rule(event,known)
        print(f'{i+1}/{len(selected)}: {identifier}, {len(standings)} players',flush=True)
    if not events:raise ValueError('No results; previous snapshot preserved')
    events.sort(key=lambda e:e['date'],reverse=True)
    data={'updated':datetime.now(timezone.utc).isoformat(),'events':events,'packs':config,'period':{'start':start.isoformat(),'end':end.isoformat(),'minimum_players':30,'listing_complete':True,'eligible_count':len(selected),'complete':True}}
    result={'schemaVersion':1,'data':data,'rules':rules}
    encoded=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()
    if len(encoded)>MAX_BYTES:raise ValueError('50 MB publication cap exceeded; keep previous snapshot')
    output=ROOT/'public';output.mkdir(exist_ok=True)
    temp=output/'snapshot.json.tmp';temp.write_bytes(encoded);temp.replace(output/'snapshot.json')
    (output/'index.html').write_text('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><title>PokeScope大会データ</title><p>PokeScope用の大会データ配信先です。大会データ出典：<a href="https://play.limitlesstcg.com/">Limitless TCG</a></p></html>')
    (output/'.nojekyll').touch()
    print(f'Validated: {len(events)} tournaments, {len(encoded)} bytes',flush=True)
if __name__=='__main__':build()
