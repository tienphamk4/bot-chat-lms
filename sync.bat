@echo off
rem Dựng lại cột dẫn xuất của courses + nạp lại chỉ mục tên môn — gõ:  .\sync
rem Chạy sau khi sửa bảng courses bằng SQL tay.
.venv\Scripts\python -m ingest.cli resync-derived
