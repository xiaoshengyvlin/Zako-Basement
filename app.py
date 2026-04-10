import os
import uuid
import time
import subprocess
from flask import Flask, request, render_template, send_file, jsonify

app = Flask(__name__)

UPLOAD_DIR = "/app/static/uploads"
OUTPUT_DIR = "/app/static/outputs"
BRAILLE_SCRIPT = "/test/braille_art.py"
RENDER_SCRIPT = "/test/render_txt_to_png.py"
REMBG_BIN = "/test/venv/bin/rembg"
TTL_SECONDS = 2 * 60 * 60  # 2小时

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODEL_OPTIONS = [
    ("u2net", "u2net（通用）"),
    ("isnet-anime", "isnet-anime（动漫/卡通）"),
    ("isnet-general-use", "isnet-general-use（通用增强）"),
    ("u2net_human_seg", "u2net_human_seg（人物）"),
]

def run(cmd):
    subprocess.run(cmd, check=True)

def recommend_model(model):
    mapping = {
        "u2net": "适合大多数普通图片，作为默认首选。",
        "isnet-anime": "适合辛普森、动漫、卡通角色等简洁人物。",
        "isnet-general-use": "适合想试更强通用主体提取的情况。",
        "u2net_human_seg": "可作为额外对比项。",
    }
    return mapping.get(model, "")

def cleanup_old_files():
    now = time.time()
    for folder in [UPLOAD_DIR, OUTPUT_DIR]:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.isfile(path):
                    mtime = os.path.getmtime(path)
                    if now - mtime > TTL_SECONDS:
                        os.remove(path)
            except Exception:
                pass

@app.route("/", methods=["GET"])
def index():
    cleanup_old_files()
    session_id = str(uuid.uuid4())
    return render_template(
        "index.html",
        model_options=MODEL_OPTIONS,
        session_id=session_id
    )

@app.route("/api/preview", methods=["POST"])
def api_preview():
    cleanup_old_files()

    file = request.files.get("image")
    model = request.form.get("model", "u2net")
    use_ppm = request.form.get("post_process_mask") == "on"
    session_id = request.form.get("session_id") or str(uuid.uuid4())

    input_path = os.path.join(UPLOAD_DIR, f"{session_id}_original.png")
    cutout_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}.png")
    white_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}_white.png")

    if file and getattr(file, "filename", ""):
        file.save(input_path)

    if not os.path.exists(input_path):
        return jsonify({
            "ok": False,
            "error": "没有可用的原图，请先上传图片。"
        }), 400

    try:
        cmd = [REMBG_BIN, "i", "-m", model]
        if use_ppm:
            cmd.append("-ppm")
        cmd.extend([input_path, cutout_path])

        run(cmd)

        if not os.path.exists(cutout_path):
            raise RuntimeError("抠图输出文件未生成")

        run(["convert", cutout_path, "-background", "white", "-alpha", "remove", "-alpha", "off", white_path])

        if not os.path.exists(white_path):
            raise RuntimeError("白底预览文件未生成")

        return jsonify({
            "ok": True,
            "session_id": session_id,
            "original_image": f"/static/uploads/{session_id}_original.png",
            "preview_image": f"/static/outputs/{session_id}_{model}_white.png",
            "model": model,
            "post_process_mask": use_ppm,
            "model_tip": recommend_model(model),
            "width": 100,
            "threshold": 110,
            "ratio": 1.45
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"模型处理失败：{model}。请换一个模型试试。错误信息：{str(e)}"
        }), 500

@app.route("/api/braille", methods=["POST"])
def api_braille():
    cleanup_old_files()

    session_id = request.form.get("session_id")
    model = request.form.get("model", "u2net")
    width = request.form.get("width", "100")
    threshold = request.form.get("threshold", "110")
    ratio = request.form.get("ratio", "1.45")

    if not session_id:
        return jsonify({
            "ok": False,
            "error": "缺少 session_id。"
        }), 400

    white_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}_white.png")
    focus_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}_focus.png")
    txt_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}.txt")
    png_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}.png")

    if not os.path.exists(white_path):
        return jsonify({
            "ok": False,
            "error": "预览图不存在，请先生成抠图预览。"
        }), 400

    try:
        run([
            "convert", white_path,
            "-colorspace", "Gray",
            "-auto-level",
            "-contrast-stretch", "10%x90%",
            focus_path
        ])

        result = subprocess.check_output([
            "python", BRAILLE_SCRIPT, focus_path, width, threshold, ratio
        ], text=True)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(result)

        run(["python", RENDER_SCRIPT, txt_path, png_path, "18"])

        return jsonify({
            "ok": True,
            "braille_text": result,
            "download_url": f"/download?session_id={session_id}&model={model}",
            "image_url": f"/static/outputs/{session_id}_{model}.png"
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"Braille 生成失败：{str(e)}"
        }), 500

@app.route("/download")
def download_txt():
    cleanup_old_files()

    session_id = request.args.get("session_id")
    model = request.args.get("model", "u2net")

    if not session_id:
        return "缺少 session_id", 400

    txt_path = os.path.join(OUTPUT_DIR, f"{session_id}_{model}.txt")

    if not os.path.exists(txt_path):
        return "文件不存在", 404

    return send_file(txt_path, as_attachment=True, download_name=f"{session_id}_{model}.txt")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
