"""Biên tập truyện có dẫn chứng; chỉ dùng thư viện chuẩn Python."""

import json
import math
import re
import unicodedata
from collections import deque
from pathlib import Path

from backend.errors import InputError, ProviderError
from backend.providers import ask_ai


MAX_SOURCE = 1_000_000
SCENE_LIMIT = 8_000
CONTEXT_LIMIT = 1_600
PROFILES = {"preserve": 0.70, "balanced": 0.48, "compact": 0.28}
PROFILE_NAMES = {"preserve": "Giữ nhiều", "balanced": "Cân bằng", "compact": "Rút mạnh"}
RATE = 145  # Khuyến nghị pilot: đơn vị cách trắng/phút, chưa phải tốc độ đo.


def normalize(text):
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip().lstrip("\ufeff").strip()


def units(text):
    return len(text.split())


def validate_request(data):
    if not isinstance(data, dict):
        raise InputError("Yêu cầu phải là một đối tượng JSON.")
    for key, limit in (("text", MAX_SOURCE), ("title", 160), ("author", 160), ("rights_note", 1000)):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise InputError(f"Trường {key} phải là văn bản, tối đa {limit:,} ký tự.")
    text = normalize(data.get("text", ""))
    if units(text) < 20 or "\x00" in text:
        raise InputError("Nhập ít nhất 20 đơn vị cách trắng, không chứa ký tự NUL.")
    if data.get("mode", "preview") not in ("preview", "ai"):
        raise InputError("Chế độ không hợp lệ.")
    if data.get("rights_confirmed") is not True or not data.get("rights_note", "").strip():
        raise InputError("Cần xác nhận quyền sử dụng và ghi nguồn hoặc căn cứ sử dụng.")
    target = data.get("target_duration")
    if target is not None and (type(target) not in (int, float) or not 0.5 <= target <= 180 or not math.isfinite(target)):
        raise InputError("Thời lượng phải từ 0,5 đến 180 phút, hoặc để trống.")
    profile = data.get("profile", "balanced")
    if not isinstance(profile, str) or profile not in PROFILES:
        raise InputError("Mức rút gọn không hợp lệ.")
    from backend.services.pronunciation import validate_lexicon
    lexicon = validate_lexicon(data.get("pronunciation_lexicon", {}))
    return {
        "text": text, "raw_text": data["text"], "title": data.get("title", "").strip() or "Chương chưa đặt tên",
        "author": data.get("author", "").strip(), "rights_note": data["rights_note"].strip(),
        "rights_confirmed": True, "mode": data.get("mode", "preview"),
        "profile": profile, "target_duration": target, "pronunciation_lexicon": lexicon,
    }


def source_sentences(text, limit=None):
    # ponytail: ranh giới theo đoạn và dấu câu; thay bằng NLP khi cần xử lý OCR/viết tắt phức tạp.
    result = []
    pattern = r'[^\n]+?(?:[.!?…]+[”"’»)]*(?=\s|$)|(?=\n|$))'
    for match in re.finditer(pattern, text):
        raw = match.group()
        start = match.start() + len(raw) - len(raw.lstrip())
        value = raw.strip()
        while value:
            length = len(value)
            if limit and length > limit:
                boundaries = list(re.finditer(r"\s", value[:limit + 1]))
                length = boundaries[-1].start() if boundaries else -1
                if length <= 0:
                    raise InputError("Một chuỗi không có khoảng trắng vượt giới hạn xử lý cảnh.")
            part = value[:length].rstrip()
            result.append({"id": f"T{len(result) + 1:04d}", "text": part,
                           "source_span": [start, start + len(part)]})
            remainder = value[length:]
            skipped = len(remainder) - len(remainder.lstrip())
            start += length + skipped
            value = remainder.lstrip()
    return result


def story_structure(text, sentences):
    """Chia chương/cảnh bằng dấu mốc tác giả và giới hạn ngữ cảnh; giữ span nguồn."""
    # ponytail: cảnh theo dấu phân cách/kích thước, chưa suy ra chuyển cảnh ngữ nghĩa; nâng cấp bằng NLP khi cần.
    markers = []
    ordinal = r"(?:[0-9]+|[IVXLCDM]+|(?i:thứ[ \t]+(?:[0-9]+|nhất|nhì|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười)))"
    pattern = (r"(?m)^[ \t]*(?:#{1,6}[ \t]+[^\n]+|(?i:chương|hồi|phần)[ \t]+" + ordinal
               + r"(?=$|[ \t:.,\-–—])[^\n]*|(?:\*[ \t]*){3,}|(?:-[ \t]*){3,})[ \t]*$")
    for match in re.finditer(pattern, text):
        title = match.group().strip()
        separator = bool(re.fullmatch(r"(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}", title))
        markers.append((match.start(), match.end(), None if separator else title.lstrip("# ")))
    chapters, scenes = [], []
    current_chapter = None
    current_scene = None
    marker_index = 0
    pending_boundary = True
    for sentence in sentences:
        position = sentence["source_span"][0]
        while marker_index < len(markers) and markers[marker_index][0] <= position:
            _, _, title = markers[marker_index]
            if title is not None:
                current_chapter = {"id": f"C{len(chapters) + 1:04d}", "title": title, "scene_ids": []}
                chapters.append(current_chapter)
            pending_boundary = True
            marker_index += 1
        # Tiêu đề và dấu phân cách là metadata, không phải lời cần đọc.
        if any(start <= position < end for start, end, _ in markers[max(0, marker_index - 1):marker_index]):
            continue
        if current_chapter is None:
            current_chapter = {"id": f"C{len(chapters) + 1:04d}", "title": "Phần mở đầu", "scene_ids": []}
            chapters.append(current_chapter)
        if pending_boundary or current_scene is None or current_scene["characters"] + len(sentence["text"]) + 1 > SCENE_LIMIT:
            current_scene = {"id": f"S{len(scenes) + 1:04d}", "chapter_id": current_chapter["id"],
                             "title": f"Cảnh {len(scenes) + 1}",
                             "source_ids": [], "source_span": [position, position], "characters": 0}
            scenes.append(current_scene)
            current_chapter["scene_ids"].append(current_scene["id"])
            pending_boundary = False
        sentence["chapter_id"] = current_chapter["id"]
        sentence["scene_id"] = current_scene["id"]
        current_scene["characters"] += len(sentence["text"]) + bool(current_scene["source_ids"])
        current_scene["source_ids"].append(sentence["id"])
        current_scene["source_span"][1] = sentence["source_span"][1]
    sentences[:] = [sentence for sentence in sentences if "scene_id" in sentence]
    return {"method": "headings_separators_and_bounded_chunks", "scene_character_limit": SCENE_LIMIT,
            "chapters": [chapter for chapter in chapters if chapter["scene_ids"]], "scenes": scenes,
            "continuity_limit": "AI chỉ đối chiếu từng cảnh và ngữ cảnh trước giới hạn 1.600 ký tự; cần người duyệt setup/payoff xa nhau."}


def baseline_graph(sentences):
    """Không suy diễn plot. Mỗi câu là một beat trích nguyên văn để thử luồng."""
    beats = []
    for index, sentence in enumerate(sentences):
        dialogue = any(mark in sentence["text"] for mark in ('“', '”', '"', '—'))
        beats.append({"id": f"B{index + 1:04d}", "source_ids": [sentence["id"]],
                      "description": sentence["text"], "score": 0.5 + 0.2 * dialogue,
                      "must_keep": index in (0, len(sentences) - 1), "depends_on": []})
    return {"method": "extractive_preview", "style": "Giữ nguyên câu nguồn; chưa phân tích phong cách.",
            "characters": [], "beats": beats}


def validate_graph(graph, sentences, external_ids=()):
    if not isinstance(graph, dict) or not isinstance(graph.get("beats"), list) or not graph["beats"]:
        raise ProviderError("AI không trả về danh sách beats hợp lệ.")
    if len(graph["beats"]) > len(sentences) * 3:
        raise ProviderError("AI trả quá nhiều beats.")
    source_ids = {sentence["id"] for sentence in sentences}
    beats = graph["beats"]
    ids = []
    for beat in beats:
        if not isinstance(beat, dict) or not isinstance(beat.get("id"), str) or not re.fullmatch(r"B[0-9]{1,7}", beat["id"]):
            raise ProviderError("ID beat do AI trả về không hợp lệ.")
        refs = beat.get("source_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(s, str) or s not in source_ids for s in refs):
            raise ProviderError("Beat có dẫn chứng không tồn tại trong nguồn.")
        if type(beat.get("score")) not in (int, float) or not 0 <= beat["score"] <= 1:
            raise ProviderError("Điểm quan trọng phải từ 0 đến 1.")
        if type(beat.get("must_keep")) is not bool or not isinstance(beat.get("description"), str) or not 0 < len(beat["description"]) <= SCENE_LIMIT:
            raise ProviderError("Beat thiếu mô tả hoặc cờ must_keep.")
        if not isinstance(beat.get("depends_on"), list) or any(not isinstance(d, str) for d in beat["depends_on"]):
            raise ProviderError("Quan hệ phụ thuộc không hợp lệ.")
        ids.append(beat["id"])
    id_set = set(ids)
    if len(ids) != len(id_set) or any(dep not in id_set and dep not in external_ids for beat in beats for dep in beat["depends_on"]):
        raise ProviderError("ID beat bị trùng hoặc phụ thuộc không tồn tại.")
    indegree = {beat["id"]: sum(dep in id_set for dep in beat["depends_on"]) for beat in beats}
    dependents = {beat_id: [] for beat_id in ids}
    for beat in beats:
        for dependency in beat["depends_on"]:
            if dependency in id_set:
                dependents[dependency].append(beat["id"])
    ready = deque(beat_id for beat_id in ids if indegree[beat_id] == 0)
    visited = 0
    while ready:
        visited += 1
        for dependent in dependents[ready.popleft()]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(ids):
        raise ProviderError("Story graph có vòng phụ thuộc.")
    if not isinstance(graph.get("style", ""), str) or len(graph.get("style", "")) > 2000:
        raise ProviderError("Mô tả phong cách không hợp lệ.")
    characters = graph.get("characters", [])
    if not isinstance(characters, list) or len(characters) > 10_000 or any(not isinstance(name, str) or len(name) > 160 for name in characters):
        raise ProviderError("Danh sách nhân vật không hợp lệ.")
    return graph


def make_plan(graph, sentences, profile, target_duration, budget_units=None):
    budget = max(1, round(budget_units if budget_units is not None else target_duration * RATE * 0.92 if target_duration else sum(units(s["text"]) for s in sentences) * PROFILES[profile]))
    by_id = {beat["id"]: beat for beat in graph["beats"]}
    source = {sentence["id"]: sentence for sentence in sentences}

    def closure(beat_ids, existing=()):
        found, pending = set(), list(beat_ids)
        while pending:
            beat_id = pending.pop()
            if beat_id not in found and beat_id not in existing:
                found.add(beat_id)
                pending.extend(by_id[beat_id]["depends_on"])
        return found

    def refs(beat_ids):
        return {ref for beat_id in beat_ids for ref in by_id[beat_id]["source_ids"]}

    selected = closure(beat["id"] for beat in graph["beats"] if beat["must_keep"])
    selected_refs = refs(selected)
    selected_cost = sum(units(source[ref]["text"]) for ref in selected_refs)
    mandatory_cost = selected_cost
    for beat in sorted(graph["beats"], key=lambda b: (-b["score"], b["id"])):
        if beat["id"] in selected:
            continue
        additions = closure([beat["id"]], selected)
        added_refs = refs(additions) - selected_refs
        added_cost = sum(units(source[ref]["text"]) for ref in added_refs)
        if selected_cost + added_cost <= budget:
            selected.update(additions)
            selected_refs.update(added_refs)
            selected_cost += added_cost
    if not selected:
        selected = closure([max(graph["beats"], key=lambda b: b["score"])["id"]])
        selected_refs = refs(selected)
        selected_cost = sum(units(source[ref]["text"]) for ref in selected_refs)
    return {"profile": profile, "target_duration": target_duration, "budget_units": budget,
            "selected_beats": [b["id"] for b in graph["beats"] if b["id"] in selected],
            "source_ids": [s["id"] for s in sentences if s["id"] in selected_refs],
            "selected_source_units": selected_cost, "required_over_budget": mandatory_cost > budget,
            "removed_beats": [b["id"] for b in graph["beats"] if b["id"] not in selected]}


def analyze(sentences, previous_context=()):
    graph = ask_ai(
        'Phân tích nhân quả và phong cách từ nguồn. Trả {"style": "...", "characters": ["..."], '
        '"beats": [{"id": "B0001", "description": "sự kiện", "source_ids": ["T0001"], '
        '"score": 0.95, "must_keep": true, "depends_on": []}]}. '
        'Dẫn chứng phải tồn tại. depends_on gồm cả setup cần cho payoff; không tạo vòng. '
        'Đánh dấu biến cố mở đầu, cao trào, kết cục và manh mối không thể thiếu là must_keep. '
        'Không thêm sự kiện, đổi người nói hoặc giải thích bí mật mà nguồn cố ý để mở. '
        'Đây là một cảnh của truyện dài. previous_context chỉ là ngữ cảnh, không được dẫn source_ids ngoài source. '
        'depends_on có thể trỏ tới ID P trong previous_context khi cần giữ setup từ cảnh trước.',
        {"source": sentences, "previous_context": previous_context})
    return validate_graph(graph, sentences, {item["id"] for item in previous_context})


def validate_script(value, plan, sentences):
    segments = value.get("segments") if isinstance(value, dict) else None
    if not isinstance(segments, list) or not segments or len(segments) > len(sentences) * 3:
        raise ProviderError("AI không trả danh sách segments hợp lệ.")
    if sum(len(s.get("text", "")) for s in segments if isinstance(s, dict) and isinstance(s.get("text", ""), str)) > MAX_SOURCE:
        raise ProviderError("Tổng kịch bản do AI trả về vượt giới hạn đầu vào.")
    allowed = set(plan["source_ids"])
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str) or not segment["text"].strip() or len(segment["text"]) > MAX_SOURCE:
            raise ProviderError("Đoạn kịch bản không hợp lệ.")
        refs = segment.get("source_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(s, str) or s not in allowed for s in refs):
            raise ProviderError("Kịch bản có dẫn chứng ngoài kế hoạch đã chọn.")
        for field in ("speaker", "emotion"):
            if not isinstance(segment.get(field), str) or len(segment[field]) > 100:
                raise ProviderError("Nhãn người nói hoặc cảm xúc không hợp lệ.")
        pause = segment.get("pause_after_ms", 350)
        if type(pause) is not int or not 0 <= pause <= 2000:
            raise ProviderError("Khoảng nghỉ phải từ 0 đến 2000 ms.")
        segment["pause_after_ms"] = pause
    cited = {ref for segment in segments for ref in segment["source_ids"]}
    if not allowed.issubset(cited):
        raise ProviderError("Kịch bản bỏ dẫn chứng của nội dung đã chọn. Cần chạy lại AI.")
    return segments


def validate_critic(report):
    keys = ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff", "minor_errors")
    if not isinstance(report, dict) or any(not isinstance(report.get(key), list) for key in keys):
        raise ProviderError("AI kiểm định trả thiếu các nhóm lỗi bắt buộc.")
    if any(len(report[key]) > 100 for key in keys) or any(not isinstance(item, str) or len(item) > 2000 for key in keys for item in report[key]):
        raise ProviderError("Mỗi lỗi kiểm định phải là văn bản.")
    return {key: report[key] for key in keys}


def scene_groups(sentences):
    groups = {}
    for sentence in sentences:
        groups.setdefault(sentence.get("scene_id", "S0001"), []).append(sentence)
    return groups


def beat_context(beats):
    context, remaining = [], CONTEXT_LIMIT
    for beat in reversed(beats[-8:]):
        description = beat["description"][:remaining]
        if not description:
            break
        context.append({"id": "P" + beat["id"][1:], "description": description})
        remaining -= len(description)
    return list(reversed(context))


def analyze_story(sentences, mode, progress=lambda message: None):
    merged, cards, characters = [], [], []
    for scene_id, source in scene_groups(sentences).items():
        progress(f"Đang phân tích cảnh {scene_id}")
        graph = analyze(source, beat_context(merged)) if mode == "ai" else baseline_graph(source)
        remap = {beat["id"]: f"B{len(merged) + index + 1:04d}" for index, beat in enumerate(graph["beats"])}
        for beat in graph["beats"]:
            merged.append({**beat, "id": remap[beat["id"]], "scene_id": scene_id,
                           "chapter_id": source[0].get("chapter_id", "C0001"),
                           "depends_on": [remap[dep] if dep in remap else "B" + dep[1:] for dep in beat["depends_on"]]})
        for name in graph.get("characters", []):
            if name not in characters:
                characters.append(name)
        cards.append({"scene_id": scene_id, "style": graph.get("style", ""),
                      "characters": graph.get("characters", []), "beat_ids": list(remap.values())})
    combined = {"method": "scene_ai_review" if mode == "ai" else "extractive_preview",
                "style": "Phong cách được ghi riêng theo scene_cards.", "characters": characters,
                "beats": merged, "scene_cards": cards,
                "continuity_scope": "Cảnh hiện tại và tối đa 1.600 ký tự beat của cảnh trước; người duyệt kiểm tra quan hệ xa."}
    return validate_graph(combined, sentences)


def create_variant(config, sentences, graph, profile, budget_units=None, duration_feedback=None):
    plan = make_plan(graph, sentences, profile, config["target_duration"], budget_units)
    segments = []
    keys = ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff", "minor_errors")
    fidelity = {"method": "ai_review" if config["mode"] == "ai" else "verbatim_extraction",
                "human_review_required": True, "continuity_scope": graph.get("continuity_scope", "Một cảnh"),
                **{key: [] for key in keys}}
    if config["mode"] != "ai":
        fidelity["warning"] = "Trích câu bằng quy tắc; chưa kiểm định plot, người nói, nhân quả hay setup/payoff."
    selected = set(plan["source_ids"])
    by_source = {sentence["id"]: sentence for sentence in sentences}
    beats_by_scene = {}
    for beat in graph["beats"]:
        scene_id = by_source[beat["source_ids"][0]].get("scene_id", "S0001")
        beats_by_scene.setdefault(scene_id, []).append(beat)
    styles = {card["scene_id"]: card["style"] for card in graph.get("scene_cards", [])}
    previous_context = ""
    for scene_id, source in scene_groups(sentences).items():
        refs = [sentence["id"] for sentence in source if sentence["id"] in selected]
        if not refs:
            continue
        local_beats = beats_by_scene.get(scene_id, [])
        selected_beats = set(plan["selected_beats"])
        local_plan = {**plan, "source_ids": refs,
                      "budget_units": max(1, round(plan["budget_units"] * sum(units(by_source[ref]["text"]) for ref in refs) / max(1, plan["selected_source_units"]))),
                      "selected_beats": [beat["id"] for beat in local_beats if beat["id"] in selected_beats],
                      "removed_beats": [beat["id"] for beat in local_beats if beat["id"] not in selected_beats]}
        local_graph = {"style": styles.get(scene_id, graph.get("style", "")), "beats": local_beats}
        payload = {"source": source, "story_graph": local_graph, "plan": local_plan,
                   "previous_context": previous_context, "duration_feedback": duration_feedback}
        if config["mode"] == "ai":
            script = ask_ai(
            'Viết kịch bản rút gọn từ nguồn và kế hoạch. Giữ nguyên POV, động cơ, kết cục, '
            'nhân quả, hình ảnh và câu thoại quan trọng. Không thêm plot hay nhân vật. '
            'Mỗi source_id được chọn phải được phản ánh và dẫn ít nhất một lần. '
            'Cố gắng đạt budget_units đơn vị tách bằng khoảng trắng; ưu tiên sự thật khi ngân sách quá nhỏ. '
            'Trả {"segments": [{"speaker": "NARRATOR", "text": "lời thực sự đọc", '
            '"emotion": "neutral", "pause_after_ms": 350, "source_ids": ["T0001"]}]}. '
            'Không chèn chỉ dẫn diễn xuất vào text. Chỉ viết cảnh đang nhận. '
            'Nếu duration_feedback khác null, co/giãn theo budget_units sau đo audio thật, '
            'chỉ khôi phục chi tiết có trong nguồn, không thêm plot hay tăng tốc giọng. '
            'previous_context chỉ giúp nối mạch, không lặp lại hoặc dẫn ID ngoài source.', payload)
            local_segments = validate_script(script, local_plan, source)
            if sum(len(segment["text"]) for segment in local_segments) > SCENE_LIMIT * 2:
                raise ProviderError("Kịch bản một cảnh vượt giới hạn 16.000 ký tự.")
            critic = validate_critic(ask_ai(
            'Bạn là kiểm định viên fidelity, độc lập với lần viết. Đối chiếu toàn bộ kịch bản với nguồn. '
            'Chỉ trả {"critical_errors": [], "major_errors": [], "unsupported_claims": [], '
            '"broken_setup_payoff": [], "minor_errors": []}. Mỗi lỗi là một chuỗi mô tả cụ thể. '
            'Kiểm tra nhân quả, setup/payoff, người nói, nhân vật, chi tiết mới, kết cục và phần bắt buộc bị mất. '
            'Dẫn source_ids không tự chứng minh rằng nội dung đúng. Đánh dấu mọi chi tiết không có căn cứ.',
                {**payload, "segments": local_segments}))
            for key in keys:
                fidelity[key].extend(f"{scene_id}: {issue}" for issue in critic[key])
        else:
            local_segments = [{"speaker": "NARRATOR", "text": sentence["text"], "emotion": "neutral",
                               "pause_after_ms": 350, "source_ids": [sentence["id"]]}
                              for sentence in source if sentence["id"] in selected]
        for segment in local_segments:
            segment["scene_id"] = scene_id
            segment["chapter_id"] = source[0].get("chapter_id", "C0001")
        segments.extend(local_segments)
        previous_context = " ".join(segment["text"] for segment in local_segments)[-CONTEXT_LIMIT:]
    validate_script({"segments": segments}, plan, sentences)
    summary = "\n\n".join(segment["text"] for segment in segments)
    count = units(summary)
    estimate = round(count / RATE * 60 + sum(segment["pause_after_ms"] for segment in segments) / 1000, 1)
    blocked = any(fidelity.get(key) for key in ("critical_errors", "major_errors", "unsupported_claims", "broken_setup_payoff"))
    return {"profile": profile, "label": PROFILE_NAMES[profile], "summary": summary,
            "segments": segments, "plan": plan, "fidelity": fidelity, "units": count,
            "retained_ratio": round(count / max(1, config.get("source_units", units(config["text"]))), 3),
            "estimated_duration_seconds": estimate, "actual_duration_seconds": None,
            "audio_blocked": blocked,
            "rights_confirmed": config.get("rights_confirmed", False), "rights_note": config.get("rights_note", ""),
            "warnings": (["Nội dung bắt buộc vượt ngân sách; cần tăng thời lượng hoặc biên tập lại."]
                         if plan["required_over_budget"] else [])}


def revise_for_duration(result, profile, actual_seconds):
    """Tạo ứng viên đã kiểm định từ số đo; bên gọi quyết định chấp nhận và lưu."""
    target = result.get("target_duration")
    variant = next((item for item in result.get("variants", []) if item["profile"] == profile), None)
    if result.get("mode") != "ai" or not target or variant is None:
        return None
    if type(actual_seconds) not in (int, float) or not math.isfinite(actual_seconds) or actual_seconds <= 0:
        raise InputError("Số đo thời lượng audio phải hữu hạn và lớn hơn 0.")
    if abs(actual_seconds - target * 60) / (target * 60) <= 0.05:
        return None
    budget = max(1, min(result["source_units"], round(variant["units"] * target * 60 / actual_seconds)))
    config = {"mode": "ai", "target_duration": target,
              "text": " ".join(sentence["text"] for sentence in result["sentences"]),
              "source_units": result["source_units"],
              "rights_confirmed": variant.get("rights_confirmed", result.get("rights_confirmed", False)),
              "rights_note": variant.get("rights_note", result.get("rights_note", ""))}
    feedback = {"actual_seconds": actual_seconds, "target_seconds": target * 60,
                "previous_units": variant["units"], "new_budget_units": budget,
                "action": "shorten" if actual_seconds > target * 60 else "restore_source_detail"}
    candidate = create_variant(config, result["sentences"], result["graph"], profile, budget, feedback)
    candidate["duration_revision"] = feedback
    candidate["warnings"] = list(dict.fromkeys(variant.get("warnings", []) + candidate["warnings"]))
    return candidate


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def run_pipeline(config, directory, progress=lambda message: None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "source.json", {key: value for key, value in config.items() if key not in ("text", "raw_text")})
    (directory / "raw.txt").write_text(config.get("raw_text", config["text"]), encoding="utf-8")
    (directory / "clean.txt").write_text(config["text"], encoding="utf-8")
    progress("Đang tách văn bản và gắn vị trí nguồn")
    sentences = source_sentences(config["text"], SCENE_LIMIT)
    structure = story_structure(config["text"], sentences)
    if not sentences:
        raise InputError("Không tìm thấy câu trong văn bản nguồn.")
    write_json(directory / "sentences.json", sentences)
    write_json(directory / "structure.json", structure)
    progress("Đang phân tích truyện bằng AI" if config["mode"] == "ai" else "Đang tạo bản trích câu thử nghiệm")
    graph = analyze_story(sentences, config["mode"], progress)
    write_json(directory / "story_graph.json", graph)
    profiles = [config["profile"]] if config["target_duration"] else list(PROFILES)
    result = {"title": config["title"], "author": config["author"], "mode": config["mode"],
              "target_duration": config["target_duration"], "source_units": units(config["text"]),
              "pronunciation_lexicon": config.get("pronunciation_lexicon", {}),
              "rights_confirmed": config.get("rights_confirmed", False), "rights_note": config.get("rights_note", ""),
              "structure": structure, "sentences": sentences, "graph": graph, "variants": []}
    for profile in profiles:
        progress(f"Đang biên tập và đối chiếu: {PROFILE_NAMES[profile]}")
        variant = create_variant(config, sentences, graph, profile)
        folder = directory / profile
        folder.mkdir(exist_ok=True)
        write_json(folder / "compression_plan.json", variant["plan"])
        write_json(folder / "script.json", {"segments": variant["segments"]})
        write_json(folder / "fidelity_report.json", variant["fidelity"])
        (folder / "summary.txt").write_text(variant["summary"], encoding="utf-8")
        result["variants"].append(variant)
        write_json(directory / "result.json", result)
    return result
