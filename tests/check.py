"""Chạy python -m tests.check. Chỉ gọi máy chủ giả tại máy, không tiêu phí API."""

import copy
import base64
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import struct
import threading
import time
import urllib.error
import urllib.request
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend import server
from backend.errors import InputError, ProviderError
from backend.services.audio import audio_chunks, render_audio
from backend.services.pipeline import (baseline_graph, make_plan, normalize,
                      run_pipeline, source_sentences, validate_critic, validate_graph,
                      validate_request, validate_script)
from backend.services.qa import CHECKLIST_FIELDS


TEXT = ("Lâm trở về làng sau nhiều năm xa cách.\n\n"
        "Hạnh đưa anh tới bến sông để giữ chiếc chuông cũ. "
        "Cô nhắc lại đêm lũ khi người chèo đò đã cứu anh.\n\n"
        "Lâm đồng ý làm chứng để chiếc chuông được giữ lại. "
        "Họ ngồi bên nhau đến khi ngọn đèn dầu tắt.")
DATA = {"text": TEXT, "title": "Đèn thử nghiệm", "rights_confirmed": True,
        "rights_note": "Văn bản kỹ thuật tự tạo cho check.py"}


def rejects(exception, function, *args):
    try:
        function(*args)
    except exception:
        return
    raise AssertionError(f"Cần từ chối dữ liệu: {function.__name__}")


class FakeProvider(BaseHTTPRequestHandler):
    speech_calls = 0
    fail_speech = False
    audio_transcripts = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/speech":
            type(self).speech_calls += 1
            if self.fail_speech:
                self.send_response(503)
                self.end_headers()
                return
            assert payload["response_format"] == "wav"
            # Sóng sin chỉ là fixture âm thanh, không phải giọng TTS hay benchmark.
            output = io.BytesIO()
            with wave.open(output, "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                tag = hashlib.sha256(payload["input"].encode("utf-8")).digest()
                stream.writeframes(b"".join(struct.pack("<h", tag[i] if i < len(tag) else round(2000 * math.sin(i * 2 * math.pi * 220 / 16000)))
                                            for i in range(16000)))
            body, content_type = output.getvalue(), "audio/wav"
            self.audio_transcripts[hashlib.sha256(body).hexdigest()] = payload["input"]
        elif self.path == "/asr":
            assert payload["language"] == "vi"
            digest = hashlib.sha256(base64.b64decode(payload["audio_base64"])).hexdigest()
            body = json.dumps({"text": self.audio_transcripts[digest]}, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        else:
            instruction = payload["messages"][0]["content"]
            source = json.loads(payload["messages"][1]["content"])
            if "kiểm định viên fidelity" in instruction:
                content = {key: [] for key in ("critical_errors", "major_errors", "minor_errors",
                                               "unsupported_claims", "broken_setup_payoff")}
            elif "Viết kịch bản" in instruction:
                content = {"segments": [{"speaker": "NARRATOR", "text": sentence["text"],
                                          "emotion": "neutral", "pause_after_ms": 350,
                                          "source_ids": [sentence["id"]]}
                                         for sentence in source["source"] if sentence["id"] in source["plan"]["source_ids"]]}
            else:
                content = baseline_graph(source["source"])
            body = json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    from tests import check_audio, check_pipeline, check_qa
    for check in (check_pipeline, check_audio, check_qa):
        check.main()
    for value in (None, [], {**DATA, "profile": []}, {**DATA, "rights_confirmed": False},
                  {**DATA, "target_duration": True}, {**DATA, "target_duration": float("nan")},
                  {**DATA, "target_duration": 10 ** 500}, {**DATA, "text": "quá ngắn"}):
        rejects(InputError, validate_request, value)
    normalized = normalize("\ufeff" + TEXT.replace("\n", "\r\n"))
    assert normalized == TEXT
    sentences = source_sentences(normalized)
    assert " ".join(s["text"] for s in sentences).split() == normalized.split()
    for sentence in sentences:
        start, end = sentence["source_span"]
        assert normalized[start:end] == sentence["text"]
    graph = baseline_graph(sentences)
    graph["beats"][0]["must_keep"] = False
    graph["beats"][-1]["depends_on"] = [graph["beats"][0]["id"]]
    validate_graph(graph, sentences)
    plan = make_plan(graph, sentences, "compact", 0.5)
    assert graph["beats"][-1]["id"] in plan["selected_beats"]
    assert graph["beats"][0]["id"] in plan["selected_beats"]
    bad = copy.deepcopy(graph)
    bad["beats"][0]["depends_on"] = [bad["beats"][-1]["id"]]
    rejects(ProviderError, validate_graph, bad, sentences)
    bad = copy.deepcopy(graph)
    bad["beats"][0]["source_ids"] = ["T9999"]
    rejects(ProviderError, validate_graph, bad, sentences)
    rejects(ProviderError, validate_script, {"segments": [{"speaker": "NARRATOR", "text": "Bịa",
            "emotion": "neutral", "source_ids": ["T9999"]}]}, plan, sentences)
    rejects(ProviderError, validate_critic, {})
    critic = {key: [] for key in ("critical_errors", "major_errors", "minor_errors", "unsupported_claims", "broken_setup_payoff")}
    assert "human_review_required" not in validate_critic({**critic, "human_review_required": False})
    assert list(audio_chunks([{"text": TEXT, "pause_after_ms": 450}]))[-1][1] == 450

    workspace = Path(__file__).resolve().parents[1] / "storage" / "tests"
    workspace.mkdir(parents=True, exist_ok=True)
    folder = workspace / (".check-" + secrets.token_hex(8))
    folder.mkdir()
    try:
        result = run_pipeline(validate_request(DATA), folder / "direct")
        assert [v["profile"] for v in result["variants"]] == ["preserve", "balanced", "compact"]
        for variant in result["variants"]:
            for segment in variant["segments"]:
                assert any(s["text"] == segment["text"] for s in sentences)
        provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        for item in (provider, http_server):
            threading.Thread(target=item.serve_forever, daemon=True).start()
        provider_url = f"http://127.0.0.1:{provider.server_port}"
        base = f"http://127.0.0.1:{http_server.server_port}"
        outputs = folder / "outputs"
        outputs.mkdir()
        environment = {"LLM_URL": provider_url + "/chat", "LLM_MODEL": "fake-editor",
                       "LLM_API_KEY": "fixture-secret", "TTS_URL": provider_url + "/speech",
                       "TTS_MODEL": "fake-speech", "TTS_VOICE": "fixture", "TTS_API_KEY": "fixture-secret",
                       "ASR_URL": provider_url + "/asr", "ASR_MODEL": "fake-asr", "ASR_API_KEY": "fixture-secret",
                       "QA_MAX_CER": "0", "QA_MAX_WER": "0"}

        def http(path, payload=None, headers=None, expected=200):
            request = urllib.request.Request(base + path,
                        json.dumps(payload).encode("utf-8") if payload is not None else None,
                        {"Content-Type": "application/json", "X-CSRF-Token": server.TOKEN, **(headers or {})})
            try:
                response = urllib.request.urlopen(request, timeout=20)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read()
                assert response.status == expected, (path, response.status, body)
                return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body

        def completed(job_id):
            for _ in range(200):
                job = http("/api/jobs/" + job_id)
                if job["status"] != "running":
                    assert job["status"] == "completed", job["message"]
                    return job
                time.sleep(0.1)
            raise AssertionError("Xử lý quá 20 giây")

        try:
            with patch.object(server, "PROJECTS_DIR", outputs), patch.dict(os.environ, environment):
                page = http("/")
                assert b"__TOKEN__" not in page
                assert b'<link rel="stylesheet" href="/css/styles.css">' in page
                assert b'<script src="/js/app.js" defer></script>' in page
                assert b"<style>" not in page and b"<script nonce=" not in page
                assert b"--paper:" in http("/css/styles.css")
                assert b"async function api" in http("/js/app.js")
                http("/backend/server.py", expected=404)
                http("/js/../../backend/server.py", expected=404)
                assert http("/api/health")["tts_configured"]
                assert http("/api/health")["max_source"] == 1_000_000
                assert len(http("/api/sample")["text"]) > 1000
                http("/api/jobs", DATA, {"X-CSRF-Token": "sai"}, 403)
                http("/api/jobs", DATA, {"Origin": "https://example.org"}, 403)
                http("/api/jobs", {**DATA, "rights_confirmed": False}, expected=400)
                http("/api/jobs", {**DATA, "profile": []}, expected=400)
                job = http("/api/jobs", {**DATA, "mode": "ai", "target_duration": 0.5}, expected=202)
                job = completed(job["id"])
                assert len(job["result"]["variants"]) == 1
                job_id = job["id"]
                http(f"/api/jobs/{job_id}/audio", {"profile": "balanced", "approved": False}, expected=400)
                if server.shutil.which("ffmpeg"):
                    http(f"/api/jobs/{job_id}/audio", {"profile": "balanced", "approved": True}, expected=202)
                    job = completed(job_id)
                    variant = job["result"]["variants"][0]
                    assert variant["actual_duration_seconds"] > 0
                    assert variant["duration_report"]["within_pilot_tolerance"] is False
                    assert http(f"/api/jobs/{job_id}/balanced/master.wav")[:4] == b"RIFF"
                    assert len(http(f"/api/jobs/{job_id}/balanced/delivery.mp3", headers={"Range": "bytes=0-15"}, expected=206)) == 16
                    before = FakeProvider.speech_calls
                    render_audio(job["result"], "balanced", outputs / job_id)
                    assert FakeProvider.speech_calls == before, "Đã tạo lại chunk thay vì dùng cache"
                    previous = (outputs / job_id / "balanced" / "master.wav").read_bytes()
                    FakeProvider.fail_speech = True
                    with patch.dict(os.environ, {"TTS_VOICE": "failed-fixture"}):
                        rejects(ProviderError, render_audio, job["result"], "balanced", outputs / job_id)
                    assert (outputs / job_id / "balanced" / "master.wav").read_bytes() == previous
                    FakeProvider.fail_speech = False
                    qa = variant["qa"]
                    review = {"profile": "balanced", "audio_sha256": qa["audio_sha256"],
                              "reviewer": "Kiểm tra kỹ thuật", "decision": "approved", "notes": "",
                              "checklist": {field: True for field in CHECKLIST_FIELDS}}
                    http(f"/api/jobs/{job_id}/review", review, expected=400)
                    rejected = http(f"/api/jobs/{job_id}/review", {**review, "decision": "rejected", "notes": "Thời lượng chưa đạt."})
                    assert rejected["status"] == "rejected" and not rejected["release_ready"]

                    ready = http("/api/jobs", {**DATA, "mode": "ai"}, expected=202)
                    ready = completed(ready["id"])
                    http(f"/api/jobs/{ready['id']}/audio", {"profile": "balanced", "approved": True}, expected=202)
                    ready = completed(ready["id"])
                    qa = next(v for v in ready["result"]["variants"] if v["profile"] == "balanced")["qa"]
                    assert qa["status"] == "ready_for_review", qa
                    review["audio_sha256"] = qa["audio_sha256"]
                    http(f"/api/jobs/{ready['id']}/review", {**review, "audio_sha256": "0" * 64}, expected=400)
                    approved = http(f"/api/jobs/{ready['id']}/review", review)
                    assert approved["release_ready"] and approved["status"] == "approved"
                    saved = http(f"/api/jobs/{ready['id']}")
                    assert next(v for v in saved["result"]["variants"] if v["profile"] == "balanced")["qa"] == approved
                draft = outputs / job_id / ".render-fixture"
                draft.mkdir()
                (draft / "draft.txt").write_text("Chưa hoàn tất", encoding="utf-8")
                archive = zipfile.ZipFile(io.BytesIO(http(f"/api/jobs/{job_id}/download")))
                assert {"raw.txt", "clean.txt", "story_graph.json", "balanced/script.json"}.issubset(archive.namelist())
                assert not any(".render-" in name for name in archive.namelist())
                with server.WORKER:
                    http(f"/api/jobs/{job_id}/download", expected=409)
                    http(f"/api/jobs/{job_id}/review", {"profile": "balanced"}, expected=409)
                assert "fixture-secret" not in "".join(p.read_text(encoding="utf-8") for p in outputs.rglob("*.json"))
                assert job_id in {job["id"] for job in http("/api/jobs")}
        finally:
            for item in (http_server, provider):
                item.shutdown()
                item.server_close()
    finally:
        assert folder.resolve().parent == workspace and folder.name.startswith(".check-")
        shutil.rmtree(folder)
    print("PASS: input, source spans, graph dependencies, AI/TTS/ASR protocol, HTTP gates, archive, audio/cache/failure preservation, human review API.")


if __name__ == "__main__":
    main()
