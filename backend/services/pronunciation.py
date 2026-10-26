"""Chuẩn hóa lời đọc tiếng Việt; luôn giữ nguyên văn bản biên tập."""

import copy
import datetime
import re
import unicodedata

from backend.errors import InputError


DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")


def validate_lexicon(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 200:
        raise InputError("Từ điển phát âm phải là đối tượng, tối đa 200 mục.")
    result = {}
    for key, spoken in value.items():
        if (not isinstance(key, str) or not isinstance(spoken, str)
                or not 1 <= len(key.strip()) <= 80 or not 1 <= len(spoken.strip()) <= 160
                or any(ord(char) < 32 for char in key + spoken)):
            raise InputError("Mục phát âm cần tên 1–80 ký tự và lời đọc 1–160 ký tự, không chứa ký tự điều khiển.")
        key = unicodedata.normalize("NFC", key.strip())
        if key.casefold() in {item.casefold() for item in result}:
            raise InputError("Từ điển phát âm có tên trùng sau chuẩn hóa.")
        result[key] = unicodedata.normalize("NFC", spoken.strip())
    return result


def _triple(number, full=False):
    hundred, rest = divmod(number, 100)
    ten, unit = divmod(rest, 10)
    words = []
    if hundred or full:
        words.extend((DIGITS[hundred], "trăm"))
    if ten >= 2:
        words.extend((DIGITS[ten], "mươi"))
    elif ten == 1:
        words.append("mười")
    elif unit and words:
        words.append("lẻ")
    if unit:
        words.append("mốt" if unit == 1 and ten >= 2 else "lăm" if unit == 5 and ten else DIGITS[unit])
    return " ".join(words)


def integer_words(value):
    value = str(value)
    if not re.fullmatch(r"-?\d{1,18}", value):
        raise ValueError("Số vượt phạm vi 18 chữ số.")
    if value.startswith("-"):
        return "âm " + integer_words(value[1:])
    if len(value) > 1 and value.startswith("0"):
        return " ".join(DIGITS[int(char)] for char in value)
    number = int(value)
    if not number:
        return DIGITS[0]
    groups = []
    while number:
        number, group = divmod(number, 1000)
        groups.append(group)
    scales = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ")
    words = []
    for index in range(len(groups) - 1, -1, -1):
        if groups[index]:
            words.append(_triple(groups[index], bool(words) and groups[index] < 100))
            if scales[index]:
                words.append(scales[index])
    return " ".join(words)


def number_words(value):
    sign = "âm " if value.startswith("-") else ""
    value = value.lstrip("-")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", value):
        value = value.replace(".", "")
    elif "." in value and "," in value:
        raise ValueError("Cách phân tách số chưa được hỗ trợ.")
    elif value.count(".") == 1:
        value = value.replace(".", ",")
    if not re.fullmatch(r"\d{1,18}(?:,\d{1,18})?", value):
        raise ValueError("Số không hợp lệ hoặc vượt 18 chữ số mỗi phần.")
    whole, _, decimal = value.partition(",")
    return sign + integer_words(whole) + (" phẩy " + " ".join(DIGITS[int(c)] for c in decimal) if decimal else "")


def normalize_text(text, lexicon=None):
    text = unicodedata.normalize("NFC", text)
    protected = []
    unresolved = []
    marker = "\ue000"
    while marker in text:
        marker += "\ue000"

    def protect(value):
        # Private-use markers prevent later number rules from changing dictionary replacements.
        protected.append(value)
        return marker + chr(0xE100 + len(protected) - 1) + "\ue001"

    lexicon = validate_lexicon(lexicon)
    if lexicon:
        lookup = {key.casefold(): value for key, value in lexicon.items()}
        alternatives = [re.escape(key) if key.isupper() else "(?i:" + re.escape(key) + ")"
                        for key in sorted(lexicon, key=len, reverse=True)]
        pattern = r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)"
        text = re.sub(pattern, lambda match: protect(lookup[match[0].casefold()]), text)

    def date(match):
        try:
            day, month, year = map(int, match.groups())
            datetime.date(year, month, day)
            return protect(f"ngày {integer_words(day)} tháng {integer_words(month)} năm {integer_words(year)}")
        except ValueError:
            unresolved.append({"token": match[0], "reason": "Ngày không hợp lệ; cần biên tập viên xác nhận."})
            return protect(match[0])

    text = re.sub(r"(?<!\w)(?:ngày\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\w)", date, text, flags=re.IGNORECASE)

    def clock(match):
        hour, minute = map(int, match.groups()[:2])
        second = int(match[3]) if match[3] is not None else None
        if hour > 23 or minute > 59 or (second is not None and second > 59):
            unresolved.append({"token": match[0], "reason": "Giờ không hợp lệ."})
            return protect(match[0])
        return protect(f"{integer_words(hour)} giờ" + (f" {integer_words(minute)} phút" if minute or second is not None else "")
                       + (f" {integer_words(second)} giây" if second is not None else ""))

    text = re.sub(r"(?<!\w)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\w)", clock, text)

    def ambiguous(match):
        unresolved.append({"token": match[0], "reason": "Ký hiệu số/ngày chưa rõ nghĩa; thêm cách đọc vào từ điển."})
        return protect(match[0])

    text = re.sub(r"(?<!\w)\d+(?:[/-]\d+)+(?!\w)", ambiguous, text)
    currencies = {"vnd": "đồng", "vnđ": "đồng", "đ": "đồng", "₫": "đồng", "usd": "đô la Mỹ", "$": "đô la Mỹ", "eur": "ơ rô", "€": "ơ rô", "%": "phần trăm"}

    def numeric(match):
        token, suffix = match.group(1), match.group(2)
        try:
            spoken = number_words(token)
        except ValueError:
            unresolved.append({"token": match[0], "reason": "Dạng số chưa được hỗ trợ."})
            return protect(match[0])
        return protect(spoken + (" " + currencies[suffix.casefold()] if suffix else ""))

    text = re.sub(r"(?<![\w])(-?\d+(?:[.,]\d+)*)(?:\s*(VND|VNĐ|USD|EUR|đ|₫|\$|€|%)(?!\w))?(?!\w)", numeric, text, flags=re.IGNORECASE)
    for match in re.finditer(r"\b\w*\d\w*\b", text):
        unresolved.append({"token": match[0], "reason": "Mã hoặc đơn vị chứa số chưa có cách đọc trong từ điển."})
    for match in re.finditer(r"\b[A-ZĐ]{2,}\b", text):
        unresolved.append({"token": match[0], "reason": "Viết tắt chưa có trong từ điển phát âm."})
    for index, value in enumerate(protected):
        text = text.replace(marker + chr(0xE100 + index) + "\ue001", value)
    return text, unresolved


def normalize_segments(segments, lexicon=None):
    lexicon = validate_lexicon(lexicon)
    output = copy.deepcopy(segments)
    changes, unresolved = [], []
    for index, segment in enumerate(output):
        spoken, issues = normalize_text(segment["text"], lexicon)
        segment["tts_text"] = spoken
        if spoken != segment["text"]:
            changes.append({"segment_index": index, "original": segment["text"], "spoken": spoken})
        unresolved.extend({"segment_index": index, **issue} for issue in issues)
    return output, {"method": "vietnamese_rules_and_lexicon", "changes": changes, "unresolved": unresolved,
                    "lexicon_entries": len(lexicon), "human_review_required": True,
                    "warning": "Quy tắc phát âm không chứng minh giọng TTS đã đọc đúng; cần nghe tên riêng và từ ngoại lai."}
