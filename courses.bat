@echo off
rem Nhập/cập nhật môn học từ CSV — gõ:  .\courses
rem   Mặc định đọc scripts\courses-example.csv; truyền file khác:  .\courses duong\dan.csv
rem   Upsert: chạy lại bao nhiêu lần cũng được, mã cũ thì cập nhật.
setlocal
set CSV=%~1
if "%CSV%"=="" set CSV=scripts\courses-example.csv
.venv\Scripts\python -m ingest.cli import-courses "%CSV%"
