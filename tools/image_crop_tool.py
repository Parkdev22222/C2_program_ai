"""
이미지 크롭 도구 (Image Crop Tool)

브라우저 UI에서 이미지를 업로드하고, 마우스로 드래그하여 사각형 영역을
선택하면 해당 부분을 크롭하고, 크롭 영역의 원본 픽셀 좌표를 파일로 저장합니다.

특징:
- 외부 의존성 없음 (파이썬 표준 라이브러리만 사용)
- 마우스 드래그로 네모 박스 선택 → 실시간 미리보기
- 크롭 이미지(PNG)와 픽셀 좌표(JSON)를 함께 저장

사용법:
  python tools/image_crop_tool.py
  # 또는 포트/저장경로 지정
  python tools/image_crop_tool.py --port 7870 --out data/crops

브라우저에서 http://127.0.0.1:7870 접속 후 이미지를 업로드하고 드래그하세요.

저장 결과 (기본 경로: data/crops/):
  - crop_<타임스탬프>.png   : 크롭된 이미지
  - crop_<타임스탬프>.json  : 크롭 영역의 픽셀 좌표 및 메타데이터
"""
import argparse
import base64
import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

# 저장 경로 (main() 에서 갱신)
OUTPUT_DIR = Path("data/crops")


# ─────────────────────────────────────────────
# 프론트엔드 (HTML + JavaScript 캔버스)
# ─────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>이미지 크롭 도구</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
    margin: 0; padding: 24px; background: #0f172a; color: #e2e8f0;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p.sub { margin: 0 0 20px; color: #94a3b8; font-size: 13px; }
  .toolbar {
    display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    margin-bottom: 16px;
  }
  button, .filebtn {
    background: #334155; color: #e2e8f0; border: 1px solid #475569;
    padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px;
  }
  button:hover, .filebtn:hover { background: #475569; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
  button.primary:hover:not(:disabled) { background: #1d4ed8; }
  #stageWrap {
    position: relative; display: inline-block; max-width: 100%;
    border: 1px dashed #334155; border-radius: 8px; overflow: hidden;
  }
  #stage { display: block; max-width: 100%; height: auto; cursor: crosshair; }
  #overlay { position: absolute; left: 0; top: 0; pointer-events: none; }
  .panel {
    display: flex; gap: 24px; flex-wrap: wrap; margin-top: 20px;
    align-items: flex-start;
  }
  .card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 16px; min-width: 260px;
  }
  .card h2 { font-size: 14px; margin: 0 0 12px; color: #cbd5e1; }
  #coords { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
            font-size: 13px; line-height: 1.7; white-space: pre; color: #7dd3fc; }
  #preview { max-width: 240px; max-height: 240px; border-radius: 6px;
             border: 1px solid #334155; display: none; }
  #status { margin-top: 12px; font-size: 13px; min-height: 18px; }
  .ok { color: #4ade80; } .err { color: #f87171; }
  input[type=file] { display: none; }
  code { background: #0b1120; padding: 1px 6px; border-radius: 4px; color: #93c5fd; }
</style>
</head>
<body>
  <h1>🖼️ 이미지 크롭 도구</h1>
  <p class="sub">이미지를 업로드한 뒤 <b>마우스로 드래그</b>하여 네모 박스 영역을 선택하세요.
     크롭 이미지와 픽셀 좌표가 서버에 파일로 저장됩니다.</p>

  <div class="toolbar">
    <label class="filebtn">📁 이미지 업로드
      <input type="file" id="fileInput" accept="image/*">
    </label>
    <button id="resetBtn" disabled>선택 초기화</button>
    <button id="saveBtn" class="primary" disabled>💾 크롭 & 좌표 저장</button>
  </div>

  <div id="stageWrap" style="display:none;">
    <img id="stage" alt="uploaded">
    <canvas id="overlay"></canvas>
  </div>

  <div class="panel">
    <div class="card">
      <h2>📐 픽셀 좌표 (원본 기준)</h2>
      <div id="coords">영역을 드래그하면 좌표가 표시됩니다.</div>
      <div id="status"></div>
    </div>
    <div class="card">
      <h2>✂️ 크롭 미리보기</h2>
      <img id="preview" alt="crop preview">
    </div>
  </div>

<script>
const fileInput = document.getElementById('fileInput');
const stageWrap = document.getElementById('stageWrap');
const img       = document.getElementById('stage');
const overlay   = document.getElementById('overlay');
const octx      = overlay.getContext('2d');
const coordsEl  = document.getElementById('coords');
const statusEl  = document.getElementById('status');
const previewEl = document.getElementById('preview');
const saveBtn   = document.getElementById('saveBtn');
const resetBtn  = document.getElementById('resetBtn');

let dragging = false;
let start = null;      // 시작점 (표시 좌표)
let box = null;        // 최종 선택 박스 (원본 픽셀 좌표): {x,y,w,h}
let currentName = "";  // 업로드한 파일 이름

// 표시(display) 좌표 → 원본(natural) 픽셀 좌표 변환 비율
function scale() {
  return {
    sx: img.naturalWidth  / img.clientWidth,
    sy: img.naturalHeight / img.clientHeight,
  };
}

function syncOverlaySize() {
  overlay.width  = img.clientWidth;
  overlay.height = img.clientHeight;
  overlay.style.width  = img.clientWidth + 'px';
  overlay.style.height = img.clientHeight + 'px';
}

function clearOverlay() {
  octx.clearRect(0, 0, overlay.width, overlay.height);
}

function drawRect(x, y, w, h) {
  clearOverlay();
  octx.save();
  // 바깥 어둡게
  octx.fillStyle = 'rgba(0,0,0,0.45)';
  octx.fillRect(0, 0, overlay.width, overlay.height);
  octx.clearRect(x, y, w, h);
  // 테두리
  octx.strokeStyle = '#38bdf8';
  octx.lineWidth = 2;
  octx.strokeRect(x + 0.5, y + 0.5, w, h);
  octx.restore();
}

function relPos(e) {
  const r = img.getBoundingClientRect();
  const cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const cy = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
  return {
    x: Math.max(0, Math.min(cx, img.clientWidth)),
    y: Math.max(0, Math.min(cy, img.clientHeight)),
  };
}

function onDown(e) {
  if (!img.src) return;
  e.preventDefault();
  dragging = true;
  start = relPos(e);
  box = null;
  saveBtn.disabled = true;
}

function onMove(e) {
  if (!dragging) return;
  e.preventDefault();
  const p = relPos(e);
  const x = Math.min(start.x, p.x), y = Math.min(start.y, p.y);
  const w = Math.abs(p.x - start.x), h = Math.abs(p.y - start.y);
  drawRect(x, y, w, h);
}

function onUp(e) {
  if (!dragging) return;
  dragging = false;
  const p = relPos(e);
  const dx = Math.min(start.x, p.x), dy = Math.min(start.y, p.y);
  const dw = Math.abs(p.x - start.x), dh = Math.abs(p.y - start.y);
  if (dw < 3 || dh < 3) {   // 너무 작은 선택은 무시
    clearOverlay(); box = null; coordsEl.textContent = '영역을 드래그하면 좌표가 표시됩니다.';
    previewEl.style.display = 'none'; saveBtn.disabled = true; return;
  }
  const s = scale();
  // 원본 픽셀 좌표로 변환 + 이미지 경계로 클램프
  let px = Math.round(dx * s.sx);
  let py = Math.round(dy * s.sy);
  let pw = Math.round(dw * s.sx);
  let ph = Math.round(dh * s.sy);
  px = Math.max(0, Math.min(px, img.naturalWidth));
  py = Math.max(0, Math.min(py, img.naturalHeight));
  pw = Math.min(pw, img.naturalWidth  - px);
  ph = Math.min(ph, img.naturalHeight - py);
  box = { x: px, y: py, w: pw, h: ph };
  showCoords();
  makePreview();
  saveBtn.disabled = false;
}

function showCoords() {
  coordsEl.textContent =
    `x      : ${box.x}\n` +
    `y      : ${box.y}\n` +
    `width  : ${box.w}\n` +
    `height : ${box.h}\n` +
    `x2     : ${box.x + box.w}\n` +
    `y2     : ${box.y + box.h}\n` +
    `원본크기: ${img.naturalWidth} x ${img.naturalHeight}`;
}

// 크롭 결과를 offscreen 캔버스로 생성 → dataURL 반환
function croppedDataURL() {
  const c = document.createElement('canvas');
  c.width = box.w; c.height = box.h;
  const cx = c.getContext('2d');
  cx.drawImage(img, box.x, box.y, box.w, box.h, 0, 0, box.w, box.h);
  return c.toDataURL('image/png');
}

function makePreview() {
  previewEl.src = croppedDataURL();
  previewEl.style.display = 'block';
}

async function save() {
  if (!box) return;
  saveBtn.disabled = true;
  statusEl.textContent = '저장 중...';
  statusEl.className = '';
  try {
    const payload = {
      source_name: currentName,
      image_width: img.naturalWidth,
      image_height: img.naturalHeight,
      box: box,
      crop_png: croppedDataURL(),
    };
    const res = await fetch('/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      statusEl.className = 'ok';
      statusEl.textContent = '✅ 저장 완료: ' + data.image_file + ' / ' + data.coords_file;
    } else {
      statusEl.className = 'err';
      statusEl.textContent = '❌ 저장 실패: ' + (data.error || 'unknown');
    }
  } catch (err) {
    statusEl.className = 'err';
    statusEl.textContent = '❌ 오류: ' + err;
  } finally {
    saveBtn.disabled = false;
  }
}

function reset() {
  clearOverlay(); box = null;
  coordsEl.textContent = '영역을 드래그하면 좌표가 표시됩니다.';
  previewEl.style.display = 'none';
  saveBtn.disabled = true; statusEl.textContent = '';
}

fileInput.addEventListener('change', (e) => {
  const f = e.target.files[0];
  if (!f) return;
  currentName = f.name;
  const url = URL.createObjectURL(f);
  img.onload = () => { syncOverlaySize(); reset(); resetBtn.disabled = false; };
  img.src = url;
  stageWrap.style.display = 'inline-block';
});

// 마우스 + 터치 이벤트
img.addEventListener('mousedown', onDown);
window.addEventListener('mousemove', onMove);
window.addEventListener('mouseup', onUp);
img.addEventListener('touchstart', onDown, { passive: false });
window.addEventListener('touchmove', onMove, { passive: false });
window.addEventListener('touchend', onUp);

window.addEventListener('resize', () => { if (img.src) syncOverlaySize(); });
resetBtn.addEventListener('click', reset);
saveBtn.addEventListener('click', save);
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────
# 백엔드 (요청 핸들러)
# ─────────────────────────────────────────────
class CropRequestHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/save":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = save_crop(payload)
            self._send_json(200, {"ok": True, **result})
        except Exception as exc:  # noqa: BLE001
            logger.exception("크롭 저장 실패")
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt, *args):  # 서버 로그를 조용하게
        logger.debug("%s - %s", self.address_string(), fmt % args)


def save_crop(payload: dict) -> dict:
    """크롭 이미지(PNG)와 좌표(JSON)를 파일로 저장하고 파일명을 반환합니다."""
    box = payload["box"]
    data_url = payload["crop_png"]
    # data:image/png;base64,.... 에서 base64 부분만 추출
    b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
    png_bytes = base64.b64decode(b64)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    img_path = OUTPUT_DIR / f"crop_{stamp}.png"
    json_path = OUTPUT_DIR / f"crop_{stamp}.json"

    img_path.write_bytes(png_bytes)

    coords = {
        "source_name": payload.get("source_name", ""),
        "image_width": payload.get("image_width"),
        "image_height": payload.get("image_height"),
        "crop": {
            "x": box["x"],
            "y": box["y"],
            "width": box["w"],
            "height": box["h"],
            "x2": box["x"] + box["w"],
            "y2": box["y"] + box["h"],
        },
        "crop_image_file": img_path.name,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    json_path.write_text(json.dumps(coords, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("크롭 저장: %s (좌표 %s)", img_path, coords["crop"])
    return {"image_file": str(img_path), "coords_file": str(json_path)}


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────
def main():
    global OUTPUT_DIR
    parser = argparse.ArgumentParser(description="이미지 드래그 크롭 & 좌표 저장 도구")
    parser.add_argument("--host", default="127.0.0.1", help="바인드 호스트 (기본 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7870, help="포트 (기본 7870)")
    parser.add_argument("--out", default="data/crops", help="저장 경로 (기본 data/crops)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    OUTPUT_DIR = Path(args.out)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), CropRequestHandler)
    url = f"http://{args.host}:{args.port}"
    logger.info("이미지 크롭 도구 실행: %s  (저장경로: %s)", url, OUTPUT_DIR.resolve())
    logger.info("브라우저에서 위 주소로 접속하세요. 종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
