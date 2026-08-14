"""Cấu hình ứng dụng, đọc từ .env (hoặc EnvironmentFile của systemd)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- CSDL ---
    db_dsn: str = "mysql+pymysql://docbot@127.0.0.1:3306/edu_docbot?charset=utf8mb4"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- Tệp ---
    files_root: Path = Path("./files")
    xaccel_prefix: str = "/protected-files"
    max_upload_bytes: int = 100 * 1024 * 1024  # khớp client_max_body_size của nginx
    # Dev không có nginx: bật để FastAPI tự trả file download. Đặt trong .env
    # một lần, khỏi gõ biến môi trường trước mỗi lệnh chạy. Sản xuất: bỏ trống
    # → đường X-Accel-Redirect cho nginx.
    dev_serve_files: bool = Field(False, validation_alias="DEV_SERVE_FILES")

    # --- Vertex AI ---
    # Tắt USE_LLM_ROUTER thì chỉ còn router rule-based: hệ thống vẫn chạy đủ
    # nhánh metadata, không tốn một token nào. Đây cũng là trạng thái hạ cấp khi
    # chạm trần ngân sách (KT-10).
    use_llm_router: bool = True
    # Hai cách xác thực, ưu tiên theo thứ tự:
    # 1. gemini_api_key — Gemini Developer API (lấy ở aistudio.google.com), nhanh nhất.
    # 2. google_cloud_project — Vertex AI, xác thực bằng ADC/service account (production).
    gemini_api_key: str = ""
    google_cloud_project: str = ""
    # Tên biến là LLM_LOCATION có chủ đích: gcloud/SDK hay đặt sẵn
    # GOOGLE_CLOUD_LOCATION ở cấp OS, mà biến OS thắng file .env — dùng chung
    # tên là bị đè cấu hình lúc nào không biết (đã dính 13/08).
    google_cloud_location: str = Field("us-central1", validation_alias="LLM_LOCATION")
    router_model: str = "gemini-2.5-flash-lite"
    llm_timeout_seconds: float = 8.0
    # Router có trần riêng, ngắn hơn: tầng luật là fallback tử tế nên thà bỏ
    # cuộc sớm còn hơn bắt người dùng chờ — quota nghẽn chỉ tốn tối đa 5s.
    router_timeout_seconds: float = 5.0

    # --- Giai đoạn 2: RAG ---
    # Đổi embed_model/embed_dim = re-embed toàn bộ. Quyết một lần (KT-08).
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768
    # Embedding có location RIÊNG: gemini-embedding-001 không phục vụ ở
    # 'global' (đo 13/08: treo tới timeout), trong khi 3.5-flash-lite chỉ có ở
    # 'global'. Hai model hai nơi — hai client.
    embed_location: str = Field("us-central1", validation_alias="EMBED_LOCATION")
    gen_model: str = "gemini-2.5-flash-lite"  # sinh trả lời; escalate là việc GĐ3
    # Model dự phòng khi gen_model nghẽn quota (429): mỗi base model một hàng
    # đợi quota riêng nên chuyển làn là thoát. Trống = tắt.
    gen_fallback_model: str = Field(
        "gemini-2.5-flash-lite", validation_alias="GEN_FALLBACK_MODEL"
    )
    # KT-05 nguyên gốc là 400 — nới lên theo yêu cầu chủ dự án (13/08): câu hỏi
    # tổng quan được trả lời đầy đủ hơn. Chi phí trần mới ~0,0003 USD/câu output.
    gen_max_output_tokens: int = 800
    gen_temperature: float = 0.35             # 0.2 quá khô — nhích nhẹ cho tự nhiên
    rag_topk_per_channel: int = 20            # mỗi kênh (vector, FULLTEXT) lấy 20
    rerank_enabled: bool = False              # bge-reranker-v2-m3 local; cần requirements-rerank.txt
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # Tầng 5 gán metadata khi nạp: Flash-Lite phân loại file mà 4 tầng máy móc
    # bó tay. Offline, ~100 token/file — tắt bằng INGEST_LLM_META=0.
    ingest_llm_meta: bool = True

    # --- Chi phí & quota (KT-10) ---
    quota_daily_tokens: int = 20_000
    daily_budget_usd: float = 1.0
    price_in_per_mtok: float = 0.10
    price_out_per_mtok: float = 0.40
    price_embed_per_mtok: float = 0.15

    # --- Cache (KT-02 tầng hash) ---
    cache_ttl_seconds: int = 86_400

    # --- Truy hồi ---
    metadata_limit: int = 10
    # Số chunk đưa vào prompt RAG. Thiết kế gốc 4; nâng 6 để câu trả lời tổng
    # quan đủ chất liệu (đi cùng trần output 800).
    content_limit: int = 6

    # --- Khác ---
    log_level: str = "INFO"
    cors_origins: str = ""
    # Đặt giá trị bất kỳ → mọi endpoint /admin đòi thêm header X-Admin-Token
    # khớp. Trống = chế độ demo (chỉ kiểm role qua X-User-Id).
    admin_token: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def cost_usd(
        self, tokens_in: int, tokens_out: int, tokens_embed: int = 0
    ) -> float:
        """Quy đổi token thành USD theo bảng giá đang cấu hình."""
        return (
            tokens_in / 1_000_000 * self.price_in_per_mtok
            + tokens_out / 1_000_000 * self.price_out_per_mtok
            + tokens_embed / 1_000_000 * self.price_embed_per_mtok
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
