"""Dependency dùng chung cho các endpoint.

Định danh giai đoạn 1: header `X-User-Id` trỏ vào bảng `users`. Đây là chỗ ĐỘC
NHẤT sẽ thay khi gắn đăng nhập thật (phiên/JWT sau khi đồng bộ users) — các
endpoint chỉ biết tới `Identity`, không biết header.

Header thiếu, sai định dạng, hoặc trỏ vào user không tồn tại/bị khóa đều quy về
ẩn danh — chỉ thấy tài liệu public. Sai về phía an toàn.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.access import ANONYMOUS, Identity, load_identity


def get_identity(
    x_user_id: Annotated[str | None, Header()] = None,
) -> Identity:
    if not x_user_id:
        return ANONYMOUS
    try:
        user_id = int(x_user_id)
    except ValueError:
        return ANONYMOUS
    return load_identity(user_id)


CurrentIdentity = Annotated[Identity, Depends(get_identity)]


def require_admin(
    identity: CurrentIdentity,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> Identity:
    """Chặn các endpoint /admin. 404 thay vì 403 để không lộ sự tồn tại.

    Đặt ADMIN_TOKEN trong .env thì mọi lời gọi /admin phải kèm header
    X-Admin-Token khớp — bịt lỗ 'ai cũng giả được X-User-Id: 4' của chế độ
    demo. Không đặt (mặc định) = hành vi demo như cũ.
    """
    from app.config import get_settings

    if identity.role != "admin":
        raise HTTPException(status_code=404)

    required = get_settings().admin_token
    if required and x_admin_token != required:
        raise HTTPException(status_code=404)
    return identity


AdminIdentity = Annotated[Identity, Depends(require_admin)]
