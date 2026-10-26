"""Máy chủ cục bộ: python -m backend, sau đó mở http://127.0.0.1:8765."""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.config import FRONTEND_DIR, PROJECTS_DIR, REPORT_PATH
from backend.errors import InputError, ProviderError
from backend.services.audio import render_audio, tts_configured
from backend.services.pipeline import MAX_SOURCE, run_pipeline, validate_request, write_json
from backend.services.qa import apply_human_review, asr_configured


TOKEN = secrets.token_urlsafe(32)
WORKER = threading.Lock()
ID = r"[a-f0-9]{32}"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def reject_constant(value):
    raise ValueError(f"JSON không hỗ trợ {value}.")


def update_job(folder, **fields):
    path = folder / "job.json"
    job = read_json(path)
    job.update(fields)
    write_json(path, job)


def run_job(folder, config):
    try:
        run_pipeline(config, folder, lambda message: update_job(folder, message=message))
        update_job(folder, status="completed", message="Đã tạo kịch bản. Cần đọc và nghe duyệt trước khi sử dụng.")
    except (InputError, ProviderError) as exc:
        update_job(folder, status="failed", message=str(exc))
    except Exception as exc:
        print(f"Lỗi xử lý {folder.name}: {type(exc).__name__}", flush=True)
        update_job(folder, status="failed", message="Không xử lý được. Kiểm tra quyền ghi, dung lượng ổ đĩa và cấu hình.")
    finally:
        WORKER.release()


def run_audio_job(folder, profile):
    try:
        render_audio(read_json(folder / "result.json"), profile, folder,
                     lambda message: update_job(folder, message=message))
        update_job(folder, status="completed", message="Đã tạo audio và đo thời lượng. Cần nghe duyệt trước khi sử dụng.")
    except (InputError, ProviderError) as exc:
        update_job(folder, status="failed", message=str(exc))
    except Exception as exc:
        print(f"Lỗi audio {folder.name}: {type(exc).__name__}", flush=True)
        update_job(folder, status="failed", message="Không tạo được audio. Kịch bản và các chunk đã tạo vẫn được giữ.")
    finally:
        WORKER.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "AudioTruyen/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        pass

    def headers_for(self, status, content_type, length, **headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.headers_for(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def local_request(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and origin not in {f"http://{entry}" for entry in allowed}):
            self.send_json(403, {"error": "Chỉ nhận yêu cầu từ giao diện tại máy này."})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/":
                body = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).encode("utf-8")
                self.headers_for(200, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
            elif path in ("/css/styles.css", "/js/app.js"):
                content_type = "text/css" if path.endswith(".css") else "text/javascript"
                body = (FRONTEND_DIR / path.lstrip("/")).read_bytes()
                self.headers_for(200, content_type + "; charset=utf-8", len(body))
                self.wfile.write(body)
            elif path == "/api/health":
                self.send_json(200, {"ai_configured": bool(os.getenv("LLM_URL") and os.getenv("LLM_MODEL")),
                                     "tts_configured": tts_configured(), "ffmpeg_available": bool(shutil.which("ffmpeg")),
                                     "asr_configured": asr_configured(),
                                     "busy": WORKER.locked(), "max_source": MAX_SOURCE})
            elif path == "/api/sample":
                report = REPORT_PATH.read_text(encoding="utf-8")
                sample = report.split("**Nguồn thử nghiệm — “Đèn trên bến Cạn”**", 1)[1].split("**Phân tích trước khi tóm:**", 1)[0]
                text = "\n".join(line[2:] if line.startswith("> ") else "" for line in sample.splitlines() if line.startswith(">"))
                self.send_json(200, {"title": "Đèn trên bến Cạn", "author": "Ví dụ trong báo cáo nghiên cứu",
                                     "text": text.strip(), "rights_note": "Fixture được tạo riêng trong docs/deep-research-report.md; dùng thử nghiệm nội bộ."})
            elif path == "/api/jobs":
                jobs = []
                for folder in PROJECTS_DIR.iterdir():
                    if re.fullmatch(ID, folder.name) and (folder / "job.json").is_file():
                        jobs.append(read_json(folder / "job.json"))
                self.send_json(200, sorted(jobs, key=lambda job: job["created_at"], reverse=True))
            elif match := re.fullmatch(rf"/api/jobs/({ID})", path):
                folder = PROJECTS_DIR / match[1]
                job = read_json(folder / "job.json")
                if (folder / "result.json").exists():
                    job["result"] = read_json(folder / "result.json")
                self.send_json(200, job)
            elif match := re.fullmatch(rf"/api/jobs/({ID})/download", path):
                self.download(PROJECTS_DIR / match[1])
            elif match := re.fullmatch(rf"/api/jobs/({ID})/(preserve|balanced|compact)/(master.wav|delivery.mp3)", path):
                self.audio_file(PROJECTS_DIR / match[1] / match[2] / match[3])
            else:
                self.send_json(404, {"error": "Không tìm thấy nội dung."})
        except (FileNotFoundError, IndexError):
            self.send_json(404, {"error": "Không tìm thấy dữ liệu."})
        except (OSError, ValueError):
            self.send_json(500, {"error": "Không đọc được dữ liệu đã lưu."})

    def download(self, folder):
        if not WORKER.acquire(blocking=False):
            self.send_json(409, {"error": "Đang xử lý dữ liệu. Chờ hoàn thành trước khi tải hồ sơ."})
            return
        try:
            self.download_snapshot(folder)
        finally:
            WORKER.release()

    def download_snapshot(self, folder):
        if not (folder / "job.json").exists():
            raise FileNotFoundError
        with tempfile.TemporaryFile(dir=PROJECTS_DIR) as archive:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for file in sorted(folder.rglob("*")):
                    if (file.is_file() and file.suffix in (".json", ".txt", ".wav", ".mp3")
                            and not any(part.startswith(".render-") for part in file.relative_to(folder).parts)):
                        bundle.write(file, str(file.relative_to(folder)))
            length = archive.tell()
            archive.seek(0)
            self.headers_for(200, "application/zip", length,
                             Content_Disposition=f'attachment; filename="audio-truyen-{folder.name[:8]}.zip"')
            shutil.copyfileobj(archive, self.wfile)

    def audio_file(self, path):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        headers = {"Accept_Ranges": "bytes"}
        if requested := self.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
            if not match or int(match[1]) >= size:
                self.headers_for(416, "audio/wav", 0, Content_Range=f"bytes */{size}")
                return
            start, end = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1)
            if start > end:
                self.headers_for(416, "audio/wav", 0, Content_Range=f"bytes */{size}")
                return
            status, headers["Content_Range"] = 206, f"bytes {start}-{end}/{size}"
        self.headers_for(status, "audio/wav" if path.suffix == ".wav" else "audio/mpeg", end - start + 1, **headers)
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = source.read(min(remaining, 64 * 1024))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_POST(self):
        if not self.local_request():
            return
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", "").encode("utf-8"), TOKEN.encode("ascii")):
            self.send_json(403, {"error": "Phiên giao diện đã hết hạn. Tải lại trang."})
            return
        audio_match = re.fullmatch(rf"/api/jobs/({ID})/audio", self.path)
        review_match = re.fullmatch(rf"/api/jobs/({ID})/review", self.path)
        if self.path != "/api/jobs" and not audio_match and not review_match:
            self.send_json(404, {"error": "Không tìm thấy thao tác."})
            return
        acquired = False
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise InputError("Yêu cầu phải dùng Content-Type application/json.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_SOURCE * 6 + 100_000:
                raise InputError("Yêu cầu trống hoặc vượt giới hạn dung lượng.")
            data = json.loads(self.rfile.read(length), parse_constant=reject_constant)
            if review_match:
                acquired = WORKER.acquire(blocking=False)
                if not acquired:
                    self.send_json(409, {"error": "Đang xử lý dữ liệu. Chờ hoàn thành trước khi duyệt."})
                    return
                self.record_review(PROJECTS_DIR / review_match[1], data)
                return
            if audio_match:
                if not isinstance(data, dict) or data.get("approved") is not True:
                    raise InputError("Cần duyệt kịch bản trước khi gửi lời đọc tới dịch vụ TTS.")
                profile = data.get("profile")
                if not isinstance(profile, str) or profile not in ("preserve", "balanced", "compact"):
                    raise InputError("Mức rút gọn không hợp lệ.")
                folder = PROJECTS_DIR / audio_match[1]
                if not (folder / "result.json").is_file():
                    raise InputError("Dự án chưa có kịch bản.")
                variant = next((v for v in read_json(folder / "result.json")["variants"] if v["profile"] == profile), None)
                if variant is None or variant["audio_blocked"]:
                    raise InputError("Kịch bản chưa sẵn sàng hoặc đang có lỗi fidelity nghiêm trọng.")
                if not tts_configured() or not shutil.which("ffmpeg"):
                    raise InputError("Cần cấu hình TTS và cài FFmpeg trước khi tạo audio.")
            else:
                config = validate_request(data)
                if config["mode"] == "ai" and not (os.getenv("LLM_URL") and os.getenv("LLM_MODEL")):
                    raise InputError("Chưa cấu hình AI. Dùng Thử tại máy hoặc đặt LLM_URL và LLM_MODEL.")
            acquired = WORKER.acquire(blocking=False)
            if not acquired:
                self.send_json(409, {"error": "Một chương đang được xử lý. Chờ hoàn thành rồi thử lại."})
                return
            if audio_match:
                update_job(folder, status="running", operation="audio", audio_profile=profile,
                           audio_approved_at=datetime.now(timezone.utc).isoformat(), message="Đang chuẩn bị tạo audio")
                job = read_json(folder / "job.json")
                worker, arguments = run_audio_job, (folder, profile)
            else:
                job_id = secrets.token_hex(16)
                folder = PROJECTS_DIR / job_id
                folder.mkdir()
                job = {"id": job_id, "title": config["title"], "mode": config["mode"], "status": "running",
                       "created_at": datetime.now(timezone.utc).isoformat(), "message": "Đã nhận văn bản"}
                write_json(folder / "job.json", job)
                worker, arguments = run_job, (folder, config)
            threading.Thread(target=worker, args=arguments, daemon=True).start()
            acquired = False  # Luồng xử lý chịu trách nhiệm nhả khóa.
            self.send_json(202, job)
        except (InputError, ValueError, UnicodeError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, InputError) else "Nội dung JSON hoặc độ dài yêu cầu không hợp lệ."
            self.send_json(400, {"error": message})
        except OSError:
            self.send_json(500, {"error": "Không lưu được dự án. Kiểm tra quyền ghi và dung lượng ổ đĩa."})
        finally:
            if acquired:
                WORKER.release()

    def record_review(self, folder, data):
        if not isinstance(data, dict) or data.get("profile") not in ("preserve", "balanced", "compact"):
            raise InputError("Mức rút gọn không hợp lệ.")
        if not (folder / "result.json").is_file():
            raise InputError("Dự án chưa có kết quả.")
        result = read_json(folder / "result.json")
        variant = next((v for v in result["variants"] if v["profile"] == data["profile"]), None)
        directory = folder / data["profile"]
        if variant is None or not variant.get("qa") or not (directory / "master.wav").is_file():
            raise InputError("Cần tạo audio và chạy QA trước khi nghe duyệt.")
        with (directory / "master.wav").open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        reviewed = apply_human_review(variant["qa"], data, digest)
        previous = read_json(directory / "qa_report.json")
        variant["qa"] = reviewed
        write_json(directory / "qa_report.json", reviewed)
        try:
            write_json(folder / "result.json", result)
        except OSError:
            write_json(directory / "qa_report.json", previous)
            raise
        self.send_json(200, reviewed)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audio Truyện — biên tập truyện tại máy")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    for path in PROJECTS_DIR.glob("*/job.json"):
        try:
            job = read_json(path)
        except (OSError, ValueError):
            print(f"Không đọc được hồ sơ {path.parent.name}; giữ nguyên dữ liệu.", flush=True)
            continue
        if job["status"] == "running":
            update_job(path.parent, status="failed", message="Lần xử lý trước bị gián đoạn. Dữ liệu đã tạo vẫn được giữ.")
    server.daemon_threads = True
    print(f"Audio Truyện: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng máy chủ.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
