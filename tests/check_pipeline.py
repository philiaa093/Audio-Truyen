"""Chạy python -m tests.check_pipeline; không gọi dịch vụ hay ghi dữ liệu thật."""

import copy
import json
import secrets
import shutil
from pathlib import Path
from unittest.mock import patch

from backend.errors import InputError, ProviderError
from backend.services import pipeline


def main():
    paragraph = ("Lâm giữ chiếc chuông để nhớ người chèo đò. "
                 "Hạnh đưa anh về nhà rồi kể lại đêm lũ. "
                 "Anh hứa sẽ bảo vệ lời chứng của cô. ")
    text = "Chương 1: Bến sông\n" + paragraph * 90 + "\n***\n" + paragraph * 5
    text += "\n## Chương 2: Trở về\n" + paragraph * 90
    config = pipeline.validate_request({"text": text, "rights_confirmed": True, "rights_note": "Fixture tự tạo",
                                        "mode": "ai", "target_duration": 2,
                                        "pronunciation_lexicon": {"Lâm": "Lâm"}})
    calls = []
    critic_fails = False

    def fake_ai(instruction, payload):
        nonlocal critic_fails
        calls.append((instruction, copy.deepcopy(payload)))
        source = payload["source"]
        assert sum(len(sentence["text"]) for sentence in source) <= pipeline.SCENE_LIMIT
        assert len(json.dumps(payload, ensure_ascii=False)) < 80_000
        if "kiểm định viên fidelity" in instruction:
            report = {key: [] for key in ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff", "minor_errors")}
            if critic_fails:
                report["critical_errors"] = ["Mất động cơ của nhân vật"]
            return report
        if "Viết kịch bản" in instruction:
            return {"segments": [{"speaker": "NARRATOR", "emotion": "neutral", "text": sentence["text"],
                                  "source_ids": [sentence["id"]], "pause_after_ms": 350}
                                 for sentence in source if sentence["id"] in payload["plan"]["source_ids"]]}
        graph = pipeline.baseline_graph(source)
        if payload["previous_context"]:
            graph["beats"][0]["depends_on"] = [payload["previous_context"][-1]["id"]]
        return graph

    workspace = Path(__file__).resolve().parents[1] / "storage" / "tests"
    workspace.mkdir(parents=True, exist_ok=True)
    directory = workspace / (".pipeline-" + secrets.token_hex(8))
    directory.mkdir()
    try:
        with patch.object(pipeline, "ask_ai", fake_ai):
            result = pipeline.run_pipeline(config, directory)
        structure = result["structure"]
        assert len(structure["chapters"]) == 2
        assert len(structure["scenes"]) >= 5
        assert all(scene["characters"] <= pipeline.SCENE_LIMIT for scene in structure["scenes"])
        assert json.loads((Path(directory) / "structure.json").read_text(encoding="utf-8")) == structure
        source_by_id = {sentence["id"]: sentence for sentence in result["sentences"]}
        for sentence in result["sentences"]:
            start, end = sentence["source_span"]
            assert text[start:end] == sentence["text"]
            assert sentence["scene_id"] and sentence["chapter_id"]
            assert not sentence["text"].startswith(("Chương 1", "##", "***"))
        assert len({beat["id"] for beat in result["graph"]["beats"]}) == len(result["graph"]["beats"])
        assert any(beat["depends_on"] for beat in result["graph"]["beats"])
        pipeline.validate_graph(result["graph"], result["sentences"])
        variant = result["variants"][0]
        assert variant["rights_confirmed"] and variant["rights_note"]
        assert all(source_by_id[segment["source_ids"][0]]["scene_id"] == segment["scene_id"] for segment in variant["segments"])
        before = copy.deepcopy(result)
        with patch.object(pipeline, "ask_ai", fake_ai):
            candidate = pipeline.revise_for_duration(result, "balanced", 240)
        assert candidate and candidate["duration_revision"]["action"] == "shorten"
        assert candidate["plan"]["budget_units"] < variant["units"]
        assert set(beat["id"] for beat in result["graph"]["beats"] if beat["must_keep"]).issubset(candidate["plan"]["selected_beats"])
        assert result == before, "Vòng sửa đã thay đổi bản được duyệt trước khi tổng hợp"
        critic_fails = True
        with patch.object(pipeline, "ask_ai", fake_ai):
            failed = pipeline.revise_for_duration(result, "balanced", 240)
        assert failed["audio_blocked"] and failed["fidelity"]["critical_errors"]
        assert pipeline.revise_for_duration(result, "balanced", 120) is None
        assert pipeline.revise_for_duration({**result, "mode": "preview"}, "balanced", 240) is None
        for value in (0, float("nan"), True):
            try:
                pipeline.revise_for_duration(result, "balanced", value)
            except InputError:
                pass
            else:
                raise AssertionError("Cần từ chối số đo sai")
    finally:
        assert directory.resolve().parent == workspace and directory.name.startswith(".pipeline-")
        shutil.rmtree(directory)
    # Câu không có dấu chấm vẫn được chia mà không làm sai source_span.
    long_text = "ngọn đèn " * 2_000
    long_sentences = pipeline.source_sentences(long_text, pipeline.SCENE_LIMIT)
    assert len(long_sentences) > 1
    assert " ".join(sentence["text"] for sentence in long_sentences).split() == long_text.split()
    assert all(long_text[s["source_span"][0]:s["source_span"][1]] == s["text"] for s in long_sentences)
    # Từ "Hồi"/"Phần" mở đầu câu kể không phải tiêu đề chương.
    prose = ("Hồi ấy, Lâm sống ở bến sông. Anh giữ một chiếc chuông.\n\n"
             "Phần thưởng của anh là một ngọn đèn. Hạnh đưa nó cho anh.\n\n"
             "Hồi di cư, họ đã mang chiếc chuông theo.\n\n"
             "Đêm xuống, họ cùng trở về làng.")
    prose_sentences = pipeline.source_sentences(prose, pipeline.SCENE_LIMIT)
    expected = copy.deepcopy(prose_sentences)
    prose_structure = pipeline.story_structure(prose, prose_sentences)
    assert len(prose_structure["chapters"]) == 1
    assert [sentence["text"] for sentence in prose_sentences] == [sentence["text"] for sentence in expected]
    assert [sentence["source_span"] for sentence in prose_sentences] == [sentence["source_span"] for sentence in expected]
    assert all(prose[s["source_span"][0]:s["source_span"][1]] == s["text"] for s in prose_sentences)
    explicit = "Hồi IV: Bến cũ\n" + prose + "\nPhần thứ hai\n" + prose
    explicit_sentences = pipeline.source_sentences(explicit, pipeline.SCENE_LIMIT)
    explicit_structure = pipeline.story_structure(explicit, explicit_sentences)
    assert [chapter["title"] for chapter in explicit_structure["chapters"]] == ["Hồi IV: Bến cũ", "Phần thứ hai"]
    # Đồ thị sâu không phụ thuộc giới hạn recursion của Python.
    deep_source = pipeline.source_sentences("Một câu ngắn. " * 1_500)
    deep_graph = pipeline.baseline_graph(deep_source)
    for index, beat in enumerate(deep_graph["beats"]):
        beat["depends_on"] = [deep_graph["beats"][index - 1]["id"]] if index else []
    pipeline.validate_graph(deep_graph, deep_source)
    deep_graph["beats"][0]["depends_on"] = [deep_graph["beats"][-1]["id"]]
    try:
        pipeline.validate_graph(deep_graph, deep_source)
    except ProviderError:
        pass
    else:
        raise AssertionError("Cần từ chối vòng phụ thuộc")
    print("PASS: bounded chapter/scene calls, source spans, cross-scene dependencies, duration revision and fidelity blocking.")


if __name__ == "__main__":
    main()
