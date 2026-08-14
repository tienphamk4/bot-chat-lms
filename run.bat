@echo off
rem Chạy server dev EDU-DOCBOT — gõ:  .\run
rem (DEV_SERVE_FILES đã nằm trong .env, không cần đặt biến trước lệnh nữa.
rem  Thêm tham số nếu muốn, ví dụ: .\run --port 9000)
.venv\Scripts\python -m uvicorn app.main:app --port 8080 --reload %*
