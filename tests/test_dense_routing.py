"""Focused regressions for spacing, parallel routes and endpoint serialization."""
import copy
import json
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/drawio-skill/scripts"))
import autolayout
import diagram_ir
import validate


class TestDenseRouting(unittest.TestCase):
    def test_spacing_survives_ir_serialization_into_layout_input(self):
        raw = {"ranksep": 1.25, "nodesep": 0.85,
               "nodes": [{"id": "a"}], "edges": []}
        saved = json.loads(json.dumps(diagram_ir.normalize_ir(raw)))
        graph = diagram_ir.ir_to_graph(saved)
        self.assertEqual(graph.get("ranksep"), 1.25)
        self.assertEqual(graph.get("nodesep"), 0.85)
        dot = autolayout.build_dot(graph)
        self.assertIn("ranksep=1.25;", dot)
        self.assertIn("nodesep=0.85;", dot)

    def test_spacing_defaults_are_not_injected(self):
        for options in ({}, {"ranksep": 0}, {"nodesep": 0.4}):
            with self.subTest(options=options):
                ir = diagram_ir.normalize_ir({"nodes": [{"id": "a"}], **options})
                graph = diagram_ir.ir_to_graph(json.loads(json.dumps(ir)))
                self.assertEqual({k: graph[k] for k in ("ranksep", "nodesep") if k in graph}, options)

    @unittest.skipUnless(shutil.which("dot"), "Graphviz required")
    def test_parallel_relations_keep_distinct_graphviz_waypoints(self):
        graph = {"nodes": [{"id": "alpha"}, {"id": "beta"}],
                 "edges": [
                     {"id": "request", "source": "alpha", "target": "beta", "label": "Request"},
                     {"id": "refund", "source": "alpha", "target": "beta", "label": "Refund"}]}
        height, positions, routes = autolayout.layout(autolayout.build_dot(graph))
        root = ET.fromstring(autolayout.to_drawio(graph, height, positions, routes))
        edges = root.findall(".//mxCell[@edge='1']")
        self.assertEqual([e.get("value") for e in edges], ["Request", "Refund"])
        ids = {c.get("id"): c for c in root.findall(".//mxCell")}
        points = [[validate.endpoint(e, "source", ids)] + validate.edge_waypoints(e)
                  + [validate.endpoint(e, "target", ids)] for e in edges]
        self.assertNotEqual(points[0], points[1])

    def dense_fixture(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/dense-routing.json").read_text())
        graph = fixture["graph"]
        original = copy.deepcopy(graph)
        routes = {tuple(k): v for k, v in fixture["routes"]}
        root = ET.fromstring(autolayout.to_drawio(graph, fixture["height"], fixture["positions"], routes))
        self.assertEqual(graph, original, "serialization mutated the semantic graph")
        return fixture, root

    def test_manhattan_serialization_has_no_diagonal_segments(self):
        _, root = self.dense_fixture()
        ids = {c.get("id"): c for c in root.findall(".//mxCell")}
        edges = root.findall(".//mxCell[@edge='1']")
        self.assertEqual(len(edges), 40)
        for edge in edges:
            with self.subTest(edge=edge.get("id")):
                style = dict(p.split("=", 1) for p in edge.get("style").split(";") if "=" in p)
                self.assertEqual(style.get("noEdgeStyle"), "1")
                self.assertEqual(style.get("edgeStyle"), "none")
                self.assertEqual(style.get("exitPerimeter"), "0")
                self.assertEqual(style.get("entryPerimeter"), "0")
                for key in ("exitX", "exitY", "entryX", "entryY"):
                    self.assertGreaterEqual(float(style[key]), 0)
                    self.assertLessEqual(float(style[key]), 1)
                pts = [validate.endpoint(edge, "source", ids)] + validate.edge_waypoints(edge) + [validate.endpoint(edge, "target", ids)]
                vectors = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
                for dx, dy in vectors:
                    self.assertTrue(abs(dx) < 1e-6 or abs(dy) < 1e-6, (dx, dy))
                for a, b in zip(vectors, vectors[1:]):
                    self.assertAlmostEqual(a[0] * b[0] + a[1] * b[1], 0, places=5)

    def test_dense_graph_identity_and_node_geometry_are_preserved(self):
        fixture, root = self.dense_fixture()
        graph = fixture["graph"]
        vertices = root.findall(".//mxCell[@vertex='1']")
        actual = {c.get("id"): {"attributes": dict(c.attrib), "geometry": dict(c.find("mxGeometry").attrib)} for c in vertices}
        self.assertEqual(actual, fixture["expected_vertices"])
        self.assertEqual(len(vertices), 31)  # 26 semantic nodes + 5 groups
        ir = diagram_ir.normalize_ir(graph)
        diagram_ir._annotate_page(root.find("diagram"), ir)
        self.assertEqual({c.get("id"): c.get("value") for c in vertices if c.get("id") in {n["id"] for n in graph["nodes"]}},
                         {n["id"]: n["label"] for n in graph["nodes"]})
        edges = root.findall(".//mxCell[@edge='1']")
        self.assertEqual([(e.get("data-model-id"), e.get("source"), e.get("target"), e.get("value")) for e in edges],
                         [(e["id"], e["source"], e["target"], e["label"]) for e in graph["edges"]])
