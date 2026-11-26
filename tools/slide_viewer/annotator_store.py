"""Offline, frame-scoped native sidecars. Only the edited type is published."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from shapely.geometry import Polygon, mapping, shape

EXT = {"points": "points.json", "polygons": "polygons.geojson", "tags": "tags.json", "words": "words.json"}
CODE = dict(points="P", polygons="G", tags="T", words="W")
LIMIT = 24 * 1024 * 1024


def check(ok, message):
    if not ok:
        raise ValueError(message)


def pairs(items):
    result = dict(items)
    check(len(items) == len(result), "Duplicate JSON key.")
    return result


def body(data):
    def bad(_):
        raise ValueError("Nonfinite annotation value.")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=bad)


def encode(doc):
    raw = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    check(len(raw) <= LIMIT, "Sidecar exceeds 24 MiB.")
    return raw


def plain(path, *, missing=False):
    try:
        s = path.lstat()
    except FileNotFoundError:
        if missing:
            return False
        raise
    check(stat.S_ISREG(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400,
          "Refusing linked or non-regular file: " + path.name)
    return True


def read(path):
    if not plain(path, missing=True):
        return None
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    check(len(raw) <= LIMIT, "Sidecar exceeds 24 MiB.")
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


@contextmanager
def lock(path):
    # Keep the lock file: unlinking it while another instance holds it would split the lock.
    plain(path, missing=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def field(kind):
    return "features" if kind == "polygons" else kind


def props(kind, row):
    return row["properties"] if kind == "polygons" else row


def uid(kind, row):
    return props(kind, row)[kind[:-1]+"_id"]


class Store:
    def __init__(self, slide, dims):
        self.slide = Path(slide).absolute()
        plain(self.slide)
        self.geom = [float(dims[0]), float(dims[1]), int(dims[2])]
        s = self.slide.stat()
        self.signature = (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        self.paths = {k:self.slide.with_name(self.slide.name+"."+suffix) for k,suffix in EXT.items()}
        self.docs, self.hashes, self.history, self.redo = {}, {}, [], []
        owners = set()
        for kind, path in self.paths.items():
            raw = read(path)
            self.hashes[kind] = sha(raw)
            if raw is None:
                continue
            doc = body(raw)
            header = doc["properties"] if kind == "polygons" else doc
            check(isinstance(header,dict) and header.get("file_role")==kind and header.get("schema_version") in ("0.1.0","0.2.0")
                  and (kind!="polygons" or doc.get("type")=="FeatureCollection"), "Unrecognized sidecar: "+path.name)
            check(header.get("standalone_geometry", self.geom)==self.geom, "Slide dimensions or frame count changed.")
            owner = header.get("record_id")
            check(isinstance(owner,str) and 0<len(owner)<=512,"Missing source identity.")
            owners.add(owner)
            rows=doc[field(kind)]
            check(isinstance(rows,list) and len(rows)<=100000,"Invalid sidecar collection.")
            ids=set()
            for row in rows:
                p=props(kind,row)
                fr=p.get("frame_index",0)
                ident=uid(kind,row)
                check(isinstance(ident,str) and 0<len(ident)<=512 and (fr,ident) not in ids and p.get("record_id",owner)==owner
                      and type(fr) is int and 0<=fr<self.geom[2] and type(p.get("layer")) is int and 1<=p["layer"]<=10,
                      "Invalid frame, layer or object identity.")
                ids.add((fr,ident))
                if kind=="points":self.xy([p["x"],p["y"]])
                if kind=="polygons":
                    g=shape(row["geometry"])
                    check(g.geom_type in ("Polygon","MultiPolygon") and not g.is_empty and g.is_valid,"Invalid polygon sidecar.")
                    for poly in (g.geoms if g.geom_type=="MultiPolygon" else [g]):
                        for ring in (poly.exterior,*poly.interiors):
                            for pair in ring.coords:self.xy(pair)
            self.docs[kind]=doc
        check(len(owners)<=1,"Sidecars belong to different slides.")
        self.owner=next(iter(owners),"standalone_"+uuid4().hex)
        for kind in EXT:
            if kind not in self.docs:
                head=dict(file_role=kind,schema_version="0.1.0",record_id=self.owner,standalone_geometry=self.geom)
                self.docs[kind] = (dict(type="FeatureCollection",properties=head,features=[]) if kind=="polygons"
                                   else {**head, kind:[]})

    def xy(self, pair):
        check(len(pair)==2 and all(type(v) in (float,int) and math.isfinite(v) for v in pair)
              and 0<=pair[0]<=self.geom[0] and 0<=pair[1]<=self.geom[1],"Coordinate outside slide.")
        return list(pair)

    def publish(self,kind,doc):
        raw=encode(doc)
        path=self.paths[kind]
        with lock(self.slide.with_name("."+self.slide.name+".annotator.lock")):
            s=self.slide.stat()
            check((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==self.signature,"Slide changed. Reopen it.")
            for k,p in self.paths.items():
                check(sha(read(p))==self.hashes[k],"Annotations changed on disk. Reopen the slide before editing.")
            fd,name=tempfile.mkstemp(prefix=".ep-annotation-",dir=path.parent)
            tmp=Path(name)
            try:
                with os.fdopen(fd,"wb") as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                if self.hashes[kind] is not None: os.replace(tmp,path)
                elif os.name=="nt": tmp.rename(path)
                else: os.link(tmp,path)
            finally:
                tmp.unlink(missing_ok=True)
        self.docs[kind]=doc
        self.hashes[kind]=sha(raw)

    def edit(self,action,kind,frame,key,vertices=(),value="",object_id=""):
        check(kind in EXT and type(frame) is int and 0<=frame<self.geom[2],"Unknown layer or frame.")
        m=re.fullmatch(r"([1-9]|1\d|20)_([1-9]|1\d|20)_([1-9]|1\d|20)_([PGTW])_([1-9]|10)",key)
        check(m and m[4]==CODE[kind],"Invalid annotation class or layer.")
        if action=="save":
            s=self.slide.stat()
            check((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==self.signature,"Slide changed. Reopen it.")
            check(all(sha(read(p))==self.hashes[k] for k,p in self.paths.items()),"Annotations changed on disk. Reopen the slide before editing.")
            return
        if action in ("undo","redo"):
            src,dst=(self.history,self.redo) if action=="undo" else (self.redo,self.history)
            check(src,"No edit to "+action+".")
            k,prior=src[-1]
            now=encode(self.docs[k])
            self.publish(k,body(prior));src.pop();dst.append((k,now))
            return
        check(isinstance(value,str) and len(value)<=500,"Text is limited to 500 characters.")
        number=action in ("add-number","label-number")
        if number:
            check(kind=="words","Only Word layers accept numeric values.")
            try:numeric=float(value)
            except ValueError:raise ValueError("Enter a finite numeric value.") from None
            check(math.isfinite(numeric),"Enter a finite numeric value.")
            action="add" if action=="add-number" else "label"
        object_edit=action in ("label","clear-labels")
        if object_edit:
            label_kind=kind
            target,sep,object_id=object_id.partition(":")
            check(sep and target in ("0","1") and kind in ("tags","words"),"Invalid label target.")
            kind=("points","polygons")[int(target)]
        before=encode(self.docs[kind])
        doc=deepcopy(self.docs[kind]); rows=doc[field(kind)]
        if object_edit:
            row=next((props(kind,r) for r in rows if props(kind,r).get("frame_index",0)==frame and uid(kind,r)==object_id),None)
            check(row is not None,"Annotation no longer exists.")
            field_name=kind[:-1]+"_"+label_kind
            labels=row.get(field_name,[])
            check(isinstance(labels,list),"Invalid object labels.")
            if action=="clear-labels":
                if not labels:return
                row[field_name]=[]
            else:
                layer=int(m[5])
                label=dict(layer=layer,annotation_layer_key=key)
                if label_kind=="tags":
                    label.update(meaning=value,tag_primary=int(m[1]),tag_secondary=int(m[2]),tag_tertiary=int(m[3]))
                else:
                    check(value.strip(),"Enter a word or phrase.")
                    label.update(format="number" if number else "word",value=numeric if number else value)
                prior=next((idx for idx,it in enumerate(labels) if it.get("annotation_layer_key")==key),None)
                if prior is None:labels.append(label)
                elif label_kind=="tags" or (labels[prior].get("value")==label["value"] and labels[prior].get("format")==label["format"]):return
                else:labels[prior]={**labels[prior],**label}
                row[field_name]=labels
        elif action=="delete":
            found=[r for r in rows if props(kind,r).get("frame_index",0)==frame and uid(kind,r)==object_id]
            check(len(found)==1,"Annotation no longer exists.")
            rows.remove(found[0])
        elif action in ("add","clip"):
            layer=int(m[5]); ident=key if kind in ("words","tags") else uuid4().hex
            p=dict(frame_index=frame,layer=layer,record_id=self.owner,annotation_layer_key=key)
            p[kind[:-1]+"_id"]=ident
            if kind!="words":
                for name,number in zip(("primary","secondary","tertiary"),m.groups()[:3]):
                    p[kind[:-1]+"_"+name]=int(number)
            if kind=="points":
                check(len(vertices)==1 and action=="add","Select a point.")
                p["x"],p["y"]=self.xy(vertices[0]);rows.append(p)
            elif kind=="polygons":
                check(3<=len(vertices)<=100000,"Draw at least three vertices.")
                g=Polygon([self.xy(pt) for pt in vertices])
                check(g.is_valid and g.area>0,"The polygon crosses itself or has no area.")
                if action=="clip":
                    altered=False
                    for i in range(len(rows)-1,-1,-1):
                        prop=props(kind,rows[i])
                        if prop.get("frame_index",0)!=frame or prop.get("annotation_layer_key")!=key:continue
                        old=shape(rows[i]["geometry"]);out=old.difference(g)
                        if out.equals(old): continue
                        altered=True
                        if out.is_empty: rows.pop(i)
                        else: rows[i]["geometry"]=mapping(out)
                    check(altered,"No same-layer polygon overlaps the draft.")
                else:
                    merged=[]
                    for row in rows:
                        pr=props(kind,row)
                        if pr.get("frame_index",0)==frame and pr.get("annotation_layer_key")==key and shape(row["geometry"]).intersection(g).area>0:
                            merged.append(row)
                    if not merged:rows.append(dict(type="Feature",properties=p,geometry=mapping(g)))
                    else:
                        # Do not silently discard conflicting object labels or provenance.
                        from shapely.ops import unary_union
                        base=props(kind,merged[0])
                        for row in merged[1:]:
                            other=props(kind,row)
                            check({k:v for k,v in base.items() if k!="polygon_id"}=={k:v for k,v in other.items() if k!="polygon_id"},
                                  "Overlapping polygons carry different labels. Resolve their labels before merging.")
                        merged[0]["geometry"]=mapping(unary_union([g]+[shape(row["geometry"]) for row in merged]))
                        for row in merged[1:]:rows.remove(row)
            else:
                check(action=="add","Invalid action.")
                if kind=="words":p.update(format="number" if number else "word",value=numeric if number else value);check(value.strip(),"Enter a word or phrase.")
                else:p["meaning"]=value
                rows[:]=[r for r in rows if not (props(kind,r).get("frame_index",0)==frame and uid(kind,r)==ident)]
                rows.append(p)
        else: raise ValueError("Unknown editing action.")
        self.publish(kind,doc)
        self.history.append((kind,before));self.redo.clear()
        while len(self.history)>50 or sum(len(v) for _,v in self.history)>32*1024**2:self.history.pop(0)

    def visible(self,frame):
        for kind,doc in self.docs.items():
            for row in doc[field(kind)]:
                p=props(kind,row)
                if p.get("frame_index",0)!=frame:continue
                key=p.get("annotation_layer_key") or f"1_1_1_{CODE[kind]}_{p['layer']}"
                summary=str(p.get("value","") if kind=="words" else p.get("meaning",""))
                prefix=(kind,uid(kind,row),key,p["layer"],summary)
                if kind=="points":yield (*prefix,[(p["x"],p["y"])])
                elif kind=="polygons":
                    g=row["geometry"]
                    for poly in (g["coordinates"] if g["type"]=="MultiPolygon" else [g["coordinates"]]):
                        for ring in poly:yield (*prefix,ring)
                else:yield (*prefix,[])

    def object_labels(self,frame,*,typed=False):
        for parent,kind in enumerate(("points","polygons")):
            for row in self.docs[kind][field(kind)]:
                p=props(kind,row)
                if p.get("frame_index",0)!=frame:continue
                for j,label_kind in enumerate(("tags","words")):
                    labels=p.get(kind[:-1]+"_"+label_kind,[])
                    if not labels:continue
                    if parent:
                        pt=shape(row["geometry"]).representative_point()
                        anchor=[pt.x,pt.y]
                    else:anchor=[p["x"],p["y"]]
                    for label in labels:
                        layer=label.get("layer")
                        check(type(layer) is int and 1<=layer<=10,"Invalid object label layer.")
                        key=label.get("annotation_layer_key") or f"1_1_1_{CODE[label_kind]}_{layer}"
                        number=typed and j and label.get("format")=="number"
                        yield (9+parent if number else 4+2*j+parent,uid(kind,row),key,layer,str(label.get("value" if j else "meaning","")),[anchor])
