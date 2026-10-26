"""python -m tests.check_audio: quy tắc phát âm và vòng sửa audio có rollback."""

import copy
import json
import os
import secrets
import shutil
from pathlib import Path
from unittest.mock import patch

from backend.errors import InputError, ProviderError
from backend.services import audio, pipeline
from backend.services.pronunciation import integer_words, normalize_segments, normalize_text, validate_lexicon


def main():
    assert integer_words(0) == "không"
    assert integer_words(105) == "một trăm lẻ năm"
    assert integer_words(1025) == "một nghìn không trăm hai mươi lăm"
    assert integer_words("007") == "không không bảy"
    spoken, issues = normalize_text("Ngày 29/02/2024, lúc 08:05, lãi 3,5% trên 1.250.000 VNĐ.")
    assert spoken == ("ngày hai mươi chín tháng hai năm hai nghìn không trăm hai mươi bốn, "
                      "lúc tám giờ năm phút, lãi ba phẩy năm phần trăm trên một triệu hai trăm năm mươi nghìn đồng.")
    assert not issues
    assert normalize_text("31/02/2024 lúc 25:99")[1]
    assert normalize_text("1/2 và A12, 5kg")[1]
    assert normalize_text("08:05:02")[0] == "tám giờ năm phút hai giây"
    assert normalize_text("AI nói 1234567890123456789")[1]
    assert normalize_text("AI, ai đó; AIX.", {"AI": "ây ai"})[0] == "ây ai, ai đó; AIX."
    assert normalize_text("AI hỏi ai đã tới?", {"AI": "ây ai"})[0] == "ây ai hỏi ai đã tới?"
    assert normalize_text("New York và New.", {"New": "niu", "New York": "niu oóc"})[0] == "niu oóc và niu."
    for invalid in ([], {"": "a"}, {"A": "x", "a": "y"}, {"a": "\n"}):
        try:
            validate_lexicon(invalid)
        except InputError:
            pass
        else:
            raise AssertionError("Từ điển không hợp lệ được chấp nhận")
    segments = [{"text": "Lâm có 5 đồng.", "source_ids": ["T0001"], "pause_after_ms": 350}]
    normalized, report = normalize_segments(segments)
    assert segments[0]["text"] == normalized[0]["text"] == "Lâm có 5 đồng."
    assert normalized[0]["tts_text"] == "Lâm có năm đồng."
    assert report["changes"] and report["human_review_required"]
    assert list(audio.audio_chunks(normalized))[0][0] == "Lâm có năm đồng."
    long_spoken = [{"text": "1 " * 500, "tts_text": "một trăm " * 500, "pause_after_ms": 350}]
    chunks = list(audio.audio_chunks(long_spoken))
    assert len(chunks) > 1 and all(len(text) <= 2000 for text, pause in chunks)
    assert " ".join(text for text, pause in chunks).split() == long_spoken[0]["tts_text"].split()
    assert chunks[-1][1] == 350 and all(pause == 0 for text, pause in chunks[:-1])

    workspace = Path(__file__).resolve().parents[1] / "storage" / "tests"
    workspace.mkdir(parents=True, exist_ok=True)
    folder = workspace / (".audio-check-" + secrets.token_hex(8))
    folder.mkdir()
    variant = {"profile": "balanced", "audio_blocked": False, "segments": segments,
               "units": 5, "summary": "Lâm có 5 đồng.", "plan": {}, "fidelity": {}}
    result = {"mode": "ai", "target_duration": 1, "variants": [variant]}
    revised = copy.deepcopy(variant)
    revised["segments"][0]["text"] = "Lâm có 10 đồng."
    revised["summary"] = revised["segments"][0]["text"]
    environment = {"TTS_URL": "http://127.0.0.1/speech", "TTS_MODEL": "fake", "TTS_VOICE": "fake",
                   "LLM_URL": "http://127.0.0.1/chat", "LLM_MODEL": "fake"}
    calls = []

    def synthesize(current, data, destination, temporary, ffmpeg, progress):
        calls.append(current["segments"][0]["text"])
        actual = 30 if "5 đồng" in current["segments"][0]["text"] else 60
        (temporary / "master.wav").write_bytes(str(actual).encode())
        (temporary / "delivery.mp3").write_bytes(str(actual).encode())
        return ({"actual_duration_seconds": actual, "relative_duration_error": (actual - 60) / 60,
                 "within_pilot_tolerance": actual == 60}, [], {"chunks": []})

    try:
        with patch.dict(os.environ, environment), patch.object(audio.shutil, "which", return_value="ffmpeg"), \
                patch.object(audio, "_synthesize", side_effect=synthesize), \
                patch.object(pipeline, "revise_for_duration", return_value=revised, create=True), \
                patch("backend.services.qa.run_qa", return_value={"checks": {"asr": {"status": "incomplete"}}}):
            report = audio.render_audio(result, "balanced", folder)
        assert report["automatic_revisions"] == 1 and report["stop_reason"] == "within_tolerance"
        assert len(calls) == 2 and (folder / "balanced/master.wav").read_bytes() == b"60"
        assert json.loads((folder / "balanced/script.json").read_text(encoding="utf-8"))["segments"][0]["text"] == revised["summary"]
        assert result["variants"][0]["summary"] == revised["summary"]
        saved = {path: path.read_bytes() for path in (folder / "balanced").iterdir() if path.is_file()}
        prior_result = (folder / "result.json").read_bytes()
        result_before = copy.deepcopy(result)
        with patch.dict(os.environ, environment), patch.object(audio.shutil, "which", return_value="ffmpeg"), \
                patch.object(audio, "_synthesize", side_effect=ProviderError("fixture failure")):
            try:
                audio.render_audio(result, "balanced", folder)
            except ProviderError:
                pass
            else:
                raise AssertionError("Cần báo lỗi provider")
        assert result == result_before and (folder / "result.json").read_bytes() == prior_result
        assert all(path.read_bytes() == value for path, value in saved.items())
        no_change = {"mode": "ai", "target_duration": 1, "variants": [copy.deepcopy(variant)]}
        with patch.dict(os.environ, environment), patch.object(audio.shutil, "which", return_value="ffmpeg"), \
                patch.object(audio, "_synthesize", side_effect=synthesize), \
                patch.object(pipeline, "revise_for_duration", return_value=copy.deepcopy(variant), create=True), \
                patch("backend.services.qa.run_qa", return_value={}):
            report = audio.render_audio(no_change, "balanced", folder)
        assert report["stop_reason"] == "unchanged_script" and report["automatic_revisions"] == 0
        stage = folder / "stage"
        stage.mkdir()
        source, target = stage / "new.txt", folder / "existing.txt"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        try:
            audio._commit_files([(source, target), (stage / "missing", folder / "missing")], stage)
        except OSError:
            pass
        else:
            raise AssertionError("Cần thất bại khi thiếu file staging")
        assert target.read_text(encoding="utf-8") == "old"
    finally:
        assert folder.resolve().parent == workspace and folder.name.startswith(".audio-check-")
        shutil.rmtree(folder)
    print("PASS: Vietnamese normalization, lexicon validation, measured-duration revision, unchanged stop, transactional rollback.")


if __name__ == "__main__":
    main()
