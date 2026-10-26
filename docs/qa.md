# Quy trình QA truyện audio

QA kiểm tra bản cuối sau vòng chỉnh thời lượng, lưu trong `qa_report.json` của profile và `result.json`. Các con số là target pilot hoặc baseline nội bộ, không phải kết quả đo chất lượng nhà cung cấp. [Báo cáo nghiên cứu](deep-research-report.md) vẫn là đề xuất thiết kế; chỉ các bước mô tả dưới đây đã triển khai.

## Kiểm tra tự động

| Nhóm | Thực hiện | Điều kiện |
|---|---|---|
| Quyền sử dụng | Đọc metadata nguồn | Có xác nhận và ghi căn cứ; người duyệt xác minh phạm vi thực tế |
| Fidelity | Đối chiếu từng phần trong lần gọi AI riêng | Không critical/major/unsupported claim/broken setup-payoff; preview chưa đủ dữ liệu |
| Thời lượng | Số frame WAV master | Nếu có mục tiêu: lệch không quá ±5% |
| Chuẩn hóa phát âm | Từ điển và quy tắc tiếng Việt | Không còn ký hiệu mơ hồ/viết tắt chưa xử lý; vẫn phải nghe giọng thực tế |
| PCM | Đọc master theo khối | Không mẫu chạm biên clipping; tỷ lệ im lặng ≤98%; đoạn im lặng liên tục ≤5 giây, ngưỡng −50 dBFS |
| Loudness/true peak | FFmpeg `ebur128` đo lại master sau xuất | −18 ±1 LUFS; true peak ≤−1 dBTP với dung sai số đo 0,1 dB |
| ASR | Chép từng chunk WAV, so với `tts_text` | CER/WER toàn bản không vượt cả hai ngưỡng đã cấu hình; mọi chunk phải kiểm tra được |

CER tính lỗi ký tự sau bỏ khoảng trắng; WER dùng đơn vị cách trắng. Chuẩn hóa NFC, chữ hoa/thường và dấu câu trước so sánh. Không hardcode ngưỡng ASR “chuẩn chung”: dùng bộ mẫu có transcript đúng và giọng/accent dự kiến để chọn `QA_MAX_CER`, `QA_MAX_WER`. Giá trị hợp lệ 0–1. Thiếu một ngưỡng là chưa đầy đủ, không mặc định đạt dù transcript trùng.

ASR đối chiếu chunk trước ghép; PCM và loudness kiểm tra master cuối. ASR không chứng minh phát âm đúng hoặc fidelity. Nội dung gắn nhãn nhạy cảm do người duyệt rà soát; chưa có mô hình moderation riêng. Fidelity chỉ có ngữ cảnh trước giới hạn 1.600 ký tự, không bảo đảm phát hiện liên hệ setup/payoff xa nhau.

## Trạng thái và quyết định

| Trạng thái | Ý nghĩa |
|---|---|
| `blocked` | Có kiểm tra không đạt |
| `incomplete` | Chưa đủ dữ liệu, dịch vụ chưa cấu hình hoặc bước kiểm tra lỗi |
| `ready_for_review` | Kiểm tra tự động đạt; chờ người nghe |
| `approved` | Người nghe xác nhận đủ sáu nhóm; `release_ready: true` |
| `rejected` | Người nghe yêu cầu sửa, có ghi chú; `release_ready: false` |

Thiếu ASR/lỗi dịch vụ không làm mất audio đã tạo; báo cáo giữ lý do chưa kiểm tra được. Muốn chạy lại sau khi thay cấu hình, khởi động lại máy chủ và tạo audio lại; cache chunk/transcript hợp lệ được dùng lại. Nếu lệch thời lượng sau hai vòng, đổi mục tiêu hoặc biên tập nguồn rồi tạo bản mới. Không tự tăng tốc lời đọc.

## Sáu nhóm nghe duyệt

1. **Fidelity:** giữ biến cố bắt buộc, cao trào/kết cục, nhân vật/người nói, động cơ, nhân quả và setup/payoff. Đối chiếu trực tiếp nguồn, nhất là các liên hệ xa cảnh.
2. **Phát âm:** tên nhân vật/chính danh, địa danh, từ ngoại lai, số, ngày giờ, tiền và viết tắt. Kiểm tra cả `tts_text` và âm thanh.
3. **Nhịp kể:** thời lượng thực, tốc độ dễ nghe, chuyển cảnh và khoảng nghỉ; không giãn silence để ép mục tiêu.
4. **Chất lượng âm thanh:** nghe trọn bản cuối, không mất tiếng, nhảy đoạn, méo/clipping, âm lượng bất thường hay tạp âm đáng kể.
5. **Phong cách/cảm xúc:** POV, hình ảnh đặc trưng, thoại quan trọng, độ tự nhiên và cảm xúc/chuyển cảm xúc hợp nguồn.
6. **Nhãn nội dung:** rà cảnh nhạy cảm, ghi nhãn và lưu ý cần thiết trong ghi chú; xác nhận phù hợp phạm vi sử dụng.

Lưu tên người duyệt, thời điểm UTC, ghi chú, sáu cờ và SHA256 của WAV. Quyết định áp dụng đúng artifact đang nghe; yêu cầu có hash cũ bị từ chối. Tạo lại audio đặt lại bước nghe duyệt. Chỉ lưu quyết định đạt khi mọi kiểm tra tự động và sáu mục đều đạt. Không có thao tác tự publish.

API: `POST /api/jobs/<id>/review`, cùng token giao diện và JSON gồm `profile`, `audio_sha256`, `reviewer`, `decision` (`approved`/`rejected`), `notes`, `checklist` với sáu khóa `story_fidelity`, `pronunciation`, `pacing`, `audio_quality`, `style_emotion`, `content_labels`. Từ chối cần ghi lý do. Thao tác khóa công việc để tránh duyệt đồng thời với tạo audio.

## Kiểm chứng

`python -m tests.check` chạy check phân cảnh, từ điển, chỉnh thời lượng, ASR/cache/lỗi, PCM, các gate và API nghe duyệt bằng provider giả. Fixture không chứng minh giọng thật đạt chất lượng. Pilot cần truyện và giọng có quyền sử dụng, transcript chuẩn, đánh giá nghe của người Việt; dùng checklist nghiên cứu để đo thêm tỷ lệ giữ plot, lỗi phát âm và điểm phong cách/độ tự nhiên.
