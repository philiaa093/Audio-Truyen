"""QA audio độc lập: PCM, ASR qua gateway JSON và hồ sơ nghe duyệt."""

import base64
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path

from backend.errors import InputError, ProviderError
from backend.providers import request_json


CHECKLIST_FIELDS = ("story_fidelity", "pronunciation", "pacing", "audio_quality",
                    "style_emotion", "content_labels")
MAX_ASR_AUDIO_BYTES = 40_000_000
MAX_TRANSCRIPT_CHARS = 12_000
PILOT_ACOUSTIC_TARGETS = {"clipped_sample_ratio": 0, "silence_dbfs": -50,
                          "max_silence_ratio": 0.98, "max_silence_seconds": 5}
PILOT_LOUDNESS_TARGETS = {"integrated_lufs": -18, "lufs_tolerance": 1,
                         "true_peak_dbtp": -1, "measurement_tolerance_db": 0.1}


def asr_configured():
    return bool(os.getenv("ASR_URL") and os.getenv("ASR_MODEL"))


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_text(text):
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(" " if unicodedata.category(c)[0] in "PS" else c
                            for c in text).split())


def edit_distance(left, right):
    """Khoảng cách chính xác bằng bit vector, bộ nhớ theo chuỗi ngắn hơn."""
    if len(left) > len(right):
        left, right = right, left
    if not left:
        return len(right)
    if len(right) > MAX_TRANSCRIPT_CHARS:
        raise ProviderError("Văn bản ASR vượt giới hạn so sánh 12.000 ký tự/đơn vị.")
    masks = {}
    for index, item in enumerate(left):
        masks[item] = masks.get(item, 0) | (1 << index)
    limit = (1 << len(left)) - 1
    positive, negative, distance = limit, 0, len(left)
    highest = 1 << (len(left) - 1)
    for item in right:
        equal = masks.get(item, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        distance += bool(plus & highest) - bool(minus & highest)
        plus = (plus << 1) | 1
        minus <<= 1
        positive = (minus | ~(vertical | plus)) & limit
        negative = plus & vertical
    return distance


def transcript_metrics(expected, actual):
    if not isinstance(actual, str) or len(actual) > MAX_TRANSCRIPT_CHARS:
        raise ProviderError("Transcript ASR phải là văn bản tối đa 12.000 ký tự.")
    if not isinstance(expected, str) or len(expected) > MAX_TRANSCRIPT_CHARS:
        raise ProviderError("Lời đọc vượt giới hạn so sánh ASR.")
    reference, hypothesis = canonical_text(expected), canonical_text(actual)
    reference_chars, hypothesis_chars = reference.replace(" ", ""), hypothesis.replace(" ", "")
    reference_words, hypothesis_words = reference.split(), hypothesis.split()
    if not reference_chars:
        raise ProviderError("Không có lời đọc để đối chiếu ASR.")
    character_errors = edit_distance(reference_chars, hypothesis_chars)
    word_errors = edit_distance(reference_words, hypothesis_words)
    return {"character_errors": character_errors, "reference_characters": len(reference_chars),
            "word_errors": word_errors, "reference_words": len(reference_words),
            "cer": round(character_errors / len(reference_chars), 6),
            "wer": round(word_errors / len(reference_words), 6)}


def acoustic_check(path):
    """Đọc PCM theo khối; peak là sample peak, không thay thế phép đo true peak."""
    peak, clipped, samples, silent_frames, longest_silence, silence_run = 0, 0, 0, 0, 0, 0
    with wave.open(str(path), "rb") as stream:
        channels, width, rate = stream.getnchannels(), stream.getsampwidth(), stream.getframerate()
        if stream.getcomptype() != "NONE" or channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000:
            raise ProviderError("QA cần WAV PCM 1–2 kênh, 8–32 bit, 8–192 kHz.")
        full_scale = 1 << (width * 8 - 1)
        silence_level = full_scale * 10 ** (PILOT_ACOUSTIC_TARGETS["silence_dbfs"] / 20)
        frames = 0
        while raw := stream.readframes(8192):
            frame_size = width * channels
            if len(raw) % frame_size:
                raise ProviderError("WAV bị thiếu dữ liệu PCM khi kiểm tra QA.")
            for offset in range(0, len(raw), frame_size):
                frame_peak = 0
                for channel in range(channels):
                    start = offset + channel * width
                    value = raw[start] - 128 if width == 1 else int.from_bytes(raw[start:start + width], "little", signed=True)
                    magnitude = abs(value)
                    frame_peak = max(frame_peak, magnitude)
                    peak = max(peak, magnitude)
                    clipped += value in (-full_scale, full_scale - 1)
                    samples += 1
                frames += 1
                if frame_peak <= silence_level:
                    silent_frames += 1
                    silence_run += 1
                    longest_silence = max(longest_silence, silence_run)
                else:
                    silence_run = 0
        if not frames or frames != stream.getnframes():
            raise ProviderError("WAV rỗng hoặc số frame không khớp dữ liệu PCM.")
    silence_ratio = silent_frames / frames
    longest_seconds = longest_silence / rate
    reasons = []
    if clipped:
        reasons.append("Có mẫu PCM chạm biên clipping.")
    if silence_ratio > PILOT_ACOUSTIC_TARGETS["max_silence_ratio"]:
        reasons.append("Audio gần như im lặng.")
    if longest_seconds > PILOT_ACOUSTIC_TARGETS["max_silence_seconds"]:
        reasons.append("Khoảng im lặng liên tục vượt ngưỡng pilot.")
    return {"status": "failed" if reasons else "passed", "reasons": reasons,
            "samples": samples, "clipped_samples": clipped,
            "clipped_sample_ratio": round(clipped / samples, 8),
            "sample_peak_dbfs": round(20 * math.log10(peak / full_scale), 3) if peak else None,
            "silence_ratio": round(silence_ratio, 6), "longest_silence_seconds": round(longest_seconds, 3),
            "duration_seconds": round(frames / rate, 3), "pilot_targets": PILOT_ACOUSTIC_TARGETS.copy(),
            "limitation": "Sample peak PCM; true peak/loudness xem số đo FFmpeg. Ngưỡng pilot cần hiệu chỉnh bằng dữ liệu thật."}


def asr_thresholds():
    values = {}
    for name, variable in (("max_cer", "QA_MAX_CER"), ("max_wer", "QA_MAX_WER")):
        raw = os.getenv(variable)
        if raw is None or not raw.strip():
            return None
        try:
            value = float(raw)
        except ValueError:
            raise ProviderError(f"{variable} phải là số từ 0 đến 1.") from None
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ProviderError(f"{variable} phải là số từ 0 đến 1.")
        values[name] = value
    return values


def loudness_check(path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"status": "incomplete", "reason": "Cần FFmpeg để đo loudness và true peak của master."}
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        measurement = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-i", str(path),
            "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300, **options)
        output = measurement.stderr.decode("utf-8", errors="replace")
        integrated = re.findall(r"Integrated loudness:\s*I:\s*([-+\w.]+)\s+LUFS", output)
        peak = re.findall(r"True peak:\s*Peak:\s*([-+\w.]+)\s+dBFS", output)
        if measurement.returncode or not integrated or not peak:
            raise ValueError
        integrated_lufs, true_peak = float(integrated[-1]), float(peak[-1])
        if not math.isfinite(integrated_lufs) or not math.isfinite(true_peak):
            return {"status": "failed", "reason": "Master không có mức loudness/true peak hữu hạn.",
                    "pilot_targets": PILOT_LOUDNESS_TARGETS.copy()}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "incomplete", "reason": "Không đo được loudness/true peak của master bằng FFmpeg."}
    passed = (abs(integrated_lufs - PILOT_LOUDNESS_TARGETS["integrated_lufs"]) <= PILOT_LOUDNESS_TARGETS["lufs_tolerance"]
              and true_peak <= PILOT_LOUDNESS_TARGETS["true_peak_dbtp"] + PILOT_LOUDNESS_TARGETS["measurement_tolerance_db"])
    return {"status": "passed" if passed else "failed", "integrated_lufs": integrated_lufs,
            "true_peak_dbtp": true_peak, "method": "FFmpeg ebur128 trên master sau xuất",
            "pilot_targets": PILOT_LOUDNESS_TARGETS.copy(),
            "reason": "Mức âm lượng và true peak trong ngưỡng pilot." if passed else "Mức âm lượng hoặc true peak ngoài ngưỡng pilot."}


def _save_cache(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def asr_check(master_path, receipts, progress=lambda message: None):
    if not asr_configured():
        return {"status": "incomplete", "reason": "Chưa cấu hình ASR_URL và ASR_MODEL.", "chunks": []}
    chunks, failures = [], []
    try:
        thresholds = asr_thresholds()
    except ProviderError as exc:
        return {"status": "incomplete", "reason": str(exc), "chunks": []}
    if not receipts:
        return {"status": "incomplete", "reason": "Thiếu hồ sơ chunk để đối chiếu ASR.", "chunks": []}
    url, model = os.environ["ASR_URL"], os.environ["ASR_MODEL"]
    allowed_root = Path(master_path).resolve().parent.parent
    for index, receipt in enumerate(receipts):
        progress(f"Đang đối chiếu ASR {index + 1}/{len(receipts)}")
        item = {"index": index + 1, "scene_id": receipt.get("scene_id"),
                "chapter_id": receipt.get("chapter_id"), "source_ids": receipt.get("source_ids", [])}
        try:
            path = Path(receipt["path"]).resolve()
            if not path.is_relative_to(allowed_root) or not 0 < path.stat().st_size <= MAX_ASR_AUDIO_BYTES:
                raise ProviderError("Chunk ASR nằm ngoài dự án hoặc vượt 40 MB.")
            expected = receipt.get("text")
            if not isinstance(expected, str) or not expected.strip() or len(expected) > MAX_TRANSCRIPT_CHARS:
                raise ProviderError("Chunk không có lời đọc hợp lệ để đối chiếu ASR.")
            digest = file_sha256(path)
            key = hashlib.sha256(json.dumps(["vi", url, model, digest], ensure_ascii=False).encode("utf-8")).hexdigest()
            cache_dir = path.parent / "asr_cache"
            cache_dir.mkdir(exist_ok=True)
            cache_path = cache_dir / f"{key}.json"
            transcript = None
            if cache_path.is_file():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    if cached.get("audio_sha256") == digest and cached.get("model") == model:
                        transcript = cached.get("text")
                        transcript_metrics(expected, transcript)
                except (OSError, ValueError, AttributeError, ProviderError):
                    transcript = None
            if transcript is None:
                response = request_json(url, {"model": model, "language": "vi",
                    "audio_base64": base64.b64encode(path.read_bytes()).decode("ascii")}, os.getenv("ASR_API_KEY", ""))
                if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                    raise ProviderError("Gateway ASR phải trả JSON có trường text.")
                transcript = response["text"]
                transcript_metrics(expected, transcript)
                _save_cache(cache_path, {"audio_sha256": digest, "model": model, "text": transcript})
            item.update({"status": "passed", "audio_sha256": digest, "expected_text": expected,
                         "transcript": transcript, **transcript_metrics(expected, transcript)})
        except (ProviderError, OSError, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ProviderError) else "Không đọc được chunk hoặc cache ASR."
            item.update({"status": "incomplete", "reason": reason})
            failures.append(reason)
        chunks.append(item)
    valid = [chunk for chunk in chunks if chunk["status"] == "passed"]
    totals = {key: sum(chunk[key] for chunk in valid) for key in
              ("character_errors", "reference_characters", "word_errors", "reference_words")}
    cer = totals["character_errors"] / totals["reference_characters"] if totals["reference_characters"] else None
    wer = totals["word_errors"] / totals["reference_words"] if totals["reference_words"] else None
    mismatch = thresholds is not None and cer is not None and (cer > thresholds["max_cer"] or wer > thresholds["max_wer"])
    return {"status": "failed" if mismatch else "incomplete" if failures or thresholds is None else "passed",
            "model": model, "cer": round(cer, 6) if cer is not None else None,
            "wer": round(wer, 6) if wer is not None else None, **totals, "chunks": chunks,
            "thresholds": thresholds, "threshold_basis": "Cấu hình nội bộ; không phải chuẩn chung cho ASR tiếng Việt.",
            "reason": "Có chunk ASR chưa kiểm tra được." if failures else
            "Cần đặt QA_MAX_CER và QA_MAX_WER từ baseline ASR nội bộ." if thresholds is None else
            "Sai khác transcript vượt ngưỡng nội bộ." if mismatch else "Đã đối chiếu mọi chunk.",
            "limitation": "ASR đối chiếu lời đọc TTS trước ghép; acoustic QA kiểm tra master. ASR không chứng minh phát âm đúng."}


def _rights_check(master_path, variant):
    metadata = variant
    for parent in list(Path(master_path).resolve().parents)[:4]:
        path = parent / "source.json"
        if path.is_file():
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"status": "incomplete", "reason": "Không đọc được căn cứ quyền sử dụng."}
            break
    if not isinstance(metadata, dict) or metadata.get("rights_confirmed") is not True or not isinstance(metadata.get("rights_note"), str) or not metadata["rights_note"].strip():
        return {"status": "incomplete", "reason": "Thiếu xác nhận hoặc căn cứ quyền sử dụng trong hồ sơ nguồn."}
    return {"status": "passed", "reason": "Có xác nhận và ghi nhận căn cứ sử dụng; cần người duyệt phạm vi thực tế."}


def run_qa(master_path, variant, duration_report, pronunciation_report, receipts, progress=lambda message: None):
    fidelity = variant.get("fidelity", {})
    errors = {key: fidelity.get(key, []) for key in
              ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff")}
    valid_critic = isinstance(fidelity, dict) and all(isinstance(fidelity.get(key), list)
        and all(isinstance(error, str) for error in fidelity[key]) for key in (*errors, "minor_errors"))
    fidelity_status = "failed" if variant.get("audio_blocked") or any(errors.values()) else \
        "passed" if valid_critic and fidelity.get("method") == "ai_review" else "incomplete"
    duration = duration_report.get("within_pilot_tolerance")
    target = duration_report.get("target_duration")
    pronunciation = pronunciation_report.get("unresolved", [])
    checks = {
        "rights": _rights_check(master_path, variant),
        "fidelity": {"status": fidelity_status, "errors": errors,
                     "reason": "Cần đối chiếu ngữ nghĩa nguồn bằng AI và người duyệt."},
        "duration": {"status": "passed" if target is None or duration is True else "failed" if duration is False else "incomplete",
                     "actual_duration_seconds": duration_report.get("actual_duration_seconds"),
                     "target_duration": target, "relative_duration_error": duration_report.get("relative_duration_error"),
                     "reason": "Không đặt thời lượng mục tiêu." if target is None else "Dung sai pilot ±5%."},
        "pronunciation": {"status": "incomplete" if pronunciation else "passed", "unresolved": pronunciation,
                          "reason": "Đã chuẩn hóa văn bản; phát âm tên riêng và giọng thực tế vẫn cần nghe duyệt."},
    }
    try:
        digest = file_sha256(master_path)
        checks["acoustic"] = acoustic_check(master_path)
    except (OSError, wave.Error, EOFError, ProviderError) as exc:
        digest = None
        checks["acoustic"] = {"status": "incomplete", "reason": str(exc) if isinstance(exc, ProviderError) else "Không đọc được master PCM để QA."}
    progress("Đang đo loudness và true peak của master")
    checks["loudness"] = loudness_check(master_path)
    checks["asr"] = asr_check(master_path, receipts, progress)
    statuses = {check["status"] for check in checks.values()}
    status = "blocked" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "ready_for_review"
    return {"version": 1, "audio_sha256": digest, "status": status, "release_ready": False,
            "checks": checks, "human_review": {"status": "pending"},
            "human_listening_required": True,
            "limitations": ["Các ngưỡng là pilot hoặc baseline nội bộ, chưa phải benchmark nhà cung cấp.",
                            "Luôn nghe duyệt plot, POV, setup/payoff, phát âm, phong cách, nhịp kể và nhãn nội dung."]}


def apply_human_review(qa, data, current_audio_hash):
    if not isinstance(qa, dict) or not isinstance(data, dict):
        raise InputError("Hồ sơ QA và yêu cầu duyệt phải là đối tượng JSON.")
    if not isinstance(current_audio_hash, str) or len(current_audio_hash) != 64 or any(c not in "0123456789abcdef" for c in current_audio_hash) or data.get("audio_sha256") != current_audio_hash or qa.get("audio_sha256") != current_audio_hash:
        raise InputError("Audio đã thay đổi hoặc mã SHA256 không khớp. Tải lại hồ sơ QA trước khi duyệt.")
    reviewer, decision, notes = data.get("reviewer"), data.get("decision"), data.get("notes", "")
    checklist = data.get("checklist")
    if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 160:
        raise InputError("Tên người duyệt phải có từ 1 đến 160 ký tự.")
    if decision not in ("approved", "rejected") or not isinstance(notes, str) or len(notes) > 4000:
        raise InputError("Quyết định phải là approved/rejected; ghi chú tối đa 4.000 ký tự.")
    if not isinstance(checklist, dict) or set(checklist) != set(CHECKLIST_FIELDS) or any(type(checklist.get(key)) is not bool for key in CHECKLIST_FIELDS):
        raise InputError("Cần đánh dấu đầy đủ sáu nhóm kiểm tra nghe duyệt bằng true/false.")
    if decision == "rejected" and not notes.strip():
        raise InputError("Cần ghi lý do khi từ chối audio.")
    if decision == "approved":
        checks = qa.get("checks", {})
        if qa.get("status") not in ("ready_for_review", "approved", "rejected") or not isinstance(checks, dict) or not {"rights", "fidelity", "duration", "pronunciation", "acoustic", "loudness", "asr"}.issubset(checks) or any(not isinstance(check, dict) or check.get("status") != "passed" for check in checks.values()):
            raise InputError("Chưa thể duyệt: cần hoàn tất và đạt mọi kiểm tra tự động.")
        if not all(checklist.values()):
            raise InputError("Chỉ duyệt khi cả sáu nhóm nghe kiểm tra đều đạt.")
    updated = copy.deepcopy(qa)
    updated["human_review"] = {"status": decision, "reviewer": reviewer.strip(), "notes": notes.strip(),
                                "checklist": checklist.copy(), "audio_sha256": current_audio_hash,
                                "reviewed_at": datetime.now(timezone.utc).isoformat()}
    updated["status"] = decision
    updated["release_ready"] = decision == "approved"
    updated["human_listening_required"] = decision != "approved"
    return updated
