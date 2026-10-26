"""TTS tương thích /audio/speech: WAV chunks, master, delivery và đo thời lượng."""

import hashlib
import copy
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import wave
from pathlib import Path

from backend.errors import InputError, ProviderError
from backend.providers import request_bytes
from backend.services.pipeline import source_sentences, write_json
from backend.services.pronunciation import normalize_segments


def tts_configured():
    return all(os.getenv(key) for key in ("TTS_URL", "TTS_MODEL", "TTS_VOICE"))


def audio_chunks(segments, limit=2000):
    # ponytail: một giọng kể, chưa diễn cảm theo speaker; thêm bản đồ giọng khi có dịch vụ cụ thể.
    for segment in segments:
        pending = ""
        for sentence in source_sentences(segment.get("tts_text", segment["text"])):
            text = sentence["text"]
            if len(text) > limit:
                # ponytail: câu dài chia ở khoảng trắng; dùng alignment riêng khi provider có timestamp.
                parts, part = [], ""
                for word in text.split():
                    if len(word) > limit:
                        raise InputError("Một từ vượt giới hạn ký tự TTS. Cần sửa văn bản nguồn.")
                    if len(part) + len(word) + 1 > limit:
                        parts.append(part)
                        part = ""
                    part = f"{part} {word}".strip()
                parts.append(part)
            else:
                parts = [text]
            for part in parts:
                if len(pending) + len(part) + 1 > limit:
                    if pending:
                        yield pending, 0
                    pending = ""
                pending = f"{pending} {part}".strip()
        if pending:
            yield pending, segment.get("pause_after_ms", 350)


def run_ffmpeg(command):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(command, capture_output=True, timeout=300, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError("FFmpeg không chạy được hoặc đã quá thời gian xử lý.") from None
    if result.returncode:
        raise ProviderError("FFmpeg không xử lý được audio. Các file gốc vẫn được giữ để kiểm tra.")
    return result.stderr.decode("utf-8", errors="replace")


def _synthesize(variant, result, directory, temporary, ffmpeg, progress):
    cache = directory / "tts_chunks"
    cache.mkdir(exist_ok=True)
    url, model, voice = (os.environ[key] for key in ("TTS_URL", "TTS_MODEL", "TTS_VOICE"))
    chunks = [(text, pause, segment) for segment in variant["segments"]
              for text, pause in audio_chunks([segment])]
    if not chunks:
        raise InputError("Kịch bản không có lời đọc.")
    receipts = []
    combined = temporary / "combined.wav"
    elapsed = 0.0
    with wave.open(str(combined), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        expected = None
        for index, (text, pause, segment) in enumerate(chunks):
            progress(f"Đang tạo giọng đọc {index + 1}/{len(chunks)}")
            digest = hashlib.sha256(json.dumps([url, model, voice, text], ensure_ascii=False).encode("utf-8")).hexdigest()
            path = cache / f"{digest}.wav"
            if not path.exists():
                raw = request_bytes(url, {"model": model, "voice": voice, "input": text,
                                         "response_format": "wav"}, os.getenv("TTS_API_KEY", ""), 40_000_000)
                try:
                    with wave.open(io.BytesIO(raw), "rb") as check:
                        if check.getcomptype() != "NONE" or check.getnframes() == 0:
                            raise wave.Error("empty or compressed")
                except (wave.Error, EOFError):
                    raise ProviderError("TTS phải trả WAV PCM có dữ liệu, không phải URL hoặc JSON.") from None
                pending = path.with_suffix(".tmp")
                pending.write_bytes(raw)
                pending.replace(path)
            try:
                with wave.open(str(path), "rb") as source:
                    params = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                    if params[0] not in (1, 2) or params[1] not in (1, 2, 3, 4) or not 8000 <= params[2] <= 192000:
                        raise ProviderError("TTS cần trả WAV PCM 1–2 kênh, 8–192 kHz, 8–32 bit.")
                    if expected is None:
                        expected = params
                        output.setnchannels(params[0])
                        output.setsampwidth(params[1])
                        output.setframerate(params[2])
                    if params != expected or source.getnframes() == 0:
                        raise ProviderError("Các chunk TTS khác định dạng PCM hoặc không có dữ liệu.")
                    frames = source.readframes(source.getnframes())
                    if len(frames) != source.getnframes() * params[0] * params[1]:
                        raise ProviderError("Chunk WAV bị thiếu dữ liệu. Cần kiểm tra dịch vụ TTS.")
                    duration = source.getnframes() / params[2]
                    output.writeframes(frames)
                    silence = (b"\x80" if params[1] == 1 else b"\0" * params[1]) * params[0]
                    pause_frames = round(params[2] * pause / 1000)
                    output.writeframes(silence * pause_frames)
            except (wave.Error, EOFError):
                raise ProviderError("Chunk audio đã lưu không phải WAV PCM hợp lệ.") from None
            receipts.append({"index": index + 1, "sha256": digest, "pause_after_ms": pause,
                             "path": str(path.resolve()), "text": text,
                             "source_ids": segment.get("source_ids", []),
                             "scene_id": segment.get("scene_id"), "chapter_id": segment.get("chapter_id"),
                             "start_seconds": round(elapsed, 6), "duration_seconds": round(duration, 6)})
            elapsed += duration + pause_frames / params[2]
    progress("Đang chuẩn hóa âm lượng và đo audio")
    stderr = run_ffmpeg([ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(combined),
                         "-af", "loudnorm=I=-18:TP=-1:LRA=7:print_format=json",
                         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", str(temporary / "master.wav")])
    run_ffmpeg([ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(temporary / "master.wav"),
                "-c:a", "libmp3lame", "-b:a", "128k", str(temporary / "delivery.mp3")])
    with wave.open(str(temporary / "master.wav"), "rb") as master:
        actual = master.getnframes() / master.getframerate()
    blocks = re.findall(r'\{\s*"input_i".*?\}', stderr, re.DOTALL)
    target = result["target_duration"]
    error = (actual - target * 60) / (target * 60) if target else None
    report = {"actual_duration_seconds": round(actual, 3), "target_duration": target,
              "relative_duration_error": round(error, 5) if error is not None else None,
              "within_pilot_tolerance": abs(error) <= 0.05 if error is not None else None,
              "pilot_targets": {"tolerance": 0.05, "integrated_lufs": -18, "true_peak_dbtp": -1},
              "ffmpeg_loudness": json.loads(blocks[-1]) if blocks else None,
              "human_listening_required": True, "pronunciation_checked": False, "asr_checked": False}
    return report, receipts, {"model": model, "voice": voice, "chunks": receipts}


def _commit_files(pairs, temporary):
    """Rollback normal filesystem errors, including failures after replacing one artifact."""
    backups, replaced = {}, []
    try:
        for index, (source, target) in enumerate(pairs):
            if target.exists():
                backup = temporary / f"backup-{index}"
                shutil.copy2(target, backup)
                backups[target] = backup
            source.replace(target)
            replaced.append(target)
    except OSError:
        for target in reversed(replaced):
            if target in backups:
                backups[target].replace(target)
            else:
                target.unlink(missing_ok=True)
        raise


def render_audio(result, profile, directory, progress=lambda message: None):
    from backend.services import pipeline
    from backend.services.qa import run_qa

    candidate_result = copy.deepcopy(result)
    variant = next((v for v in candidate_result["variants"] if v["profile"] == profile), None)
    if variant is None:
        raise InputError("Không tìm thấy kịch bản cần đọc.")
    if variant["audio_blocked"]:
        raise InputError("Cần sửa các lỗi fidelity nghiêm trọng trước khi tạo audio.")
    if not tts_configured():
        raise InputError("Chưa cấu hình TTS_URL, TTS_MODEL và TTS_VOICE.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise InputError("Cần cài FFmpeg và thêm vào PATH trước khi tạo audio.")
    directory = Path(directory) / profile
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (".render-" + secrets.token_hex(8))
    temporary.mkdir()
    attempts = []
    try:
        for round_index in range(3):
            variant["segments"], pronunciation = normalize_segments(variant["segments"], candidate_result.get("pronunciation_lexicon", {}))
            report, receipts, receipt_report = _synthesize(variant, candidate_result, directory, temporary, ffmpeg, progress)
            attempts.append({"round": round_index + 1, "units": variant["units"],
                             "actual_duration_seconds": report["actual_duration_seconds"],
                             "relative_duration_error": report["relative_duration_error"]})
            if not candidate_result["target_duration"]:
                stop_reason = "no_target"
                break
            if report["within_pilot_tolerance"]:
                stop_reason = "within_tolerance"
                break
            if candidate_result.get("mode") != "ai":
                stop_reason = "preview_requires_editor"
                break
            if not (os.getenv("LLM_URL") and os.getenv("LLM_MODEL")):
                stop_reason = "llm_unavailable"
                break
            if round_index == 2:
                stop_reason = "revision_limit"
                break
            progress("Đang chỉnh kịch bản theo thời lượng audio đã đo và kiểm định lại nguồn")
            revised = pipeline.revise_for_duration(candidate_result, profile, report["actual_duration_seconds"])
            if revised is None:
                stop_reason = "no_safe_revision"
                break
            if revised.get("audio_blocked"):
                stop_reason = "revision_fidelity_blocked"
                attempts[-1]["revision_rejected"] = revised.get("fidelity", {})
                break
            before = [(s["text"], s.get("pause_after_ms", 350)) for s in variant["segments"]]
            after = [(s["text"], s.get("pause_after_ms", 350)) for s in revised["segments"]]
            if before == after:
                stop_reason = "unchanged_script"
                break
            candidate_result["variants"] = [revised if v["profile"] == profile else v for v in candidate_result["variants"]]
            variant = revised
        report.update({"attempts": attempts, "automatic_revisions": len(attempts) - 1,
                       "normalization_applied": True,
                       "stop_reason": stop_reason,
                       "next_action": "Nghe duyệt phát âm, nhịp kể và nội dung."
                       if report["within_pilot_tolerance"] is not False else
                       "Chưa đạt thời lượng pilot; cần biên tập viên điều chỉnh mục tiêu hoặc kịch bản."})
        variant["actual_duration_seconds"] = report["actual_duration_seconds"]
        variant["duration_report"] = report
        variant["pronunciation_report"] = pronunciation
        progress("Đang kiểm tra âm thanh và ASR đối chiếu lời đọc")
        qa = run_qa(temporary / "master.wav", variant, report, pronunciation, receipts, progress=progress)
        variant["qa"] = qa
        report["asr_checked"] = qa.get("checks", {}).get("asr", {}).get("status") in ("passed", "failed")
        write_json(temporary / "tts_receipts.json", receipt_report)
        write_json(temporary / "duration_report.json", report)
        write_json(temporary / "pronunciation_report.json", pronunciation)
        write_json(temporary / "qa_report.json", qa)
        write_json(temporary / "script.json", {"segments": variant["segments"]})
        write_json(temporary / "compression_plan.json", variant["plan"])
        write_json(temporary / "fidelity_report.json", variant["fidelity"])
        (temporary / "summary.txt").write_text(variant["summary"], encoding="utf-8")
        write_json(temporary / "result.json", candidate_result)
        filenames = ("master.wav", "delivery.mp3", "tts_receipts.json", "duration_report.json",
                     "pronunciation_report.json", "qa_report.json", "script.json", "compression_plan.json",
                     "fidelity_report.json", "summary.txt")
        pairs = [(temporary / filename, directory / filename) for filename in filenames]
        pairs.append((temporary / "result.json", directory.parent / "result.json"))
        _commit_files(pairs, temporary)
        result.clear()
        result.update(candidate_result)
        return report
    finally:
        if temporary.resolve().parent != directory.resolve() or not temporary.name.startswith(".render-"):
            raise RuntimeError("Thư mục audio tạm nằm ngoài dự án.")
        shutil.rmtree(temporary)
