# HƯỚNG DẪN NGƯỜI DÙNG — AI Video Localization Studio

Hướng dẫn cho người dùng cuối (không phải lập trình viên): cài đặt, chạy lần đầu,
dịch video có phụ đề, và xử lý lỗi thường gặp.

> Bài viết này mô tả **bản MVP (0.1.0)**. Một số tính năng đang được phát triển —
> phần nào chưa có sẽ được ghi rõ bằng nhãn *(chưa có ở 0.1.0)*.

---

## 1. Cài đặt

### Yêu cầu hệ thống

- **Hệ điều hành:** Windows 10 hoặc Windows 11 (64-bit).
- **Ổ cứng:** cần ~1 GB trống để cài, thêm vài trăm MB — vài GB cho model STT
  (self-host, tải về lần đầu).
- **RAM:** đề xuất từ 4 GB trở lên (càng nhiều càng tốt cho tốc độ transcribe).
- **GPU NVIDIA (tùy chọn):** có card NVIDIA với driver mới nhất sẽ chạy phần
  transcribe bằng CUDA nhanh hơn. Không có GPU vẫn chạy bình thường bằng CPU.
- **Internet:** bắt buộc trong **lần đầu** khi tải model STT. Sau đó có thể
  transcribe hoàn toàn offline.

### Các bước cài

1. Chạy file cài đặt: **`AI Video Localization Studio_0.1.0_x64-setup.exe`**.
2. Chọn thư mục cài đặt (mặc định nằm trong `AppData`/`Program Files`).
3. Chờ trình cài đặt hoàn tất rồi nhấn **Finish**.
4. Tắt màn hình "Windows protected your PC" (SmartScreen) nếu hiện lên — bản
   dùng thử chưa có chữ ký số, nên Windows cảnh báo. Nhấn **More info → Run anyway**.

> Lưu ý bảo mật: bản 0.1.0 chưa ký số (unsigned). Khi SmartScreen cảnh báo, chỉ
> tiếp tục nếu bạn tin file cài đặt lấy từ nguồn chính thống.

### Gỡ cài đặt

- Vào **Settings → Apps → Apps & features** → chọn **AI Video Localization
  Studio** → **Uninstall**; hoặc chạy `uninstall.exe` trong thư mục cài đặt.

---

## 2. Lần chạy đầu tiên

1. Mở **AI Video Localization Studio** từ Start Menu hoặc lối tắt trên desktop.
2. Cửa sổ chính mở ra. Ở lần đầu, ứng dụng sẽ **tự động tải model** STT về máy
   (một lần duy nhất). Để màn hình này đi tới hết; cần có internet.
3. Khi model tải xong, giao diện chính sẵn sàng.

> Nếu cửa sổ tải model diễn ra quá lâu hoặc báo lỗi mạng, xem **Mục 6 — Xử lý
> lỗi** bên dưới.

---

## 3. Cấu hình dịch máy (API key)

Ứng dụng transcribe bằng model chạy local (miễn phí, không upload video). Phần
**dịch** ("translation") có thể dùng provider ngoài:

| Provider | Cần gì? | Ghi chú |
| --- | --- | --- |
| **gemini** | Google AI API key | Được hỗ trợ chính thức ở 0.1.0; tạo key tại Google AI Studio. |
| **openai** | OpenAI API key | Có trong danh sách nhưng mang nhãn *(post-MVP)* — chưa hoàn thiện ở 0.1.0. Cú pháp `api.openai.*` dành cho bản sau. |
| **local** | Không cần gì | Đang phát triển; chưa có trong màn hình cấu hình 0.1.0. |

Cú pháp (dạng thuần — ứng dụng sẽ có màn hình Settings để nhập):

- Trong **Settings** của ứng dụng, chọn provider và dán API key.
- Key được lưu trong **Windows Credential Manager** (không lưu trong file, không
  hiển thị lại đầy đủ sau khi lưu — chỉ hiện dạng che như `AIz****wxyz`).

> Quyền riêng tư: API key chỉ được gửi tới provider bạn chọn cho hành động **dịch**.
> Video/audio KHÔNG được upload — transcribe chạy 100% local.

---

## 4. Luồng công việc chính

Các bước cơ bản để dịch một video:

### Bước 1 — Import video

- Nhấn **Import / Mở video**, chọn file video (`.mp4`, `.mov`, `.mkv`, …).
- Video xuất hiện ở danh sách media; phần phân tích (probe) chạy tự động:
  xác định thời lượng, tỷ lệ khung hình, codec.

### Bước 2 — Transcribe (STT)

- Chọn video rồi nhấn **Transcribe**.
- Ứng dụng trích audio và gõ thành chữ hoàn toàn **trên máy**:
  - Không có GPU / mặc định → CPU (chậm hơn).
  - Có GPU NVIDIA → tự dùng CUDA nếu driver hỗ trợ.
- Kết quả là **transcript** kèm mốc thời gian (timestamp) cho từng đoạn.

### Bước 3 — Dịch

- Chọn provider trong **Settings** (xem Mục 3), nhập API key nếu cần.
- Nhấn **Translate** → mỗi đoạn transcript được dịch sang ngôn ngữ đích
  (mặc định: Tiếng Trung; có thể đổi).
- Kết quả ngay bên cạnh để bạn xem/sửa trước khi xuất.

### Bước 4 — Chỉnh phụ đề (tuỳ chọn)

- Sửa nội dung dịch, điều chỉnh thời gian từng cue nếu cần.
- Ứng dụng tạo đồng thời file định dạng phụ đề (`.srt` / `.ass`).

### Bước 5 — Render & xuất

- Chọn preset chất lượng, nhấn **Render** để ghi video có phụ đề đã dịch.
  - Ưu tiên encode bằng GPU NVIDIA (NVENC) nếu có; tự động rơi về `libx264`
    (encode CPU) khi GPU không encode được — kết quả vẫn đúng.
- Nhấn **Export** để chọn nơi lưu file `.mp4` cuối cùng.

Mỗi bước hiện trạng thái riêng; nếu một bước lỗi, các bước sau sẽ bị chặn đến
khi bạn xử lý xong (bấm **Retry** hoặc sửa lại cấu hình).

---

## 5. Dữ liệu của bạn

- Model STT tải về lần đầu được lưu trong thư mục model của ứng dụng.
- Project/dữ liệu sẽ nằm trong thư mục dữ liệu của ứng dụng trên máy (không
  upload lên server nào).
- Trong bản 0.1.0, dịch chỉ chạy một lần theo transcript hiện có; các tính năng
  như dubbing, tách âm, OCR, voice-cloning nằm **ngoài MVP** và chưa có.

---

## 6. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
| --- | --- | --- |
| **SmartScreen cảnh báo khi cài** | Bản chưa ký số | Chỉ chạy file khi bạn tin nguồn; chọn **Run anyway**. |
| **Lần đầu không tải được model** | Mất internet / firewall | Kiểm tra mạng, bỏ chặn app trong firewall, thử lại. |
| **Đang transcribe thì lỗi "STT failed"** | Thiếu RAM / file audio lạ | Thử video khác; đóng ứng dụng khác; nâng RAM. |
| **Dịch báo lỗi key / 401** | API key sai/thiếu | Vào Settings, cập nhật key provider đang dùng. |
| **Render lỗi codec** | Video lạ, codec không hỗ trợ | Chuyển video sang `.mp4` (H.264) bằng công cụ khác, thử lại. |
| **Ứng dụng khởi động nhưng worker không báo sẵn sàng** | Worker bị chặn bởi antivirus | Cho phép ứng dụng chạy; khởi động lại app. |
| **Cần gỡ hẳn** | — | Chạy `uninstall.exe` (xem Mục 1). |

Nếu lỗi vẫn còn: ghi lại nội dung lỗi + video/audio dùng để tái hiện, gửi cho
người bảo trì dự án. File nhật ký (log) của ứng dụng sẽ được ghi ở một vị trí
ổn định *(đang được bổ sung ở 0.1.0 — chi tiết trong RELEASE.md)*.

---

## 7. Thông tin thêm

- Đây là bản MVP **0.1.0**, chưa phải bản production chính thức.
- Nếu bạn là lập trình viên: xem [`README.md`](../README.md), [`DEVELOPMENT.md`](../DEVELOPMENT.md),
  [`RELEASE.md`](../RELEASE.md), [`api` docs] — tài liệu kỹ thuật tiếng Anh/Việt
  có trong repo dành cho nhóm phát triển.