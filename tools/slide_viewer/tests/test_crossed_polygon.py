"""Geometry and persistence at the transaction boundary shared by both editions."""
from pathlib import Path
import sys
import tempfile
import unittest

from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from annotator_store import Store

KEY = "1_1_1_G_1"
BOW = [(10,10), (90,90), (10,90), (90,10)]
LOW = Polygon([(10,10),(50,50),(90,10)])
HIGH = Polygon([(10,90),(90,90),(50,50)])

class CrossedDraft(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.slide=Path(tmp.name)/"synthetic.svs"
        self.slide.write_bytes(b"test slide identity")
        self.s=Store(self.slide,(120,120,2))

    def edit(self,action,pts=(),key=KEY,frame=0):
        self.s.edit(action,"polygons",frame,key,pts)

    def rows(self,key=KEY,frame=0):
        return [r for r in self.s.docs["polygons"]["features"]
                if r["properties"]["annotation_layer_key"]==key and r["properties"]["frame_index"]==frame]

    def assert_parts(self,expected):
        got=[shape(r["geometry"]) for r in self.rows()]
        self.assertEqual(len(got),len(expected))
        self.assertTrue(all(g.is_valid and g.geom_type=="Polygon" for g in got))
        self.assertTrue(all(any(g.equals(want) for g in got) for want in expected))
        self.assertEqual(len({r["properties"]["polygon_id"] for r in self.rows()}),len(expected))

    def test_save_split_survives_reopen_and_undo(self):
        self.edit("add",BOW)
        self.assert_parts([LOW,HIGH])
        after=[r["properties"] for r in self.rows()]
        self.edit("undo")
        self.assert_parts([])
        self.edit("redo")
        self.s=Store(self.slide,(120,120,2))
        self.assertEqual([r["properties"] for r in self.rows()],after)
        self.assert_parts([LOW,HIGH])

    def test_merge_only_overlapping_lobe(self):
        seed=box(45,12,56,25)
        self.edit("add",seed.exterior.coords)
        original=self.rows()[0]["properties"]["polygon_id"]
        self.edit("add",BOW)
        self.assert_parts([LOW.union(seed),HIGH])
        self.assertTrue(any(r["properties"]["polygon_id"]==original and shape(r["geometry"]).equals(LOW.union(seed)) for r in self.rows()))

    def test_crossed_clip_split_and_scoping(self):
        rect=box(10,10,90,90)
        for key,frame in [(KEY,0),("1_1_1_G_2",0),("2_1_1_G_1",0),(KEY,1)]:
            self.edit("add",rect.exterior.coords,key,frame)
        self.edit("clip",BOW)
        want=list(rect.difference(unary_union([LOW,HIGH])).geoms)
        self.assert_parts(want)
        for key,frame in [("1_1_1_G_2",0),("2_1_1_G_1",0),(KEY,1)]:
            self.assertTrue(shape(self.rows(key,frame)[0]["geometry"]).equals(rect))
        self.edit("undo")
        self.assert_parts([rect])
        self.edit("redo")
        self.assert_parts(want)

    def test_spur_discarded_and_zero_area_is_atomic(self):
        self.edit("add",[(10,10),(40,10),(40,40),(30,40),(30,60),(30,40),(10,40)])
        self.assert_parts([box(10,10,40,40)])
        old=self.s.paths["polygons"].read_bytes()
        for action in ("add","clip"):
            with self.assertRaises(ValueError):self.edit(action,[(10,10),(20,20),(30,30)])
        self.assertEqual(self.s.paths["polygons"].read_bytes(),old)
        self.assertEqual(len(self.s.history),1)

    def test_clip_interior_and_hole_preserved(self):
        self.edit("add",box(0,0,100,100).exterior.coords)
        self.edit("clip",BOW)
        self.assert_parts([box(0,0,100,100).difference(LOW.union(HIGH))])

    def test_one_lobe_can_be_deleted_without_the_other(self):
        self.edit("add",BOW)
        ident=next(r["properties"]["polygon_id"] for r in self.rows() if shape(r["geometry"]).equals(LOW))
        self.s.edit("delete","polygons",0,KEY,object_id=ident)
        self.assert_parts([HIGH])

    def test_merge_label_conflict_rolls_back_entire_draft(self):
        for rect in (box(28,12,33,16), box(63,12,67,16)):
            self.edit("add",rect.exterior.coords)
        ident=self.rows()[0]["properties"]["polygon_id"]
        self.s.edit("label","tags",0,"1_1_1_T_1",value="review",object_id="1:"+ident)
        old=self.s.paths["polygons"].read_bytes()
        with self.assertRaisesRegex(ValueError,"different labels"):self.edit("add",BOW)
        self.assertEqual(self.s.paths["polygons"].read_bytes(),old)

if __name__=="__main__":unittest.main()
