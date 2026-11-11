import os, re, glob, subprocess, tempfile
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import PlainTextResponse

TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_ID = os.environ["PHONE_NUMBER_ID"]
VERIFY = os.environ["VERIFY_TOKEN"]
API = f"https://graph.facebook.com/v21.0/{PHONE_ID}"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/\S+/status/\d+\S*")
MAX_BYTES = 500 * 1024
# (fps, quality, max seconds), tried in order until the file fits
ATTEMPTS = [(15, 50, 8), (12, 40, 8), (10, 30, 6), (8, 20, 5), (6, 15, 4)]

app = FastAPI()


@app.get("/")
def home():
    return {"ok": True}


@app.get("/webhook")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY:
        return PlainTextResponse(q.get("hub.challenge", ""))
    return Response(status_code=403)


@app.post("/webhook")
async def webhook(request: Request, bg: BackgroundTasks):
    data = await request.json()
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        if msg["type"] == "text":
            bg.add_task(handle, msg["from"], msg["text"]["body"])
    except (KeyError, IndexError):
        pass
    return {"status": "ok"}


def send(to, payload):
    body = {"messaging_product": "whatsapp", "to": to, **payload}
    httpx.post(f"{API}/messages", headers=HEADERS, json=body, timeout=30)


def say(to, text):
    send(to, {"type": "text", "text": {"body": text}})


def download(url, d):
    out = os.path.join(d, "in.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-f", "best[ext=mp4]/best", "--no-playlist", "-o", out, url],
        check=True, capture_output=True, timeout=90,
    )
    return glob.glob(os.path.join(d, "in.*"))[0]


def convert(src, d):
    dst = os.path.join(d, "out.webp")
    for fps, q, secs in ATTEMPTS:
        vf = (
            f"fps={fps},scale=512:512:force_original_aspect_ratio=decrease,"
            "format=rgba,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-t", str(secs), "-vf", vf,
             "-c:v", "libwebp", "-q:v", str(q), "-compression_level", "6",
             "-loop", "0", "-an", dst],
            check=True, capture_output=True, timeout=120,
        )
        if os.path.getsize(dst) <= MAX_BYTES:
            return dst
    return None


def handle(to, text):
    m = URL_RE.search(text)
    if not m:
        say(to, "Send me a link to an X post that has a GIF in it.")
        return
    try:
        with tempfile.TemporaryDirectory() as d:
            src = download(m.group(0), d)
            webp = convert(src, d)
            if not webp:
                say(to, "Couldn't shrink it under 500KB. Try a shorter GIF.")
                return
            with open(webp, "rb") as f:
                r = httpx.post(
                    f"{API}/media", headers=HEADERS,
                    data={"messaging_product": "whatsapp", "type": "image/webp"},
                    files={"file": ("sticker.webp", f, "image/webp")},
                    timeout=60,
                )
            r.raise_for_status()
            send(to, {"type": "sticker", "sticker": {"id": r.json()["id"]}})
    except Exception as e:
        print("error:", repr(e))
        say(to, "Something broke. Make sure the post is public and has a GIF or video.")
