import ctypes as C
from pathlib import Path
from shutil import copyfile
from annotator_store import EXT


def bridge(current, text):
    i,p,d,s=C.c_int,C.c_void_p,C.POINTER(C.c_double),C.c_char_p
    emit=C.CFUNCTYPE(None,i,s,s,i,s,d,i,p)
    edit=C.CFUNCTYPE(i,s,i,i,s,d,i,s,s,emit,p,p,i)
    kinds=list(EXT)
    @edit
    def callback(cmd,k,frame,key,xy,n,value,ident,emit_,ctx,err,size):
        try:
            store=current()
            if not 0<=k<4 or not 0<=n<=100000 or not 0<=frame<store.geom[2]:
                raise ValueError("Invalid annotation input.")
            if cmd!=b"list":
                pts=[(xy[2*j],xy[2*j+1]) for j in range(n)]
                store.edit(cmd.decode(),kinds[k],frame,key.decode(),pts,value.decode(),ident.decode())
            numbers={row["word_id"] for row in store.docs["words"]["words"] if row.get("frame_index",0)==frame and row.get("format")=="number"}
            for kind,uid,address,layer,label,ring in store.visible(frame):
                arr=(C.c_double*(len(ring)*2))(*(v for pair in ring for v in pair))
                emitted_kind=8 if kind=="words" and uid in numbers else kinds.index(kind)
                emit_(emitted_kind,uid.encode(),address.encode(),layer,label.encode(),arr,len(ring),ctx)
            for k,uid,address,layer,label,ring in store.object_labels(frame,typed=True):
                arr=(C.c_double*2)(*ring[0])
                emit_(k,uid.encode(),address.encode(),layer,label.encode(),arr,1,ctx)
            return 0
        except Exception as exc:
            text(err,size,exc);return 1
    return callback


def test_copies(scan, stack, output):
    folder=Path(output)
    src=[Path(scan),Path(stack)]
    if any(p.stat().st_size>4*1024**2 for p in src) or [p.suffix.lower() for p in src]!=[".svs",".ep"]:
        raise ValueError("This check requires two small synthetic fixtures.")
    folder.mkdir(exist_ok=False)
    paths=[folder/"synthetic.svs",folder/"stack.ep"]
    for s,d in zip(src,paths):copyfile(s,d)
    return [*paths,folder/"annotated.png"]
