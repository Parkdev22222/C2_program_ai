"""
이미지 영역 교체 도구 (Image Region Replace Tool)

image_crop_tool.py 로 저장한 크롭 좌표(JSON)를 이용해, 원본 이미지의 해당
사각형 영역을 새로 업로드한 이미지로 교체(합성)합니다.

두 가지 사용 방식:
  1) CLI   : 파일 경로를 넘겨 바로 합성
  2) 웹 UI : 브라우저에서 원본/좌표JSON/교체이미지를 올려 인터랙티브하게 합성

CLI 예시:
  python tools/image_replace_tool.py \\
      --base 원본.png --coords data/crops/crop_XXXX.json \\
      --replacement 새이미지.png --out data/crops/replaced.png --mode stretch

웹 UI 예시:
  python tools/image_replace_tool.py --serve --port 7871
  # 브라우저에서 http://127.0.0.1:7871 접속

교체 모드(--mode):
  stretch : 교체 이미지를 박스 크기에 맞게 늘림 (기본)
  fit     : 비율 유지, 박스 안에 맞춤(여백 투명)
  cover   : 비율 유지, 박스를 꽉 채우고 넘치는 부분은 잘라냄
"""
import argparse
import base64
import io
import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("data/crops")


# ─────────────────────────────────────────────
# 핵심 로직 (Pillow 합성) — CLI/UI 공용
# ─────────────────────────────────────────────
def _resolve_box(coords: dict, base_w: int, base_h: int) -> tuple[int, int, int, int]:
    """좌표 JSON의 crop 박스를 실제 원본 크기에 맞춰 (x, y, w, h) 로 반환합니다.

    JSON에 기록된 image_width/height 가 실제 업로드된 원본 크기와 다르면
    (리사이즈된 경우 등) 비율에 맞춰 좌표를 보정합니다.
    """
    crop = coords["crop"]
    x, y = float(crop["x"]), float(crop["y"])
    w, h = float(crop["width"]), float(crop["height"])

    ref_w = coords.get("image_width")
    ref_h = coords.get("image_height")
    if ref_w and ref_h and (ref_w != base_w or ref_h != base_h):
        sx, sy = base_w / ref_w, base_h / ref_h
        x, y, w, h = x * sx, y * sy, w * sx, h * sy

    # 정수화 + 원본 경계로 클램프
    x = max(0, min(int(round(x)), base_w))
    y = max(0, min(int(round(y)), base_h))
    w = max(1, min(int(round(w)), base_w - x))
    h = max(1, min(int(round(h)), base_h - y))
    return x, y, w, h


def _fit_replacement(repl: Image.Image, size: tuple[int, int], mode: str) -> Image.Image:
    """교체 이미지를 지정한 박스 크기에 맞게 변형합니다."""
    w, h = size
    repl = repl.convert("RGBA")
    if mode == "stretch":
        return repl.resize((w, h), Image.LANCZOS)
    if mode == "cover":
        # 비율 유지하며 꽉 채운 뒤 넘치는 부분 잘라냄
        return ImageOps.fit(repl, (w, h), method=Image.LANCZOS, centering=(0.5, 0.5))
    if mode == "fit":
        # 비율 유지하며 박스 안에 맞추고 남는 곳은 투명 여백
        contained = ImageOps.contain(repl, (w, h), method=Image.LANCZOS)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        off = ((w - contained.width) // 2, (h - contained.height) // 2)
        canvas.paste(contained, off)
        return canvas
    raise ValueError(f"알 수 없는 mode: {mode} (stretch|fit|cover 중 선택)")


def replace_region(
    base_img: Image.Image,
    coords: dict,
    replacement_img: Image.Image,
    mode: str = "stretch",
) -> tuple[Image.Image, dict]:
    """원본 이미지의 crop 영역을 교체 이미지로 합성한 결과를 반환합니다.

    반환: (합성된 이미지(RGBA), 실제 사용한 박스 정보 dict)
    """
    base = base_img.convert("RGBA")
    x, y, w, h = _resolve_box(coords, base.width, base.height)

    patch = _fit_replacement(replacement_img, (w, h), mode)

    result = base.copy()
    # patch 의 알파 채널을 마스크로 사용 → fit 모드의 투명 여백이 원본을 가리지 않음
    result.paste(patch, (x, y), patch)

    used_box = {"x": x, "y": y, "width": w, "height": h, "x2": x + w, "y2": y + h}
    return result, used_box


def load_coords(path_or_obj) -> dict:
    """JSON 파일 경로 또는 이미 파싱된 dict 를 받아 좌표 dict 로 정규화합니다."""
    if isinstance(path_or_obj, dict):
        data = path_or_obj
    else:
        data = json.loads(Path(path_or_obj).read_text(encoding="utf-8"))
    if "crop" not in data:
        raise ValueError("좌표 JSON에 'crop' 키가 없습니다. image_crop_tool.py 가 저장한 파일을 사용하세요.")
    return data


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def run_cli(args) -> None:
    coords = load_coords(args.coords)
    base = Image.open(args.base)
    repl = Image.open(args.replacement)
    result, used_box = replace_region(base, coords, repl, mode=args.mode)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 확장자가 jpg 계열이면 RGB로 변환
    if out.suffix.lower() in (".jpg", ".jpeg"):
        result.convert("RGB").save(out)
    else:
        result.save(out)
    logger.info("합성 완료: %s (교체 영역 %s, 모드=%s)", out, used_box, args.mode)
    print(f"저장됨: {out}")
    print(f"교체 영역: {used_box}")


# ─────────────────────────────────────────────
# 웹 UI (프론트엔드)
# ─────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>이미지 영역 교체 도구</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
         "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
         margin: 0; padding: 24px; background: #0f172a; color: #e2e8f0; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p.sub { margin: 0 0 20px; color: #94a3b8; font-size: 13px; }
  .row { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
          padding: 16px; min-width: 260px; }
  .card h2 { font-size: 14px; margin: 0 0 12px; color: #cbd5e1; }
  label.field { display: block; font-size: 13px; color: #94a3b8; margin: 10px 0 4px; }
  .filebtn { display: inline-block; background: #334155; color: #e2e8f0;
             border: 1px solid #475569; padding: 7px 14px; border-radius: 8px;
             cursor: pointer; font-size: 13px; }
  .filebtn:hover { background: #475569; }
  .fname { font-size: 12px; color: #7dd3fc; margin-left: 8px; }
  input[type=file] { display: none; }
  select { background: #0b1120; color: #e2e8f0; border: 1px solid #475569;
           border-radius: 8px; padding: 7px 10px; font-size: 13px; }
  button.primary { background: #2563eb; border: 1px solid #2563eb; color: #fff;
                   padding: 10px 18px; border-radius: 8px; cursor: pointer;
                   font-size: 14px; margin-top: 16px; }
  button.primary:hover:not(:disabled) { background: #1d4ed8; }
  button.primary:disabled { opacity: .4; cursor: not-allowed; }
  #status { margin-top: 12px; font-size: 13px; min-height: 18px; }
  .ok { color: #4ade80; } .err { color: #f87171; }
  img.preview { max-width: 100%; border-radius: 6px; border: 1px solid #334155;
                margin-top: 8px; display: none; }
  #coordsInfo { font-family: ui-monospace, Menlo, monospace; font-size: 12px;
                color: #7dd3fc; white-space: pre; margin-top: 8px; }
</style>
</head>
<body>
  <h1>🔁 이미지 영역 교체 도구</h1>
  <p class="sub">크롭 좌표(JSON)를 기준으로 <b>원본 이미지의 해당 영역</b>을
     새로 올린 이미지로 교체합니다.</p>

  <div class="row">
    <div class="card">
      <h2>입력</h2>
      <label class="field">1) 원본 이미지</label>
      <label class="filebtn">📁 선택<input type="file" id="baseInput" accept="image/*"></label>
      <span class="fname" id="baseName"></span>

      <label class="field">2) 크롭 좌표 JSON (crop_*.json)</label>
      <label class="filebtn">📁 선택<input type="file" id="jsonInput" accept=".json,application/json"></label>
      <span class="fname" id="jsonName"></span>
      <div id="coordsInfo"></div>

      <label class="field">3) 교체할 새 이미지</label>
      <label class="filebtn">📁 선택<input type="file" id="replInput" accept="image/*"></label>
      <span class="fname" id="replName"></span>

      <label class="field">교체 모드</label>
      <select id="mode">
        <option value="stretch">stretch (박스에 맞게 늘리기)</option>
        <option value="fit">fit (비율 유지·여백 투명)</option>
        <option value="cover">cover (비율 유지·꽉 채우고 자르기)</option>
      </select>

      <br>
      <button class="primary" id="runBtn" disabled>🔁 교체 & 저장</button>
      <div id="status"></div>
    </div>

    <div class="card" style="flex:1;">
      <h2>결과 미리보기</h2>
      <img class="preview" id="result" alt="result">
      <p class="sub" id="resultInfo" style="margin-top:8px;"></p>
    </div>
  </div>

<script>
const $ = (id) => document.getElementById(id);
const state = { base: null, coords: null, repl: null };

function toDataURL(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}
function readText(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsText(file);
  });
}
function refresh() {
  $('runBtn').disabled = !(state.base && state.coords && state.repl);
}

$('baseInput').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  state.base = await toDataURL(f); $('baseName').textContent = f.name; refresh();
});
$('replInput').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  state.repl = await toDataURL(f); $('replName').textContent = f.name; refresh();
});
$('jsonInput').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const obj = JSON.parse(await readText(f));
    state.coords = obj; $('jsonName').textContent = f.name;
    const c = obj.crop || {};
    $('coordsInfo').textContent =
      `x:${c.x} y:${c.y} w:${c.width} h:${c.height}` +
      (obj.image_width ? `  (원본 ${obj.image_width}x${obj.image_height})` : '');
  } catch (err) {
    $('jsonName').textContent = '❌ JSON 파싱 실패'; state.coords = null;
  }
  refresh();
});

$('runBtn').addEventListener('click', async () => {
  $('runBtn').disabled = true;
  $('status').className = ''; $('status').textContent = '합성 중...';
  try {
    const res = await fetch('/replace', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_png: state.base, replacement_png: state.repl,
        coords: state.coords, mode: $('mode').value,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      $('status').className = 'ok';
      $('status').textContent = '✅ 저장 완료: ' + data.result_file;
      $('result').src = data.preview; $('result').style.display = 'block';
      $('resultInfo').textContent = '교체 영역: ' + JSON.stringify(data.used_box);
    } else {
      $('status').className = 'err'; $('status').textContent = '❌ ' + data.error;
    }
  } catch (err) {
    $('status').className = 'err'; $('status').textContent = '❌ 오류: ' + err;
  } finally {
    $('runBtn').disabled = false;
  }
});
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────
# 웹 UI (백엔드)
# ─────────────────────────────────────────────
def _decode_data_url(data_url: str) -> Image.Image:
    b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
    return Image.open(io.BytesIO(base64.b64decode(b64)))


class ReplaceRequestHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/replace":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._send_json(200, {"ok": True, **do_replace(payload)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("영역 교체 실패")
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.address_string(), fmt % args)


def do_replace(payload: dict) -> dict:
    coords = load_coords(payload["coords"])
    base = _decode_data_url(payload["base_png"])
    repl = _decode_data_url(payload["replacement_png"])
    mode = payload.get("mode", "stretch")

    result, used_box = replace_region(base, coords, repl, mode=mode)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_path = OUTPUT_DIR / f"replaced_{stamp}.png"
    result.save(out_path)

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    preview = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    logger.info("교체 저장: %s (영역 %s, 모드=%s)", out_path, used_box, mode)
    return {"result_file": str(out_path), "used_box": used_box, "preview": preview}


def run_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ReplaceRequestHandler)
    url = f"http://{host}:{port}"
    logger.info("이미지 영역 교체 도구 실행: %s  (저장경로: %s)", url, OUTPUT_DIR.resolve())
    logger.info("브라우저에서 위 주소로 접속하세요. 종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("종료합니다.")
    finally:
        server.server_close()


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────
def main():
    global OUTPUT_DIR
    parser = argparse.ArgumentParser(description="크롭 좌표로 이미지 영역을 교체하는 도구")
    parser.add_argument("--serve", action="store_true", help="브라우저 UI 서버 실행")
    parser.add_argument("--host", default="127.0.0.1", help="서버 호스트 (기본 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7871, help="서버 포트 (기본 7871)")
    parser.add_argument("--out", default="data/crops", help="UI 저장 경로 (기본 data/crops)")
    # CLI 인자
    parser.add_argument("--base", help="원본 이미지 경로")
    parser.add_argument("--coords", help="크롭 좌표 JSON 경로 (crop_*.json)")
    parser.add_argument("--replacement", help="교체할 새 이미지 경로")
    parser.add_argument("--mode", default="stretch", choices=["stretch", "fit", "cover"],
                        help="교체 모드 (기본 stretch)")
    parser.add_argument("--out-file", dest="out_file", help="CLI 결과 저장 파일 경로")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.serve:
        OUTPUT_DIR = Path(args.out)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        run_server(args.host, args.port)
        return

    # CLI 모드
    if not (args.base and args.coords and args.replacement):
        parser.error("CLI 모드에는 --base, --coords, --replacement 가 모두 필요합니다. "
                     "(웹 UI는 --serve 사용)")
    args.out = args.out_file or "replaced.png"
    run_cli(args)


if __name__ == "__main__":
    main()
