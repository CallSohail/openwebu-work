"""Distribution and shared-loader tests; run with python -m unittest discover -s tests."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import types
import unittest
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / 'chat_inspector.py'
SOURCE = FILE.read_text(encoding='utf8')


def load_plugin():
    for name in ('open_webui', 'open_webui.utils'):
        sys.modules.setdefault(name, types.ModuleType(name))
    codec = types.ModuleType('open_webui.utils.json_codec')
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules['open_webui.utils.json_codec'] = codec
    name = 'function_chat_inspector_' + uuid.uuid4().hex
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    sys.modules[name] = module
    exec(compile(SOURCE, str(FILE), 'exec'), module.__dict__)
    return module


class InspectorTests(unittest.TestCase):
    def test_distribution_reproducible(self):
        subprocess.run([sys.executable, str(ROOT / 'build.py')], check=True)
        self.assertEqual(FILE.read_text(encoding='utf8'), SOURCE)

    def test_shared_protocol_hashes(self):
        expected = {
            '# Live reload broadcast': ('# =========================== end live reload block =========================', 'fea3dadb266175baf8ae83d3f330fb72311d09971bb08f245e1304431f45db39'),
            '# Shared static-asset registry': ('# =========================== end shared asset block ========================', '8293a34b015c84e9f53ac44a3cceabea6b190a2489fd55f8721f8427ae9c9cc9'),
        }
        for title, (marker, digest) in expected.items():
            start = SOURCE.rindex('# ====', 0, SOURCE.index(title))
            block = SOURCE[start:SOURCE.index(marker) + len(marker)] + '\n'
            self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), digest)

    def test_asset_is_composable_and_withdrawn(self):
        plugin = load_plugin()
        app = Starlette()
        plugin.Event()
        plugin.reload_mark_loaded(app)
        client = TestClient(app)
        response = client.get('/static/loader.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('// owui-chat-inspector:start', response.text)
        self.assertIn('Chat inspector', response.text)
        self.assertEqual(client.get('/static/loader.js', headers={'If-None-Match': response.headers['etag']}).status_code, 304)
        plugin.reload_state(app)['active'] = False
        self.assertNotIn('// owui-chat-inspector:start', client.get('/static/loader.js').text)

    def test_gallery_can_share_loader_and_survive_inspector_disable(self):
        plugin = load_plugin()
        gallery_file = ROOT.parent / 'chat-gallery' / 'chat_gallery.py'
        gallery_name = 'function_chat_gallery_' + uuid.uuid4().hex
        gallery = importlib.util.module_from_spec(importlib.util.spec_from_loader(gallery_name, loader=None))
        sys.modules[gallery_name] = gallery
        exec(compile(gallery_file.read_text(encoding='utf8'), str(gallery_file), 'exec'), gallery.__dict__)
        app = Starlette()
        plugin.Event()
        gallery.Event()
        plugin.reload_mark_loaded(app)
        gallery.reload_mark_loaded(app)
        client = TestClient(app)
        script = client.get('/static/loader.js').text
        self.assertIn('// owui-chat-inspector:start', script)
        self.assertIn('// owui-chat-gallery:start', script)
        plugin.reload_state(app)['active'] = False
        script = client.get('/static/loader.js').text
        self.assertNotIn('// owui-chat-inspector:start', script)
        self.assertIn('// owui-chat-gallery:start', script)

    def test_javascript_matches_source(self):
        plugin = load_plugin()
        self.assertEqual(plugin.LOADER_SCRIPT, (ROOT / 'inspector.js').read_text(encoding='utf8'))
        self.assertEqual(plugin.RELOAD_FUNCTION_ID, 'chat_inspector')


if __name__ == '__main__':
    unittest.main()
