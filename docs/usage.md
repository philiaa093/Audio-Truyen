# Sử dụng Audio Truyện

Ứng dụng chuyển truyện tiếng Việt thành kịch bản có dẫn chứng và audio. Quy trình dựa trên [báo cáo nghiên cứu](deep-research-report.md); các mặc định kỹ thuật là đề xuất pilot, chưa phải kết quả benchmark.

## Chạy tại máy

Cần Python 3.12 trở lên và FFmpeg trong `PATH` để xuất audio. Không cần thư viện Python ngoài thư viện chuẩn. Chạy từ gốc workspace:

```powershell
python -m backend
```

Mở [bàn biên tập](http://127.0.0.1:8765). Dừng bằng `Ctrl+C`; đổi cổng bằng `python -m backend --port 8766`. Frontend được backend phục vụ, không có bước build.

1. Nhập TXT/Markdown UTF-8 hoặc dán truyện, tối đa 1.000.000 ký tự và tối thiểu 20 đơn vị cách trắng. Dùng tiêu đề `Chương 1`, `Hồi 1`, `Phần 1` hoặc heading Markdown; dùng `***` để ngăn cảnh.
2. Ghi căn cứ sử dụng và xác nhận quyền xử lý/chuyển thể. QA kiểm tra metadata này.
3. Chọn **Thử tại máy** hoặc **Biên tập bằng AI**. `target_duration` dùng đơn vị phút, từ 0,5 đến 180. Để trống (`null`) để tạo ba mức Giữ nhiều / Cân bằng / Rút mạnh.
4. Thêm từ điển phát âm nếu cần: mỗi dòng `tên = cách đọc`. API nhận `pronunciation_lexicon` dạng đối tượng JSON, tối đa 200 mục; tên 1–80 ký tự, lời đọc 1–160 ký tự.
5. Chọn chương/cảnh để đọc, mở **Xem nguồn** đối chiếu. Tải TXT hoặc hồ sơ ZIP để xem artifact trung gian.
6. Duyệt kịch bản rồi bấm **Tạo WAV & MP3**. Với AI và thời lượng mục tiêu, thao tác này cho phép tối đa hai vòng sửa kịch bản và tổng hợp lại. ASR nhận audio nếu đã cấu hình. Các dịch vụ có thể tính phí.
7. Đọc QA, nghe toàn bộ bản cuối và hoàn tất checklist. **Duyệt bản cuối** chỉ khả dụng khi kiểm tra tự động đủ dữ liệu và đạt. **Yêu cầu sửa** lưu người duyệt, ghi chú và hash audio.

Mẫu “Đèn trên bến Cạn” được báo cáo mô tả là văn bản tạo riêng để thử nội bộ. Giọng trình duyệt chỉ nghe thử, không xuất file hoặc đo thời lượng; có thể dùng dịch vụ của hệ điều hành.

## Chia truyện dài

Mỗi câu có ID toàn cục, `chapter_id`, `scene_id` và `source_span` trong `clean.txt`. Chương/cảnh lớn được chia tiếp ở ranh giới câu, mỗi phần nguồn khoảng 8.000 ký tự. Đây là nhận diện theo cấu trúc văn bản, chưa phải bộ nhận diện cảnh văn học đã benchmark; người đọc cần kiểm tra ranh giới.

AI phân tích, viết và kiểm định từng phần; ngữ cảnh nối phần được giới hạn. Không gửi cả truyện dài trong một request. Quan hệ phụ thuộc được giữ khi chọn beat. Ngữ cảnh giới hạn có thể bỏ sót setup/payoff xa nhau; fidelity ghi phạm vi kiểm tra và vẫn cần đối chiếu toàn truyện. Dẫn chứng tồn tại không tự chứng minh diễn đạt đúng nghĩa.

Chế độ thử chỉ trích nguyên câu, chưa hiểu plot hoặc nhân quả; QA fidelity ghi chưa đầy đủ. Các profile khởi tạo giữ khoảng 70% / 48% / 28% nguồn; nội dung bắt buộc vượt ngân sách có cảnh báo. Số đơn vị cách trắng không phải từ NLP hoặc token tính phí.

## Kết nối AI

Đặt biến trong cùng cửa sổ PowerShell trước khi khởi động:

```powershell
$env:LLM_URL = 'http://127.0.0.1:1234/v1/chat/completions'
$env:LLM_MODEL = 'ten-model-dang-chay'
$env:LLM_API_KEY = '<YOUR_API_KEY>' # Bỏ nếu dịch vụ không yêu cầu.
```

Giao thức Chat Completions nhận `model`, `temperature: 0.2`, `messages`; trả JSON trong `choices[0].message.content`. Chấp nhận JSON thuần hoặc khối mã `json`. Từ chối graph có vòng, nguồn giả, nhãn sai kiểu và critic thiếu nhóm lỗi. Truyện được gửi như dữ liệu, không phải chỉ dẫn hệ thống.

Số request tăng theo số phần và profile; mỗi phần cần phân tích, viết và kiểm định riêng. Chỉnh thời lượng gọi lại viết/kiểm định. Không tự retry POST lỗi có thể tính phí. `.env` không tự nạp. Endpoint ngoài loopback phải dùng HTTPS; khóa chỉ nằm trong header, không ghi vào dự án.

## TTS và phát âm

```powershell
ffmpeg -version
$env:TTS_URL = 'http://127.0.0.1:8000/v1/audio/speech'
$env:TTS_MODEL = 'ten-model-tts'
$env:TTS_VOICE = 'ten-giong-tieng-viet'
$env:TTS_API_KEY = '<YOUR_API_KEY>' # Nếu cần.
```

Request JSON:

```json
{
  "model": "ten-model-tts",
  "voice": "ten-giong-tieng-viet",
  "input": "Nội dung thực sự đọc.",
  "response_format": "wav"
}
```

Endpoint trả trực tiếp WAV PCM 1–2 kênh, 8–192 kHz, 8–32 bit, không phải JSON/URL bất đồng bộ. Đây là giao thức được hỗ trợ, không phải adapter cho mọi nhà cung cấp. Hiện dùng một giọng; speaker/emotion giữ trong kịch bản nhưng chưa điều khiển TTS.

`text` giữ bản biên tập, `tts_text` là lời chuẩn hóa. Từ điển ưu tiên cụm dài, khớp ranh giới từ; mục viết toàn chữ hoa khớp đúng hoa/thường để `AI` không thay đại từ `ai`. Quy tắc xử lý số nguyên/âm/thập phân, ngày `dd/mm/yyyy` hoặc `dd-mm-yyyy`, giờ `HH:mm`, tiền và phần trăm. Ngày không hợp lệ, số ngoài phạm vi hoặc viết tắt chưa có từ điển được ghi để sửa/nghe duyệt. Quy tắc không chứng minh giọng đã đọc đúng tên riêng/từ ngoại lai.

Mỗi chunk tối đa 2.000 ký tự, tách theo câu hoặc khoảng trắng nếu câu quá dài. Cache theo endpoint/model/voice/lời đọc. Đổi cấu hình tạo cache mới; đổi giọng bên dịch vụ nhưng giữ ID cần quản lý cache. `tts_receipts.json` ghi thời điểm và ID chương/cảnh.

FFmpeg `loudnorm` dùng pilot `-18 LUFS`, `-1 dBTP`, `LRA 7`; xuất WAV mono 48 kHz/24-bit và MP3 mono 128 kbps.

## Tự chỉnh thời lượng

Hệ thống đo số frame WAV sau mỗi lần tổng hợp. Nếu lệch mục tiêu quá ±5%, chế độ AI điều chỉnh ngân sách theo tỷ lệ đo, viết lại từ nguồn và kiểm định fidelity trước khi tổng hợp tiếp. Tối đa ba lần tổng hợp gồm bản đầu và hai lần sửa. Không tăng tốc giọng hoặc thêm silence để ép đạt.

Dừng khi đạt, kịch bản không đổi, không có sửa an toàn, critic chặn hoặc hết số vòng. Chế độ thử không tự viết lại. `duration_report.json` ghi các lần đo và lý do dừng; không bảo đảm luôn đạt mục tiêu. Lỗi dịch vụ trước khi hoàn tất giữ bộ master/kịch bản/kết quả trước đó; chunk mới có thể tái sử dụng.

## ASR và nghe duyệt

ASR dùng **gateway JSON riêng**; cần adapter nếu dịch vụ thực tế dùng multipart hoặc trả job bất đồng bộ:

```powershell
$env:ASR_URL = 'http://127.0.0.1:8001/transcribe'
$env:ASR_MODEL = 'ten-model-asr-tieng-viet'
$env:ASR_API_KEY = '<YOUR_API_KEY>' # Nếu cần.
# Chỉ đặt sau khi đo và chọn ngưỡng trên bộ mẫu nội bộ:
# $env:QA_MAX_CER = '<nguong-tu-0-den-1>'
# $env:QA_MAX_WER = '<nguong-tu-0-den-1>'
```

Request `{"model":"...","language":"vi","audio_base64":"<WAV_BASE64>"}`, phản hồi `{"text":"bản chép lời"}`. Gửi từng chunk WAV, cache transcript theo audio/endpoint/model và so với lời gửi TTS. CER/WER tính sau chuẩn hóa chữ hoa/thường, Unicode và dấu câu. ASR có thể nhận sai giọng/accent, không thay thế nghe duyệt.

Thiếu ASR, lỗi dịch vụ, thiếu ngưỡng hiệu chỉnh hoặc fidelity chưa kiểm định tạo trạng thái chưa đầy đủ. Lệch thời lượng, transcript vượt ngưỡng hoặc lỗi tín hiệu sẽ chặn duyệt cuối. Xem [checklist QA](qa.md). Tạo lại audio xóa hiệu lực nghe duyệt cũ; chưa có tự phát hành.

## Dữ liệu và kiểm tra

```text
storage/projects/<id>/
  job.json, source.json, raw.txt, clean.txt
  sentences.json, structure.json, story_graph.json, result.json
  balanced/                   # preserve/, compact/ tương tự
    compression_plan.json, summary.txt, script.json, fidelity_report.json
    pronunciation_report.json, tts_receipts.json, tts_chunks/*.wav
    master.wav, delivery.mp3, duration_report.json, qa_report.json
```

`source_span` là `[start, end)` theo ký tự Unicode Python trong `clean.txt` đã chuẩn hóa NFC, không phải byte offset hoặc vị trí file gốc. `raw.txt` giữ đầu vào. ASR cache trong thư mục dữ liệu audio; QA lưu transcript và số đo.

Máy chủ chỉ nghe `127.0.0.1`, kiểm tra Host/Origin/token POST, xử lý một công việc mỗi lần. Công việc gián đoạn chuyển lỗi sau khởi động lại, giữ artifact. Không có khôi phục hàng đợi, tài khoản, database hay publish. ZIP chứa truyện và audio; lưu theo quyền sử dụng, không đưa API key vào truyện hay commit.

```powershell
python -m tests.check
python -m compileall -q backend tests
node --check frontend/js/app.js
```

Các check dùng AI/TTS/ASR giả tại loopback, không gọi dịch vụ trả phí. Sóng sin là fixture kỹ thuật, không phải giọng đọc hoặc bằng chứng chất lượng tiếng Việt. Cần pilot với truyện/giọng có quyền sử dụng để chọn ngưỡng CER/WER và đánh giá fidelity, phát âm, độ tự nhiên.
