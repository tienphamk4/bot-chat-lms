"""Truy cập CSDL — SQLAlchemy Core, không ORM.

Hai luật cứng của dự án được ép ở đây:

1. **Mọi tham số đều ràng buộc.** Không có hàm nào trong module này nhận chuỗi
   SQL đã nối giá trị. Danh sách `IN (...)` sinh placeholder bằng `in_clause()`.
2. **Mọi kết nối chạy ở UTC.** `SET time_zone = '+00:00'` gắn vào sự kiện
   connect, nên `CURRENT_TIMESTAMP` trong CSDL luôn là UTC — đúng quy ước
   DATETIME của dự án.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from app.config import get_settings

_engine: Engine | None = None
_read_engine: Engine | None = None


def _create_engine(autocommit: bool) -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.db_dsn,
        # KHÔNG pre_ping: nó bắn thêm một lượt SELECT 1 mỗi lần lấy kết nối
        # khỏi pool — DB ở xa là mọi truy vấn trả giá gấp đôi.
        pool_pre_ping=False,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Recycle theo TUỔI, nên phải dài: 300s từng làm kết nối bị đập đi
        # xây lại mỗi 5 phút — mỗi lần là một cú bắt tay TLS ~2,5-3s với DB
        # ở xa. 4h vẫn ngắn hơn wait_timeout 8h của MySQL. Kết nối chết
        # bất thường đã có retry-một-lần ở _with_retry lo.
        pool_recycle=14_400,
        # Không ROLLBACK dọn dẹp mỗi lần trả kết nối về pool (thêm 1 lượt mạng):
        # đường ghi luôn commit/rollback tường minh qua begin(), đường đọc chạy
        # autocommit nên không có gì để dọn.
        pool_reset_on_return=None,
        # Đường đọc: autocommit NGAY TẦNG DRIVER — mỗi SELECT là đúng MỘT lượt
        # mạng. Qua begin() như cũ thì mỗi lần đọc = query + COMMIT = gấp đôi.
        connect_args={"autocommit": True} if autocommit else {},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _init_session(dbapi_connection, _record):  # pragma: no cover - I/O
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        finally:
            cursor.close()

    return engine


def get_engine() -> Engine:
    """Engine GHI (transaction đầy đủ), tạo lười."""
    global _engine
    if _engine is None:
        _engine = _create_engine(autocommit=False)
    return _engine


def get_read_engine() -> Engine:
    """Engine ĐỌC (autocommit driver-level) — pool riêng, 1 câu = 1 lượt mạng."""
    global _read_engine
    if _read_engine is None:
        _read_engine = _create_engine(autocommit=True)
    return _read_engine


def dispose_engine() -> None:
    """Đóng pool khi tắt ứng dụng."""
    global _engine, _read_engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    if _read_engine is not None:
        _read_engine.dispose()
        _read_engine = None


@contextmanager
def connection():
    """Kết nối tự đóng, tự commit khi thoát êm."""
    with get_engine().begin() as conn:
        yield conn


def _with_retry(operation):
    """Chạy lại ĐÚNG MỘT LẦN cho hai loại sự cố kết nối an toàn-để-thử-lại:

    1. Kết nối trong pool đã bị server cắt (idle lâu, DB restart) — lộ ra ở
       lần dùng đầu, SQLAlchemy tự loại khỏi pool (`connection_invalidated`).
    2. Lỗi NGAY PHA BẮT TAY khi mở kết nối mới (mạng chập giữa TLS/auth —
       "Packet sequence number wrong", timeout connect...): chưa có câu lệnh
       nào được gửi nên thử lại không bao giờ nhân đôi hiệu ứng.

    Đây là nửa còn lại của quyết định tắt pre_ping.
    """
    from sqlalchemy.exc import DBAPIError, InternalError, OperationalError

    try:
        return operation()
    except (OperationalError, InternalError, DBAPIError) as exc:
        if getattr(exc, "connection_invalidated", False):
            return operation()
        # Lỗi lúc CONNECT: SQLAlchemy gói lỗi của driver nhưng chưa chạy câu
        # lệnh nào — nhận diện qua thông điệp pha bắt tay.
        message = str(exc).lower()
        if any(
            marker in message
            for marker in (
                "packet sequence number wrong",
                "can't connect",
                "connection refused",
                "timed out",
                "lost connection",
                "broken pipe",
            )
        ):
            return operation()
        raise


def warm_pool(connections: int | None = None) -> int:
    """Mở sẵn N kết nối song song cho CẢ hai pool (đọc + ghi) — trả trước phí
    bắt tay TLS lúc boot, người hỏi đầu tiên không phải gánh."""
    import concurrent.futures

    settings = get_settings()
    target = min(connections or 4, settings.db_pool_size)

    def _open_read() -> bool:
        try:
            with get_read_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def _open_write() -> bool:
        try:
            with connection() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # Mở đồng thời để pool giữ NHIỀU kết nối (tuần tự thì một cái được tái dùng).
    jobs = [_open_read] * target + [_open_write] * 2
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(lambda fn: fn(), jobs))
    return sum(results)


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    def _run():
        # Đường đọc autocommit: không begin/commit — một lượt mạng duy nhất.
        with get_read_engine().connect() as conn:
            return _rows(conn, sql, params)

    return _with_retry(_run)


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def fetch_value(sql: str, params: dict[str, Any] | None = None) -> Any:
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: dict[str, Any] | None = None) -> int:
    """Chạy câu ghi, trả về id vừa chèn (0 nếu câu không sinh id)."""

    def _run():
        with connection() as conn:
            result = conn.execute(text(sql), params or {})
            return int(result.lastrowid or 0)

    return _with_retry(_run)


def _rows(conn: Connection, sql: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = conn.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings()]


def in_clause(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """Sinh placeholder cho `IN (...)` — đúng số lượng, không nối chuỗi.

    Dãy rỗng trả về ``NULL``: `x IN (NULL)` không bao giờ đúng, nên mệnh đề
    quyền tự động chặn hết thay vì mở hết. Đây là hành vi cố ý — sai về phía an
    toàn.

    >>> in_clause("doc", [4, 7])
    (':doc_0, :doc_1', {'doc_0': 4, 'doc_1': 7})
    >>> in_clause("doc", [])
    ('NULL', {})
    """
    if not values:
        return "NULL", {}
    keys = [f"{prefix}_{i}" for i in range(len(values))]
    placeholders = ", ".join(f":{key}" for key in keys)
    return placeholders, dict(zip(keys, values))


def where_and(conditions: Iterable[str]) -> str:
    """Ghép các điều kiện đã lọc rỗng thành mệnh đề WHERE."""
    kept = [c for c in conditions if c]
    return " AND ".join(kept) if kept else "1=1"


def ping() -> bool:
    """Dùng cho /readyz."""
    try:
        return fetch_value("SELECT 1") == 1
    except Exception:  # pragma: no cover - phụ thuộc môi trường
        return False
