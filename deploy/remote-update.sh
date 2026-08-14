#!/usr/bin/env bash
#
# Cập nhật EDU-DOCBOT trên VM. Chạy bằng root, do .github/workflows/deploy.yml gọi:
#
#   sudo bash /tmp/edu-docbot-deploy/deploy/remote-update.sh
#
# Tiền đề: workflow đã chép gói mã nguồn lên /tmp/edu-docbot.tar.gz và giải nén
# ra $STAGE. Script này đồng bộ vào $APP_ROOT, cài lại thư viện NẾU
# requirements.txt đổi, khởi động lại service rồi tự kiểm chứng /readyz.
#
# ─── BA RANH GIỚI KHÔNG ĐƯỢC PHÁ (đọc kỹ trước khi sửa file này) ───────────
#  1. Không đụng $APP_ROOT/.env — chứa khóa API và mật khẩu CSDL, không có
#     trong repo. Ghi đè là mất sạch, phải gõ lại bằng tay.
#  2. Không rsync --delete vào thẳng $APP_ROOT — thư mục files/ (toàn bộ tài
#     liệu đã nạp) nằm ngay đó và cũng không có trong repo. Chỉ đồng bộ từng
#     thư mục mã nguồn liệt kê trong SYNC_DIRS.
#  3. Không chép đè cloud-sql-proxy.service (bản repo còn placeholder) và
#     cấu hình nginx (certbot có thể đã sửa bản trên máy). Sửa tay khi cần.

set -euo pipefail

APP_ROOT=${APP_ROOT:-/srv/edu-docbot}
STAGE=${STAGE:-/tmp/edu-docbot-deploy}
TARBALL=${TARBALL:-/tmp/edu-docbot.tar.gz}
SERVICE=edu-docbot
SYNC_DIRS=(app ingest eval migrations scripts web deploy)

log() { printf '\n==> %s\n' "$*"; }

# ─── 0. Kiểm tra tiền đề ───────────────────────────────────────────────────
[[ -d "$STAGE/app" ]] || {
  echo "LỖI: không thấy mã nguồn ở $STAGE — bước giải nén hỏng?" >&2; exit 1; }
[[ -x "$APP_ROOT/venv/bin/python" ]] || {
  echo "LỖI: chưa có $APP_ROOT/venv — phải chạy cài đặt lần đầu (Phần A của" \
       "docs/trien-khai-gcp.md) trước khi deploy tự động." >&2; exit 1; }
[[ -f "$APP_ROOT/.env" ]] || {
  echo "LỖI: thiếu $APP_ROOT/.env." >&2; exit 1; }

# ─── 1. Đồng bộ mã nguồn ───────────────────────────────────────────────────
log "Đồng bộ mã nguồn"
OLD_REQ=$(sha256sum "$APP_ROOT/requirements.txt" 2>/dev/null | cut -d' ' -f1 || true)
NEW_REQ=$(sha256sum "$STAGE/requirements.txt" | cut -d' ' -f1)

for d in "${SYNC_DIRS[@]}"; do
  [[ -d "$STAGE/$d" ]] || continue
  rsync -a --delete --exclude='__pycache__/' "$STAGE/$d/" "$APP_ROOT/$d/"
  echo "  $d"
done

install -m 644 "$STAGE/requirements.txt" "$APP_ROOT/requirements.txt"
if [[ -f "$STAGE/requirements-rerank.txt" ]]; then
  install -m 644 "$STAGE/requirements-rerank.txt" "$APP_ROOT/requirements-rerank.txt"
fi

chown -R docbot:docbot "${SYNC_DIRS[@]/#/$APP_ROOT/}" "$APP_ROOT/requirements.txt"

# ─── 2. Thư viện Python — chỉ cài lại khi requirements.txt đổi ──────────────
if [[ "$OLD_REQ" != "$NEW_REQ" ]]; then
  log "requirements.txt đã đổi → cài lại thư viện (chậm, vài phút)"
  "$APP_ROOT/venv/bin/pip" install --no-input --upgrade -r "$APP_ROOT/requirements.txt"
  chown -R docbot:docbot "$APP_ROOT/venv"
else
  echo "  requirements.txt không đổi → bỏ qua pip"
fi

# ─── 3. Unit systemd của ứng dụng ──────────────────────────────────────────
# Chỉ ba file này. cloud-sql-proxy.service KHÔNG có trong danh sách (ranh giới 3).
units_changed=0
for u in edu-docbot.service edu-docbot-ingest.service edu-docbot-ingest.timer; do
  src="$APP_ROOT/deploy/$u"
  [[ -f "$src" ]] || continue
  if ! cmp -s "$src" "/etc/systemd/system/$u"; then
    install -m 644 "$src" "/etc/systemd/system/$u"
    units_changed=1
    echo "  cập nhật $u"
  fi
done
if [[ "$units_changed" == 1 ]]; then
  log "Unit systemd đổi → daemon-reload"
  systemctl daemon-reload
fi

# nginx: chỉ báo khác biệt, không tự chép đè.
if [[ -f /etc/nginx/sites-available/edu-docbot ]] &&
   ! cmp -s "$APP_ROOT/deploy/nginx-edu-docbot.conf" /etc/nginx/sites-available/edu-docbot; then
  echo "  LƯU Ý: nginx-edu-docbot.conf trong repo khác bản đang chạy." \
       "Nếu có chủ đích thì cập nhật tay rồi 'nginx -t && systemctl reload nginx'."
fi

# ─── 4. Khởi động lại ──────────────────────────────────────────────────────
log "Khởi động lại $SERVICE"
systemctl restart "$SERVICE"

# ─── 5. Nghiệm thu — /readyz phải trả 200 ──────────────────────────────────
# /readyz trả 503 khi CSDL không nối được, nên `curl -f` bắt được cả hai kiểu
# hỏng: tiến trình chết hẳn và tiến trình sống nhưng mất CSDL.
log "Kiểm chứng /readyz"
for _ in $(seq 1 20); do
  if out=$(curl -fsS --max-time 3 http://127.0.0.1:8080/readyz 2>/dev/null); then
    echo "  $out"
    rm -rf "$STAGE" "$TARBALL"
    log "Deploy xong."
    exit 0
  fi
  sleep 1
done

echo "LỖI: /readyz không trả 200 sau 20 giây. Log gần nhất:" >&2
journalctl -u "$SERVICE" -n 40 --no-pager >&2
exit 1
