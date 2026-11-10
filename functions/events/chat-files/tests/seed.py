"""Seed a throwaway Open WebUI with what the Chat Files browser tests need.

    python tests/seed.py http://127.0.0.1:8080 chat_files.py [chat_gallery.py] > seed.json

Run it against an instance with no users yet: the first account it makes
becomes the administrator. With a Chat Gallery source as the third argument it
installs that too, to test the two side by side. It needs Pillow. Never point
it at an instance people use: the tests delete and rename files there.
"""

import io
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
import zipfile

from PIL import Image, ImageDraw

BASE = sys.argv[1].rstrip("/")
SOURCE = open(sys.argv[2], encoding="utf-8").read()
GALLERY = open(sys.argv[3], encoding="utf-8").read() if len(sys.argv) > 3 else None
PASSWORD = "files-test-password"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, token=None, body=None, files=None, ok=(200,)):
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
                # Browsers send the name as raw UTF-8 in quotes; Open WebUI's form parser expects that
                head += '; filename="' + filename.replace('"', '%22') + '"'
            head += f"\r\nContent-Type: {content_type}\r\n\r\n"
            parts += [head.encode(), content, b"\r\n"]
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if error.code in ok:
            return None
        raise SystemExit(f"{method} {path} -> {error.code}: {error.read()[:300]!r}")
    return json.loads(raw) if raw else None


def upload(token, filename, content, content_type, process=False):
    return call("POST", "/api/v1/files/?process=" + ("true" if process else "false"), token,
                files={"file": (filename, content, content_type)})


def pdf_bytes(label):
    image = Image.new("RGB", (595, 842), "#ffffff")
    ImageDraw.Draw(image).text((60, 60), label, fill="#000000")
    out = io.BytesIO()
    image.save(out, "PDF")
    return out.getvalue()


def png_bytes():
    image = Image.new("RGB", (320, 200), "#3366cc")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def wav_bytes(seconds=1.0, rate=8000):
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(rate * seconds))))
    return out.getvalue()


def docx_bytes(text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        package.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def zip_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr("inside.txt", "inside the archive\n")
    return out.getvalue()


def signup(name, email):
    return call("POST", "/api/v1/auths/signup", body={"name": name, "email": email, "password": PASSWORD})


def install(token, function_id, name, source):
    call("POST", "/api/v1/functions/create", token, {"id": function_id, "name": name, "content": source, "meta": {"description": name + " under test"}})
    call("POST", f"/api/v1/functions/id/{function_id}/toggle", token)


admin = signup("Files Admin", "admin@example.test")
admin_token = admin["token"]
install(admin_token, "chat_files", "Chat Files", SOURCE)
if GALLERY:
    install(admin_token, "chat_gallery", "Chat Gallery", GALLERY)
call("POST", "/api/v1/auths/add", admin_token, {"name": "Files User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user_token = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]


def ref(record):
    return {"type": "file", "id": record["id"], "url": record["id"], "name": record["meta"]["name"],
            "size": record["meta"].get("size"), "content_type": record["meta"].get("content_type"), "file": record, "status": "uploaded"}


def make_chat(token, title, messages, current):
    chat_id = str(uuid.uuid4())
    chat = call("POST", "/api/v1/chats/new", token, {"chat": {
        "id": chat_id, "title": title, "models": ["test-model"], "params": {}, "files": [], "tags": [],
        "history": {"messages": messages, "currentId": current},
        "messages": [], "timestamp": int(time.time() * 1000),
    }})
    return chat["id"]


def seed_user(token):
    now = int(time.time())
    long_name = "a-very-long-file-name-" + "that-keeps-going-" * 10 + "end.txt"
    made = {
        "pdf": upload(token, "report.pdf", pdf_bytes("Quarterly report"), "application/pdf"),
        "pdf2": upload(token, "report.pdf", pdf_bytes("Second report"), "application/pdf"),
        "csv": upload(token, "data.csv", 'name,city,note\n"Smith, Jane",Évry,"said ""hello"""\nAli,القاهرة,"two\nlines"\n'.encode(), "text/csv"),
        "semicolon": upload(token, "ventes.csv", "produit;prix;quantité\npomme;1,20;3\npoire;0,95;7\n".encode(), "text/csv"),
        "json": upload(token, "config.json", b'{"name":"demo","items":[1,2,3],"nested":{"ok":true}}', "application/json"),
        "py": upload(token, "script.py", b"def hello():\n    print('hi')\n", "text/x-python"),
        "docx": upload(token, "notes.docx", docx_bytes("Minutes of the planning meeting"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", process=True),
        "pptx": upload(token, "slides.pptx", b"not really a presentation" * 40, "application/vnd.openxmlformats-officedocument.presentationml.presentation", process=True),
        "wav": upload(token, "tone.wav", wav_bytes(), "audio/wav"),
        "zip": upload(token, "archive.zip", zip_bytes(), "application/zip"),
        "log": upload(token, "big.log", b"".join(b"%07d line of a long log file\n" % i for i in range(110000)), "text/plain"),
        "bin": upload(token, "data.bin", bytes(range(256)) * 64, "application/octet-stream"),
        "fake": upload(token, "fake.pdf", b"<html><script>window.__pwned=1</script><h1>not a pdf</h1></html>", "application/pdf"),
        "arabic": upload(token, "تقرير المشروع.txt", "مرحبا بالعالم\nسطر ثان\n".encode(), "text/plain"),
        "long": upload(token, long_name, b"long name\n", "text/plain"),
        "md": upload(token, "summary.md", b"# Summary\n\nWritten by a tool.\n", "text/markdown"),
        "branch": upload(token, "branch-only.txt", b"only on the other branch\n", "text/plain"),
        "image": upload(token, "photo.png", png_bytes(), "image/png"),
        "gone": upload(token, "deleted-later.txt", b"soon gone\n", "text/plain"),
    }
    # Plain users can't create knowledge bases by default; an administrator's shared one is the usual case
    knowledge = call("POST", "/api/v1/knowledge/create", admin_token, {"name": "Course reading list", "description": "for the test"}, ok=(200, 401, 403))
    note = call("POST", "/api/v1/notes/create", token, {"title": "Meeting notes", "data": {"content": {"md": "notes"}}}, ok=(200, 401, 403))
    refs = []
    if knowledge:
        refs.append({"type": "collection", "id": knowledge["id"], "name": knowledge["name"]})
    if note:
        refs.append({"type": "note", "id": note["id"], "name": note["title"]})
    refs.append({"type": "web", "url": "https://linked.example.test/article", "name": "An article"})
    messages = {
        "m1": {"id": "m1", "parentId": None, "childrenIds": ["m2", "m2b"], "role": "user", "timestamp": now - 500, "content": "Here are my files.",
               "files": [ref(made[k]) for k in ("pdf", "csv", "docx", "image")]},
        "m2": {"id": "m2", "parentId": "m1", "childrenIds": ["m3"], "role": "assistant", "timestamp": now - 490, "model": "test-model", "done": True,
               "content": f"I wrote [a summary](/api/v1/files/{made['md']['id']}/content) and drew [this](/api/v1/files/{made['image']['id']}/content)."},
        "m2b": {"id": "m2b", "parentId": "m1", "childrenIds": [], "role": "assistant", "timestamp": now - 480, "model": "test-model", "done": True,
                "content": "Another answer.", "files": [ref(made["branch"])]},
        "m3": {"id": "m3", "parentId": "m2", "childrenIds": ["m4"], "role": "user", "timestamp": now - 400, "content": "More files.",
               "files": refs + [ref(made[k]) for k in ("pdf2", "semicolon", "json", "py", "pptx", "wav", "zip", "log", "bin", "fake", "arabic", "long", "gone")]},
        "m4": {"id": "m4", "parentId": "m3", "childrenIds": [], "role": "assistant", "timestamp": now - 390, "model": "test-model", "done": True, "content": "Thanks."},
    }
    chat = make_chat(token, "Files test chat", messages, "m4")
    empty = make_chat(token, "Empty chat", {}, None)
    call("DELETE", f"/api/v1/files/{made['gone']['id']}", token)
    # Enough files to need a second page in My files
    extras = [upload(token, f"extra-{i:03d}.txt", f"extra file {i}\n".encode(), "text/plain")["id"] for i in range(230)]
    in_chat = [k for k in made if k not in ("image",)]
    return {
        "chat": chat, "empty": empty, "files": {k: v["id"] for k, v in made.items()}, "extras": extras,
        "knowledge": knowledge["id"] if knowledge else None, "note": note["id"] if note else None,
        "expect": {"chat_files": len(in_chat), "chat_refs": len(refs), "library_files": len(made) - 1 - 1 + len(extras)},
        "statuses": {k: (call("GET", f"/api/v1/files/{made[k]['id']}", token) or {}).get("data", {}).get("status") for k in ("docx", "pptx")},
    }


def seed_admin(token):
    own = upload(token, "admin-only.pdf", pdf_bytes("Admin file"), "application/pdf")
    messages = {"m1": {"id": "m1", "parentId": None, "childrenIds": [], "role": "user", "timestamp": int(time.time()), "content": "A file.", "files": [ref(own)]}}
    return {"chat": make_chat(token, "Admin chat", messages, "m1"), "files": {"own": own["id"]}}


user = seed_user(user_token)
print(json.dumps({
    "base": BASE, "password": PASSWORD, "gallery": bool(GALLERY),
    "admin": {"email": "admin@example.test", "token": admin_token, **seed_admin(admin_token)},
    "user": {"email": "user@example.test", "token": user_token, **user},
}, indent=2))
