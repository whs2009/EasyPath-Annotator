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

from shapely import STRtree, make_valid
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

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


def polygon_parts(geom):
    """Ignore collapsed lines and points, never the second lobe of a crossing."""
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom] if geom.area > 0 else []
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for child in geom.geoms for p in polygon_parts(child)]
    return []


def split_features(template, parts):
    # Keep the original ID on the largest remaining part; clone labels, not IDs.
    for i, part in enumerate(sorted(parts, key=lambda p: (-p.area, p.bounds))):
        row = deepcopy(template)
        if i:
            row["properties"]["polygon_id"] = uuid4().hex
        row["geometry"] = mapping(part)
        yield row


def polygon_edit(rows, draft, props_new, clip):
    parts = polygon_parts(draft if draft.is_valid else make_valid(draft))
    check(parts, "The outline encloses no area. Resume tracing or clear the draft.")
    key, frame = props_new["annotation_layer_key"], props_new["frame_index"]
    eligible = [(i, row) for i, row in enumerate(rows)
                if row["properties"].get("frame_index", 0) == frame
                and row["properties"].get("annotation_layer_key") == key]
    changes = {}
    if clip:
        mask = unary_union(parts)
        for i, row in eligible:
            old = shape(row["geometry"])
            if not old.intersects(mask):
                continue
            result = old.difference(mask)
            if not result.equals(old):
                changes[i] = list(split_features(row, polygon_parts(result)))
        check(changes, "No same-layer polygon overlaps the draft.")
        rows[:] = [part for i, row in enumerate(rows) for part in changes.get(i, [row])]
        return

    # Link draft faces only through existing polygons with positive-area overlap.
    # Faces meeting only at a crossing remain independently selectable.
    tree = STRtree(parts)
    parent = list(range(len(parts)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    hits = []
    for i, row in eligible:
        old = shape(row["geometry"])
        match = [int(j) for j in tree.query(old, predicate="intersects")
                 if old.intersection(parts[j]).area > 0]
        if match:
            first = root(match[0])
            for j in match[1:]:
                parent[root(j)] = first
            hits.append((match[0], i, row, old))
    groups = {}
    for j, part in enumerate(parts):
        groups.setdefault(root(j), ([], []))[0].append(part)
    for j, i, row, old in hits:
        groups[root(j)][1].append((i, row, old))
    added = []
    for faces, matches in groups.values():
        if not matches:
            p = dict(props_new, polygon_id=uuid4().hex)
            added.append(dict(type="Feature", properties=p, geometry=mapping(faces[0])))
            continue
        first, template, _ = matches[0]
        def attributes(row):
            return {k:v for k,v in row["properties"].items() if k != "polygon_id"}
        check(all(attributes(row) == attributes(template) for _,row,_ in matches[1:]),
              "Overlapping polygons carry different labels. Resolve their labels before merging.")
        merged = unary_union(faces + [g for _,_,g in matches])
        for i, _, _ in matches:
            changes[i] = []
        changes[first] = list(split_features(template, polygon_parts(merged)))
    rows[:] = [part for i, row in enumerate(rows) for part in changes.get(i, [row])] + added


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
                polygon_edit(rows,g,p,action=="clip")
                check(len(rows)<=100000,"Too many polygons in this annotation file.")
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
