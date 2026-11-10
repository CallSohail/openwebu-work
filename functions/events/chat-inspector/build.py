"""Rebuild the single-file Open WebUI distribution from reviewed sources."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
GALLERY = HERE.parent / 'chat-gallery' / 'chat_gallery.py'
source = GALLERY.read_text(encoding='utf-8')
start = source.index('import json as _json')
end_marker = '# =========================== end shared asset block ========================'
end = source.index(end_marker) + len(end_marker)
shared = source[start:end]
js = (HERE / 'inspector.js').read_text(encoding='utf-8')
header = '''"""
title: Chat Inspector
description: Inspect live chat context estimates, response usage, and saved model and tool settings.
author: Muhammad Sohail
version: 0.2.0
license: MIT
required_open_webui_version: 0.13.3
"""

# Shared Assets Protocol and live reload blocks are by Classic298 (BSD-3-Clause),
# copied byte-for-byte from the Chat Gallery distribution. See its attribution.
'''
footer = '''

class Event:
    class Valves(BaseModel):
        pass

    LOADER_BLOCK_START = "// owui-chat-inspector:start"
    LOADER_BLOCK_END = "// owui-chat-inspector:end"
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        self._fragment = self.LOADER_BLOCK_START + "\\n" + LOADER_SCRIPT + "\\n" + self.LOADER_BLOCK_END
        reload_bootstrap()

    def _publish(self, app):
        asset_register(app, LOADER_PATH, self.LOADER_BLOCK_START, self.LOADER_BLOCK_END,
                       lambda: Event.instance._fragment if reload_active(app) else "")

    async def event(self, event: Optional[dict] = None, __event_name__: str = "",
                    __app__: Any = None, **kwargs) -> None:
        if __app__ is not None:
            await reload_on_event(__app__, event, __event_name__)


def register(app: Any) -> None:
    Event.instance._publish(app)
'''
(HERE / 'chat_inspector.py').write_text(header + shared + '\n\nLOADER_SCRIPT = ' + repr(js) + '\n' + footer, encoding='utf-8')
