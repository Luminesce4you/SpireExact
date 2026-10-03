"""Telemetry JSON may contain finite floats; exact semantic keys use canonical.py."""
import json,gzip,time
from pathlib import Path

def read_json(path,*,resolve_checkpoint=True):
    path=Path(path)
    if not path.exists() and Path(str(path)+'.gz').exists():path=Path(str(path)+'.gz')
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='utf-8-sig')as f:document=json.load(f)
    else:document=json.loads(path.read_text(encoding='utf-8-sig'))
    if resolve_checkpoint and isinstance(document,dict)and document.get('schema')=='spire-map-checkpoint/v1'and 'evidence_ref'in document:
        reference=document.pop('evidence_ref');payload=document['payload']
        if reference.get('file')!='decision.json.gz' or 'evidence'in payload:raise ValueError('CHECKPOINT_EVIDENCE_REFERENCE')
        evidence=read_json(path.parent.parent/'decision.json.gz')['decision_evidence'];count=reference['count']
        if type(count)is not int or not 0<=count<=len(evidence)or count!=len(payload['history']):raise ValueError('CHECKPOINT_EVIDENCE_LENGTH')
        payload['evidence']=evidence[:count]
    return document
def write_json(path,value,*,compact=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=None if compact else 2,
                        separators=(',',':')if compact else None,allow_nan=False)+'\n',encoding='utf-8')
    # Windows readers (including external editors/indexers) can briefly omit
    # FILE_SHARE_DELETE. Preserve atomic publication; never truncate the target.
    # Retry only sharing/access failures and retain a bounded failure path.
    for attempt in range(11):
        try:
            temporary.replace(path)
            break
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32,33) or attempt==10:raise
            time.sleep(min(.01*2**attempt,.2))
