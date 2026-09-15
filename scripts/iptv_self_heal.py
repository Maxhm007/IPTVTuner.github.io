#!/usr/bin/env python3
import json,re,sys,time,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin

USER_AGENT="Mozilla/5.0 (IPTVTuner-SelfHeal/3.1)"
TIMEOUT=6; SOURCE_TIMEOUT=12; MAX_REPLACEMENT_CANDIDATES=3
FAILURES_BEFORE_ACTION=3; RECOVERY_SUCCESSES_REQUIRED=2
BATCH_SIZE=40; CHECK_WORKERS=8
SOURCES=[
("iptv-org/bd","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/bd.m3u",10),
("iptv-org/in","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u",20),
("iptv-org/us","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us.m3u",20),
("iptv-org/uk","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/uk.m3u",20),
("iptv-org/qa","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/qa.m3u",20),
("iptv-org/jp","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/jp.m3u",20),
("iptv-org/at","https://raw.githubusercontent.com/iptv-org/iptv/master/streams/at.m3u",20)]
ATTR_RE=re.compile(r'(tvg-id|tvg-name|group-title)="([^"]*)"')
INACTIVE_INFO="#SELFHEAL-INACTIVE "; INACTIVE_URL="#SELFHEAL-URL "
ALIASES={"anando":"ananda","anando tv":"ananda","shomoy":"somoy","shomoy tv":"somoy","tsports":"t sports","t sports":"t sports","ekhon tv hd":"ekhon","ekushey tv hd":"ekushey","boishakhi tv hd":"boishakhi"}
RESTRICTED=("hbo","cinemax","showtime","disney channel","disney jr","disney xd","espn","sony six","sony ten","star sports","supersport","wwe network","star movies","sony pix","fox movies","fox family movies")

def now_iso(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def fetch_bytes(url,timeout=TIMEOUT,max_bytes=524288,range_request=False):
    h={"User-Agent":USER_AGENT,"Accept":"application/vnd.apple.mpegurl, application/x-mpegURL, video/*, audio/*, */*"}
    if range_request:h["Range"]=f"bytes=0-{max_bytes-1}"
    with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=timeout) as r:return r.read(max_bytes),getattr(r,"url",url)

def fetch_text(url,timeout=TIMEOUT):
    b,u=fetch_bytes(url,timeout); return b.decode("utf-8",errors="replace"),u

def first_uri_after(text,tag):
    lines=[x.strip() for x in text.splitlines()]
    for i,line in enumerate(lines):
        if line.startswith(tag):
            for x in lines[i+1:]:
                if not x:continue
                if not x.startswith("#"):return x
                break
    return None

def validate_hls_once(url):
    text,base=fetch_text(url)
    if "#EXTM3U" not in text.lstrip()[:2048]:return False
    variant=first_uri_after(text,"#EXT-X-STREAM-INF")
    if variant:
        text,base=fetch_text(urljoin(base,variant))
        if "#EXTM3U" not in text.lstrip()[:2048]:return False
    seg=first_uri_after(text,"#EXTINF")
    if not seg and "#EXT-X-TARGETDURATION" in text:
        seg=next((x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith("#")),None)
    if not seg:return False
    b,_=fetch_bytes(urljoin(base,seg),max_bytes=2048,range_request=True)
    return bool(b)

def is_working_hls(url,attempts=2):
    for n in range(attempts):
        try:
            if validate_hls_once(url):return True
        except Exception:pass
        if n+1<attempts:time.sleep(.25)
    return False

def normalize(s):
    s=(s or "").lower(); s=re.sub(r"\([^)]*\)|\[[^]]*\]"," ",s); s=re.sub(r"\b(hd|fhd|sd|uhd|4k|1080p|720p|576p|480p|360p|tv|channel|live)\b"," ",s); s=re.sub(r"[^a-z0-9]+"," ",s); return " ".join(s.split())
def canonical_name(s):
    n=normalize(s); return ALIASES.get(n,n)
def base_tvg_id(v):
    raw=(v or "").strip().lower().split("@",1)[0]
    compact=re.sub(r"[^a-z0-9]+","",raw)
    if not raw or compact in {"none","null","na","notvgid"} or compact.startswith("notvgid"):return ""
    return raw

def parse_playlist(text):
    lines=text.splitlines(); out=[]; i=0
    while i<len(lines):
        raw=lines[i]; s=raw.strip(); inactive=False
        if s.startswith(INACTIVE_INFO+"#EXTINF:"):inactive=True; info=s[len(INACTIVE_INFO):]
        elif s.startswith("#EXTINF:"):info=s
        else:i+=1;continue
        attrs=dict(ATTR_RE.findall(info)); name=info.split(",",1)[1].strip() if "," in info else attrs.get("tvg-name",""); j=i+1
        if inactive:
            while j<len(lines) and not lines[j].strip().startswith(INACTIVE_URL):
                q=lines[j].strip()
                if q.startswith("#EXTINF:") or q.startswith(INACTIVE_INFO+"#EXTINF:"):break
                j+=1
            if j>=len(lines) or not lines[j].strip().startswith(INACTIVE_URL):i+=1;continue
            url=lines[j].strip()[len(INACTIVE_URL):].strip()
        else:
            while j<len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):j+=1
            if j>=len(lines) or not lines[j].strip().startswith(("http://","https://")):i+=1;continue
            url=lines[j].strip()
        out.append({"info":info,"url":url,"name":attrs.get("tvg-name") or name,"display":name,"tvg_id":attrs.get("tvg-id",""),"group":attrs.get("group-title",""),"inactive":inactive,"raw_info":raw,"raw_url":lines[j]}); i=j+1
    return out

def catalog_key(e):
    tid=base_tvg_id(e.get("tvg_id"))
    return f"id:{tid}" if tid else f"name:{normalize(e.get('group'))}:{canonical_name(e.get('name') or e.get('display'))}"
def is_restricted(e):return any(x in canonical_name(e.get("name") or e.get("display")) for x in RESTRICTED)
def credential_style(u):return bool(re.search(r"/live/[^/]+/[^/]+/[^/?]+",u or "",re.I))

def sync_legacy_catalog(p):
    legacy=p.with_name("IPTV-V007.m3u")
    if not legacy.exists():return 0
    cur=p.read_text(encoding="utf-8"); existing={catalog_key(e) for e in parse_playlist(cur)}; add=[]; skipped=0
    for e in parse_playlist(legacy.read_text(encoding="utf-8")):
        k=catalog_key(e)
        if k in existing:continue
        if is_restricted(e) or credential_style(e["url"]):skipped+=1;continue
        add.append(e);existing.add(k)
    if add:
        block=["","########## V007 LEGACY CATALOG - SELF HEAL ##########",""]
        for e in add:block += [e["info"],e["url"],""]
        p.write_text(cur.rstrip()+"\n"+"\n".join(block),encoding="utf-8")
    print(f"legacy sync: imported={len(add)}, skipped={skipped}"); return len(add)

def load_candidates():
    out=[]
    for name,url,rank in SOURCES:
        try:
            text,_=fetch_text(url,SOURCE_TIMEOUT); parsed=parse_playlist(text)
            for e in parsed:e["source"]=name;e["source_rank"]=rank
            out+=parsed; print(f"loaded {name}: {len(parsed)}")
        except Exception as ex:print(f"warning source {name}: {ex}")
    return out

def same_channel(a,b):
    ai,bi=base_tvg_id(a.get("tvg_id")),base_tvg_id(b.get("tvg_id"))
    if ai and bi:return ai==bi
    an,bn=canonical_name(a.get("name") or a.get("display")),canonical_name(b.get("name") or b.get("display"))
    if not an or an!=bn:return False
    ag,bg=normalize(a.get("group")),normalize(b.get("group")); return not(ag and bg and ag!=bg)

def find_replacement(e,cands,used,pending=None):
    if is_restricted(e):print("  replacement disabled for restricted/subscription channel");return None,None
    trial=[]
    if pending and pending!=e["url"]:trial.append({"url":pending,"source":"pending-recovery","source_rank":0})
    matches=[c for c in cands if c["url"]!=e["url"] and same_channel(e,c) and not credential_style(c["url"])]
    matches.sort(key=lambda c:(c.get("source_rank",99),not c["url"].startswith("https://"))); seen={x["url"] for x in trial}
    for c in matches:
        if c["url"] not in seen:trial.append(c);seen.add(c["url"])
    for c in trial[:MAX_REPLACEMENT_CANDIDATES]:
        u=c["url"]; owner=used.get(u)
        if owner and owner!=catalog_key(e):continue
        print(f"  validating exact match from {c.get('source','source')}: {u}")
        if is_working_hls(u,1):return u,c.get("source","unknown")
    return None,None

def replace_block(text,e,info,url):return text.replace(e["raw_info"]+"\n"+e["raw_url"],info+"\n"+url,1)
def load_state(path):
    try:d=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:d={}
    if not isinstance(d,dict):d={}
    d.setdefault("version",3);d.setdefault("cursor",0);d.setdefault("channels",{});return d

def channel_state(state,e):
    k=catalog_key(e);r=state["channels"].setdefault(k,{})
    for a,v in (("name",e["display"]),("group",e.get("group","")),("failures",0),("recovery_successes",0),("pending_url",""),("last_working_url",""),("status","inactive" if e["inactive"] else "active")):r.setdefault(a,v)
    return k,r

def select_rotation(entries,cursor):
    n=len(entries)
    if not n:return [],0
    cursor%=n;c=min(BATCH_SIZE,n);return [(cursor+i)%n for i in range(c)],(cursor+c)%n

def parallel_health(entries,indexes):
    result={}; active=[i for i in indexes if not entries[i]["inactive"]]
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        fs={pool.submit(is_working_hls,entries[i]["url"],2):i for i in active}
        for f in as_completed(fs):
            try:result[fs[f]]=bool(f.result())
            except Exception:result[fs[f]]=False
    return result

def write_report(path,entries,state,checked,imported,repl,off,on):
    active=sum(not e["inactive"] for e in entries);lines=["# IPTV V008 Health","",f"- Updated: {now_iso()}",f"- Total channels: {len(entries)}",f"- Active: {active}",f"- Inactive: {len(entries)-active}",f"- Checked this run: {checked}",f"- Imported from V007: {imported}",f"- Replaced: {len(repl)}",f"- Inactivated: {len(off)}",f"- Reactivated: {len(on)}",f"- Next cursor: {state.get('cursor',0)+1 if entries else 0}","","## Inactive channels",""]
    dead=[e for e in entries if e["inactive"]]
    for e in dead:
        r=state["channels"].get(catalog_key(e),{});lines.append(f"- {e['display']} — failures={r.get('failures',0)}, recovery={r.get('recovery_successes',0)}/{RECOVERY_SUCCESSES_REQUIRED}")
    if not dead:lines.append("- None")
    path.write_text("\n".join(lines)+"\n",encoding="utf-8")

def heal(playlist,state_dir=None):
    p=Path(playlist); root=Path(state_dir) if state_dir else p.parent; root.mkdir(parents=True,exist_ok=True)
    sp=root/"IPTV-V008-health.json"; rp=root/"IPTV-V008-health.md"; imported=sync_legacy_catalog(p)
    original=p.read_text(encoding="utf-8"); entries=parse_playlist(original); state=load_state(sp)
    valid={catalog_key(e) for e in entries}; state["channels"]={k:v for k,v in state["channels"].items() if k in valid}
    indexes,next_cursor=select_rotation(entries,int(state.get("cursor",0))); health=parallel_health(entries,indexes);cands=None
    used={}
    for e in entries:
        if e["url"]:used.setdefault(e["url"],catalog_key(e))
    repl=[];off=[];on=[]
    for idx in indexes:
        e=entries[idx];prefix=f"[{idx+1}/{len(entries)}]";k,r=channel_state(state,e);r["last_checked"]=now_iso()
        if e["inactive"]:
            print(f"{prefix} INACTIVE {e['display']}: recovery search")
            if cands is None:cands=load_candidates()
            u,src=find_replacement(e,cands,used,r.get("pending_url") or None)
            if u:
                r["recovery_successes"]=(int(r.get("recovery_successes",0))+1) if r.get("pending_url")==u else 1;r["pending_url"]=u;r["replacement_source"]=src
                if r["recovery_successes"]>=RECOVERY_SUCCESSES_REQUIRED:
                    original=replace_block(original,e,e["info"],u);used[u]=k;r.update({"status":"active","failures":0,"recovery_successes":0,"pending_url":"","last_working_url":u});on.append((e["display"],u));print(f"  REACTIVATED -> {u}")
            else:r["recovery_successes"]=0;r["pending_url"]="";print("  still inactive")
            continue
        print(f"{prefix} checking {e['display']}: {e['url']}")
        if health.get(idx,False):r.update({"status":"active","failures":0,"recovery_successes":0,"pending_url":"","last_working_url":e["url"]});print("  OK (manifest + segment)");continue
        r["failures"]=int(r.get("failures",0))+1;f=r["failures"];print(f"  FAILED ({f}/{FAILURES_BEFORE_ACTION})")
        if f<FAILURES_BEFORE_ACTION:print("  no replacement search before threshold");continue
        if cands is None:cands=load_candidates()
        u,src=find_replacement(e,cands,used)
        if u:
            original=replace_block(original,e,e["info"],u);used[u]=k;r.update({"status":"active","failures":0,"last_working_url":u,"replacement_source":src,"pending_url":"","recovery_successes":0});repl.append((e["display"],e["url"],u));print(f"  REPLACED -> {u}")
        else:
            original=replace_block(original,e,INACTIVE_INFO+e["info"],INACTIVE_URL+e["url"]);r.update({"status":"inactive","pending_url":"","recovery_successes":0});off.append((e["display"],e["url"]));print("  INACTIVATED")
    state.update({"version":3,"cursor":next_cursor,"last_run":now_iso(),"last_run_checked":len(indexes),"total_channels":len(entries)})
    sp.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    if imported or repl or off or on:p.write_text(original,encoding="utf-8")
    latest=parse_playlist(original);write_report(rp,latest,state,len(indexes),imported,repl,off,on)
    print(f"run summary: checked={len(indexes)}, next_cursor={next_cursor+1 if entries else 0}, replaced={len(repl)}, inactivated={len(off)}, reactivated={len(on)}")

if __name__=="__main__":heal(sys.argv[1] if len(sys.argv)>1 else "IPTV-V008.m3u",sys.argv[2] if len(sys.argv)>2 else None)
