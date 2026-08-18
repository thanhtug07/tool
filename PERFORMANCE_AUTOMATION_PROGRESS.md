# Performance Optimization - Automation Pipeline

Ngày bắt đầu: 2026-08-15

## Mục tiêu

Tối ưu pipeline Automation để xử lý video dài nhanh hơn mà không giảm chất lượng, không redesign UI, không mở rộng scope MVP, và không thay đổi provider system nếu không cần.

Ưu tiên: Correctness > Stability > Performance > UI.

## Phạm vi task

- Đo thời gian từng stage pipeline thay vì đoán bottleneck.
- Tạo performance report có stage timing, tổng thời gian, segment count và thông tin tài nguyên nếu thu thập được.
- Audit audio/video pipeline để tránh extract/encode lặp lại.
- Tối ưu batching/concurrency/cache/incremental processing trong phạm vi architecture hiện tại.
- Giữ mọi thay đổi có kiểm chứng bằng test/smoke check.

## Checklist Definition of Done

- [ ] Có profiling từng stage.
- [ ] Xác định top 3 bottleneck bằng số đo.
- [ ] Translation batching hoạt động nếu provider hỗ trợ.
- [ ] TTS bounded concurrency hoạt động nếu provider hỗ trợ.
- [ ] Không encode video dư thừa.
- [ ] Audio extraction không lặp lại.
- [ ] Cache hoạt động với key phụ thuộc input/config phù hợp.
- [ ] Incremental processing hoạt động.
- [ ] Memory không tăng bất thường.
- [ ] GPU được dùng khi phù hợp.
- [ ] CPU fallback hoạt động.
- [ ] Existing Automation không regression.
- [ ] Existing tests pass.

## Tiến độ

### 2026-08-15

- [x] Đọc yêu cầu task từ attachment.
- [x] Tạo file tiến độ riêng: `PERFORMANCE_AUTOMATION_PROGRESS.md`.
- [ ] Audit code pipeline hiện tại.
- [ ] Thiết kế instrumentation profiling tối thiểu, không đổi UI.
- [ ] Implement profiling/report.
- [ ] Chạy kiểm tra liên quan.

## Ghi chú kỹ thuật

- Repo đang có nhiều thay đổi sẵn trước task này; chỉ chỉnh trong phạm vi cần thiết.
- Chưa có baseline performance thực tế, nên chưa kết luận speedup hoặc bottleneck.

## Performance Report

Chưa có dữ liệu đo.

| Video length | Total time | STT | Translation | TTS | Audio mix | Subtitle | Encoding | RAM peak | VRAM peak | GPU | Speedup |
| ------------ | ---------- | --- | ----------- | --- | --------- | -------- | -------- | -------- | --------- | --- | ------- |
| TBD          | TBD        | TBD | TBD         | TBD | TBD       | TBD      | TBD      | TBD      | TBD       | TBD | TBD     |
