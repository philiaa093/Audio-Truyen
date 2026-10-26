"""Chạy python -m tests.check_qa; không gọi dịch vụ ASR trả phí."""

import base64
import copy
import json
import math
import os
import random
import secrets
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

from backend.errors import InputError, ProviderError
from backend.services.qa import (CHECKLIST_FIELDS, acoustic_check, apply_human_review,
                                 asr_check, edit_distance, file_sha256, loudness_check, run_qa,
                                 transcript_metrics)


def rejects(exception, function, *args):
    try:
        function(*args)
    except exception:
        return
    raise AssertionError(function.__name__)


def slow_distance(left, right):
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = current
    return row[-1]


def make_wav(path, clipping=False, silent=False):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        values = [0 if silent else round(4000 * math.sin(i * 2 * math.pi * 220 / 16000)) for i in range(16000)]
        if clipping:
            values[10] = 32767
        stream.writeframes(b"".join(struct.pack("<h", value) for value in values))


def main():
    generator = random.Random(42)
    for _ in range(300):
        left = "".join(generator.choices("abc", k=generator.randrange(30)))
        right = "".join(generator.choices("abc", k=generator.randrange(30)))
        assert edit_distance(left, right) == slow_distance(left, right), (left, right)
    assert transcript_metrics("Lâm, về làng!", "lâm về làng")["cer"] == 0
    assert transcript_metrics("Lâm về làng", "Hạnh đi xa")["wer"] == 1
    rejects(ProviderError, transcript_metrics, "x", "x" * 12001)
    with patch("backend.services.qa.shutil.which", return_value="ffmpeg"), patch("backend.services.qa.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stderr=b"Integrated loudness:\n I: -18.1 LUFS\nTrue peak:\n Peak: -1.0 dBFS")):
        assert loudness_check("fixture.wav")["status"] == "passed"
    with patch("backend.services.qa.shutil.which", return_value="ffmpeg"), patch("backend.services.qa.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stderr=b"Integrated loudness:\n I: -18 LUFS\nTrue peak:\n Peak: 0.0 dBFS")):
        assert loudness_check("fixture.wav")["status"] == "failed"
    with patch("backend.services.qa.shutil.which", return_value="ffmpeg"), patch("backend.services.qa.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stderr=b"not measurements")):
        assert loudness_check("fixture.wav")["status"] == "incomplete"
    workspace = Path(__file__).resolve().parents[1] / "storage" / "tests"
    workspace.mkdir(parents=True, exist_ok=True)
    folder = workspace / (".qa-" + secrets.token_hex(8))
    folder.mkdir()
    loudness_fixture = patch("backend.services.qa.loudness_check", return_value={"status": "passed", "integrated_lufs": -18, "true_peak_dbtp": -1})
    loudness_fixture.start()
    try:
        profile = folder / "balanced"
        profile.mkdir()
        cache = profile / "tts_chunks"
        cache.mkdir()
        master, chunk = profile / "master.wav", cache / "chunk.wav"
        make_wav(master)
        make_wav(chunk)
        assert acoustic_check(master)["status"] == "passed"
        clipped = profile / "clipped.wav"
        make_wav(clipped, clipping=True)
        assert acoustic_check(clipped)["clipped_samples"] == 1
        assert acoustic_check(clipped)["status"] == "failed"
        silent = profile / "silent.wav"
        make_wav(silent, silent=True)
        assert acoustic_check(silent)["status"] == "failed"
        (folder / "source.json").write_text(json.dumps({"rights_confirmed": True, "rights_note": "Fixture tự tạo"}), encoding="utf-8")
        receipts = [{"path": str(chunk), "text": "Lâm về làng.", "scene_id": "S0001", "source_ids": ["T0001"]}]
        critic = {key: [] for key in ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff", "minor_errors")}
        variant = {"fidelity": {"method": "ai_review", **critic}, "audio_blocked": False}
        duration = {"within_pilot_tolerance": True, "actual_duration_seconds": 1,
                    "target_duration": 1 / 60, "relative_duration_error": 0}
        pronunciation = {"unresolved": []}
        with patch.dict(os.environ, {"ASR_URL": "", "ASR_MODEL": ""}):
            qa = run_qa(master, variant, duration, pronunciation, receipts)
        assert qa["status"] == "incomplete" and not qa["release_ready"]
        request = {"audio_sha256": file_sha256(master), "reviewer": "Biên tập viên",
                   "decision": "approved", "notes": "Đã nghe", "checklist": {key: True for key in CHECKLIST_FIELDS}}
        rejects(InputError, apply_human_review, qa, request, request["audio_sha256"])
        rejected = apply_human_review(qa, {**request, "decision": "rejected", "notes": "Thiếu ASR"}, request["audio_sha256"])
        assert rejected["status"] == "rejected" and not rejected["release_ready"]
        environment = {"ASR_URL": "http://127.0.0.1/asr", "ASR_MODEL": "fake-asr",
                       "ASR_API_KEY": "fixture-secret", "QA_MAX_CER": "0.1", "QA_MAX_WER": "0.2"}
        calls = []

        def fake_asr(url, payload, key):
            assert payload["language"] == "vi" and payload["model"] == "fake-asr"
            assert base64.b64decode(payload["audio_base64"]) == chunk.read_bytes()
            assert key == "fixture-secret"
            calls.append(payload)
            return {"text": "Lâm về làng"}

        with patch.dict(os.environ, environment), patch("backend.services.qa.request_json", fake_asr):
            qa = run_qa(master, variant, duration, pronunciation, receipts)
            assert qa["status"] == "ready_for_review", qa
            assert qa["checks"]["asr"]["wer"] == 0
            assert len(calls) == 1
            assert asr_check(master, receipts)["status"] == "passed"
            assert len(calls) == 1, "ASR không dùng cache"
            approved = apply_human_review(qa, request, request["audio_sha256"])
            assert approved["release_ready"] and approved["status"] == "approved"
            assert qa["human_review"]["status"] == "pending", "Hàm duyệt sửa hồ sơ gốc"
            rejects(InputError, apply_human_review, qa, {**request, "audio_sha256": "0" * 64}, request["audio_sha256"])
            checklist = request["checklist"].copy()
            checklist["style_emotion"] = False
            rejects(InputError, apply_human_review, qa, {**request, "checklist": checklist}, request["audio_sha256"])
            blocked = copy.deepcopy(qa)
            blocked["checks"]["duration"]["status"] = "failed"
            rejects(InputError, apply_human_review, blocked, request, request["audio_sha256"])
        assert "fixture-secret" not in "".join(path.read_text(encoding="utf-8") for path in folder.rglob("*.json"))
        with patch.dict(os.environ, {**environment, "ASR_MODEL": "uncalibrated", "QA_MAX_CER": "", "QA_MAX_WER": ""}), patch("backend.services.qa.request_json", return_value={"text": "Lâm về làng"}):
            assert asr_check(master, receipts)["status"] == "incomplete"
        with patch.dict(os.environ, {**environment, "ASR_MODEL": "mismatch"}), patch("backend.services.qa.request_json", return_value={"text": "Hạnh đi xa"}):
            assert asr_check(master, receipts)["status"] == "failed"
        with patch.dict(os.environ, {**environment, "ASR_MODEL": "bad-json"}), patch("backend.services.qa.request_json", return_value={"transcript": "wrong field"}):
            assert asr_check(master, receipts)["status"] == "incomplete"
        with patch.dict(os.environ, {**environment, "ASR_MODEL": "failure"}), patch("backend.services.qa.request_json", side_effect=ProviderError("API trả HTTP 503.")):
            assert asr_check(master, receipts)["status"] == "incomplete"
        preview = {**variant, "fidelity": {"method": "verbatim_extraction"}}
        with patch.dict(os.environ, environment), patch("backend.services.qa.request_json", fake_asr):
            assert run_qa(master, preview, duration, pronunciation, receipts)["status"] == "incomplete"
            assert run_qa(master, variant, {**duration, "within_pilot_tolerance": False}, pronunciation, receipts)["status"] == "blocked"
            assert run_qa(master, variant, duration, {"unresolved": [{"token": "XYZ"}]}, receipts)["status"] == "incomplete"
        print("PASS: exact CER/WER, PCM clipping/silence, ASR protocol/cache/failure, calibrated gates, stale audio and human review.")
    finally:
        loudness_fixture.stop()
        assert folder.resolve().parent == workspace.resolve() and folder.name.startswith(".qa-")
        shutil.rmtree(folder)


if __name__ == "__main__":
    main()
