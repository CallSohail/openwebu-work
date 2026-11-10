"""Seed a throwaway Open WebUI with what the Chat Gallery browser test needs.

    python tests/seed.py http://127.0.0.1:8080 chat_gallery.py > seed.json

Run it against an instance with no users yet: the first account it makes
becomes the administrator. It needs Pillow, and nothing else outside the
standard library. Never point it at an instance people use.
"""

import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

BASE = sys.argv[1].rstrip("/")
SOURCE = open(sys.argv[2], encoding="utf-8").read()
PASSWORD = "gallery-test-password"


def call(method, path, token=None, body=None, files=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, (filename, content, content_type) in files.items():
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            if filename:
                head += f'; filename="{filename}"'
            head += f"\r\nContent-Type: {content_type}\r\n\r\n"
            parts += [head.encode(), content, b"\r\n"]
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path} -> {error.code}: {error.read()[:300]!r}")
    return json.loads(raw) if raw else None


def picture(size, colors, label, fmt="PNG", info=None, quality=90):
    image = Image.new("RGB", size, colors[0])
    draw = ImageDraw.Draw(image)
    width, height = size
    # Diagonal bands, so the files don't compress to nothing and thumbnails have something to show
    step = max(8, width // 40)
    for x in range(-height, width, step * 2):
        draw.polygon([(x, 0), (x + step, 0), (x + step + height, height), (x + height, height)], fill=colors[1])
    draw.rectangle([width // 4, height // 3, width * 3 // 4, height * 2 // 3], fill=colors[0])
    draw.text((width // 4 + 10, height // 3 + 10), label, fill=colors[1])
    out = io.BytesIO()
    if fmt == "PNG":
        image.save(out, "PNG", pnginfo=info)
    else:
        image.save(out, fmt, quality=quality)
    return out.getvalue()


def upload(token, filename, content, content_type, metadata=None):
    files = {"file": (filename, content, content_type)}
    if metadata is not None:
        files["metadata"] = ("", json.dumps(metadata).encode(), "text/plain")
    return call("POST", "/api/v1/files/?process=false", token, files=files)


def signup(name, email):
    return call("POST", "/api/v1/auths/signup", body={"name": name, "email": email, "password": PASSWORD})


admin = signup("Gallery Admin", "admin@example.test")
token = admin["token"]

call("POST", "/api/v1/functions/create", token, {
    "id": "chat_gallery",
    "name": "Chat Gallery",
    "content": SOURCE,
    "meta": {"description": "Chat Gallery under test"},
})
call("POST", "/api/v1/functions/id/chat_gallery/toggle", token)

# A plain user: no side chat, so no + menu, which is the case the header button exists for
call("POST", "/api/v1/auths/add", token, {
    "name": "Gallery User", "email": "user@example.test", "password": PASSWORD, "role": "user",
})
user = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})


def seed_account(token, tag):
    chat_id = str(uuid.uuid4())
    now = int(time.time())
    a1111 = PngInfo()
    a1111.add_text(
        "parameters",
        "desert dunes at golden hour, café\nNegative prompt: blurry, text\n"
        "Steps: 28, Sampler: DPM++ 2M, Schedule type: Karras, CFG scale: 6.5, Seed: 424242, Size: 768x512, Model: sdxl_base_1.0",
    )
    comfy = PngInfo()
    comfy.add_text("prompt", json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": 77, "steps": 20, "cfg": 7, "sampler_name": "euler",
                                                    "scheduler": "normal", "positive": ["6", 0], "negative": ["7", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "models/dreamshaper_8.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a mountain lake at dawn"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "people"}},
    }))
    made = {}
    # Uploaded by the person: a large phone-sized photo, a small screenshot, and a document that is not an image
    made["photo"] = upload(token, "holiday-photo.jpg", picture((4000, 3000), ("#234", "#e96"), "photo", "JPEG"), "image/jpeg")
    made["shot"] = upload(token, "screenshot.png", picture((640, 400), ("#fff", "#36c"), "screenshot"), "image/png")
    made["doc"] = upload(token, "notes.txt", b"not an image\n" * 50, "text/plain")
    # Generated: named as Open WebUI names them, with the metadata its image router stores
    made["gen1"] = upload(token, "generated-image.png", picture((768, 512), ("#c84", "#421"), "dunes", info=a1111), "image/png",
                          {"prompt": "desert dunes at golden hour", "model": "sdxl_base_1.0", "chat_id": chat_id, "message_id": "m2"})
    made["gen2"] = upload(token, "generated-image.png", picture((1024, 1024), ("#135", "#8cf"), "lake", info=comfy), "image/png",
                          {"chat_id": chat_id, "message_id": "m2"})
    time.sleep(1.1)
    extras = [upload(token, "generated-image.png", picture((1536, 1024), ("#%03x" % (i * 291 % 4096), "#eee"), f"extra {i}"), "image/png",
                     {"prompt": f"extra picture number {i}", "model": "test-model"}) for i in range(8)]

    def ref(item):
        return {"type": "image", "url": item["id"], "name": item["meta"]["name"], "id": item["id"], "content_type": item["meta"]["content_type"]}

    messages = {
        "m1": {"id": "m1", "parentId": None, "childrenIds": ["m2"], "role": "user", "timestamp": now - 300,
               "content": "Here is my holiday photo and a screenshot.", "files": [ref(made["photo"]), ref(made["shot"]),
                                                                              {"type": "file", "id": made["doc"]["id"], "name": "notes.txt", "content_type": "text/plain"}]},
        "m2": {"id": "m2", "parentId": "m1", "childrenIds": ["m3"], "role": "assistant", "timestamp": now - 290, "model": "test-model",
               "content": "Two images for you.", "files": [{"type": "image", "url": f"/api/v1/files/{made['gen1']['id']}/content"},
                                                           {"type": "image", "url": f"/api/v1/files/{made['gen2']['id']}/content"}], "done": True},
        "m3": {"id": "m3", "parentId": "m2", "childrenIds": ["m4"], "role": "user", "timestamp": now - 200, "content": "Any from the web?"},
        # The last address looks local but leaves the site: the gallery must treat it as a link and never send it the sign-in token
        "m4": {"id": "m4", "parentId": "m3", "childrenIds": [], "role": "assistant", "timestamp": now - 190, "model": "test-model", "done": True,
               "content": "![A linked picture](https://linked.example.test/cat.png)\n\n![again](/api/v1/files/%s/content)\n\n"
                          "![trap](/\\\\evil.example.test/steal.png)\n\n[not an image](javascript:alert(1))" % made["gen1"]["id"]},
    }
    chat = call("POST", "/api/v1/chats/new", token, {"chat": {
        "id": chat_id, "title": f"Gallery test {tag}", "models": ["test-model"], "params": {}, "files": [], "tags": [],
        "history": {"messages": messages, "currentId": "m4"},
        "messages": [messages[key] for key in ("m1", "m2", "m3", "m4")],
        "timestamp": now * 1000,
    }})
    empty = call("POST", "/api/v1/chats/new", token, {"chat": {
        "title": f"Empty chat {tag}", "models": ["test-model"], "history": {"messages": {}, "currentId": None}, "messages": [],
    }})
    return {"chat": chat["id"], "empty": empty["id"], "files": {key: value["id"] for key, value in made.items()},
            "extras": [item["id"] for item in extras]}


print(json.dumps({
    "base": BASE,
    "password": PASSWORD,
    "admin": {"email": "admin@example.test", "token": token, **seed_account(token, "admin")},
    "user": {"email": "user@example.test", "token": user["token"], **seed_account(user["token"], "user")},
}, indent=2))
