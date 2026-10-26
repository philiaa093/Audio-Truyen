# Audio Truyện

Công cụ biên tập truyện tiếng Việt thành kịch bản audio ngắn có dẫn chứng nguồn. Hiện là bản thử nghiệm; AI/TTS thật cần cấu hình dịch vụ. Các giới hạn được ghi trong [hướng dẫn sử dụng](docs/usage.md).

## Cấu trúc dự án

```text
Audio-Truyen/
├── frontend/
│   ├── index.html           # Cấu trúc giao diện
│   ├── css/styles.css       # Giao diện và responsive
│   └── js/app.js            # Tương tác, gọi API, nghe audio
├── backend/
│   ├── __main__.py          # Điểm khởi động
│   ├── server.py            # HTTP API, tác vụ, phục vụ frontend
│   ├── config.py            # Đường dẫn dự án và dữ liệu
│   ├── errors.py            # Lỗi đầu vào và lỗi dịch vụ
│   ├── providers.py         # Kết nối HTTP/AI dùng chung
│   └── services/
│       ├── pipeline.py      # Nhập, lập kế hoạch, biên tập, kiểm định
│       ├── audio.py         # TTS, xuất audio và chỉnh thời lượng đo
│       ├── pronunciation.py # Chuẩn hóa lời đọc và từ điển phát âm
│       └── qa.py            # ASR, tín hiệu audio và nghe duyệt
├── tests/
│   └── check.py             # Kiểm tra pipeline, API và audio
├── docs/
│   ├── architecture.md      # Thành phần và quy trình xử lý
│   ├── usage.md             # Sử dụng, cấu hình AI/TTS và giới hạn
│   └── deep-research-report.md
├── storage/                 # Dữ liệu chạy, không đưa vào Git
│   ├── projects/            # Truyện, kịch bản, audio theo mã dự án
│   ├── logs/                # Log khi chạy nền
│   └── tests/               # Thư mục tạm của kiểm tra tự động
├── AGENTS.md
└── README.md
```

## Chạy ứng dụng

Cần Python 3.12 trở lên. Không cần cài thư viện Python; xuất audio cần FFmpeg trong `PATH`.

Chạy từ thư mục gốc `E:\Audio-Truyen`:

```powershell
python -m backend
```

Mở [bàn biên tập](http://127.0.0.1:8765). Dừng bằng `Ctrl+C`; đổi cổng bằng `python -m backend --port 8766`. Frontend được backend phục vụ cùng địa chỉ, không cần bước build riêng.

## Kiểm tra

```powershell
python -m tests.check
python -m compileall -q backend tests
node --check frontend/js/app.js
```

Node chỉ cần cho kiểm tra cú pháp JavaScript, không cần để chạy ứng dụng. Kiểm tra AI/TTS/ASR dùng máy chủ giả tại máy, không gọi dịch vụ trả phí.

## Các phần đã bổ sung

- Truyện tối đa 1.000.000 ký tự; chia chương/cảnh và phần xử lý khoảng 8.000 ký tự, giữ vị trí nguồn.
- Chuẩn hóa số, ngày giờ, tiền, phần trăm và từ điển phát âm riêng, giữ nguyên văn bản biên tập.
- AI tự viết lại theo thời lượng WAV đã đo tối đa hai vòng, kiểm định lại mỗi lần.
- ASR qua gateway JSON, CER/WER, kiểm tra tín hiệu và checklist nghe duyệt gắn với hash của audio.

Các ngưỡng là đề xuất pilot. Thiếu ASR hoặc ngưỡng đã hiệu chỉnh thì QA ghi chưa đầy đủ. Xem [quy trình QA](docs/qa.md) và [cấu hình dịch vụ](docs/usage.md).

Xem [kiến trúc và quy trình](docs/architecture.md), [cấu hình AI/TTS](docs/usage.md) và [báo cáo nghiên cứu](docs/deep-research-report.md).
