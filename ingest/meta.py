"""Gán metadata cho tài liệu — năm tầng, không ép quy ước tên file.

Nạp dữ liệu là việc OFFLINE: chậm một chút cũng được, miễn là đúng. Thứ tự:

1. **Tên file** — mã môn (regex) + TÊN/TÊN GỌI TẮT theo danh mục môn + từ khóa loại.
2. **Cấu trúc thư mục** — `files/MAT1093/de_thi/...` hoặc `files/OOP/...`.
3. **manifest.csv** — ghi đè thủ công cho từng file, thắng mọi tầng khác.
4. **Nội dung file** — quét phần đầu (trang bìa giáo trình thường ghi đủ cả
   tên môn lẫn loại). Chỉ điền trường còn thiếu, không ghi đè 3 tầng trên.
5. **LLM Flash-Lite** — đúng phương án dự phòng của thiết kế gốc: chỉ chạy khi
   4 tầng máy móc bó tay; chỉ nhận kết quả tự tin cao VÀ mã môn có thật trong
   danh mục — LLM không được phép bịa ra môn mới.

Vẫn không đoán bừa: qua cả 5 tầng mà thiếu trường → `pending_review`, một file
lạ không làm hỏng cả mẻ. Danh mục môn (bảng `courses`) là nguồn sự thật: mọi
tầng đều đối chiếu về nó.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.vn import vn_norm

MANIFEST_NAME = "manifest.csv"

# Từ khóa trong tên file → loại tài liệu. Khớp trên tên đã vn_norm.
_TYPE_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"tai[ _-]?lieu[ _-]?tham[ _-]?khao|tham[ _-]?khao|reference"), "tai_lieu_tham_khao"),
    (re.compile(r"de[ _-]?cuong|syllabus"), "de_cuong"),
    (re.compile(r"giao[ _-]?trinh|textbook"), "giao_trinh"),
    (re.compile(r"slide|bai[ _-]?giang|lecture"), "slide"),
    (re.compile(r"de[ _-]?thi|de[ _-]?kiem[ _-]?tra|giua[ _-]?ky|cuoi[ _-]?ky|exam|midterm|final"), "de_thi"),
    (re.compile(r"bai[ _-]?tap|assignment|exercise|homework"), "bai_tap"),
    (re.compile(r"quy[ _-]?che|quy[ _-]?dinh|noi[ _-]?quy"), "quy_che"),
]

# Mã môn: chữ dính liền số (MAT1093). KHÔNG cho phép khoảng trắng/gạch giữa
# chữ và số — nới ra là "giữa kỳ 2025" bị đọc thành mã môn "KY2025".
_COURSE_CODE = re.compile(r"\b([A-Za-z]{2,4})(\d{3,4})\b")
_SEMESTER = re.compile(r"\b(20\d{2})(?:hk|k)?([1-3])\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")

# Thư mục loại tài liệu: chấp nhận cả mã ENUM lẫn tên thường gặp.
_DIR_TYPE = {
    "giao_trinh": "giao_trinh", "giao-trinh": "giao_trinh", "giaotrinh": "giao_trinh",
    "slide": "slide", "slides": "slide", "bai_giang": "slide", "bai-giang": "slide",
    "de_cuong": "de_cuong", "de-cuong": "de_cuong",
    "de_thi": "de_thi", "de-thi": "de_thi", "dethi": "de_thi",
    "bai_tap": "bai_tap", "bai-tap": "bai_tap", "baitap": "bai_tap",
    "tai_lieu_tham_khao": "tai_lieu_tham_khao", "tham_khao": "tai_lieu_tham_khao",
    "quy_che": "quy_che", "quy-che": "quy_che", "quyche": "quy_che",
}

VALID_TYPES = set(_DIR_TYPE.values())


@dataclass
class DocMeta:
    title: str = ""
    course_code: str | None = None
    doc_type: str | None = None
    semester: str | None = None
    lecturer: str | None = None
    year: int | None = None
    access: str = "public"
    sources: list[str] = field(default_factory=list)  # tầng nào điền trường nào

    @property
    def needs_review(self) -> bool:
        """Thiếu loại tài liệu là không xếp kệ được. Quy chế không cần môn;
        các loại khác thiếu môn vẫn nạp được nhưng nên soát lại."""
        if self.doc_type is None:
            return True
        if self.doc_type != "quy_che" and self.course_code is None:
            return True
        return False


def resolve(path: Path, files_root: Path) -> DocMeta:
    """Ghép ba tầng: regex tên file → thư mục → manifest. Manifest thắng."""
    meta = _from_filename(path)
    _merge(meta, _from_directories(path, files_root))
    _merge(meta, _from_manifest(path, files_root), override=True)

    if not meta.title:
        meta.title = _title_from_filename(path)
    return meta


def _course_from_catalog(text: str) -> str | None:
    """Khớp mã/tên/tên gọi tắt môn theo DANH MỤC (bảng courses, chỉ mục RAM).

    Dùng chung bộ khớp cụm-dài-nhất với phía chat — 'oop', 'lthdt', 'giải tích
    1' đều nhận được. Trả về mã môn chuẩn (đã chắc chắn tồn tại trong danh mục)
    hoặc None. Lỗi DB → None: tầng tăng cường không được làm hỏng mẻ nạp.
    """
    try:
        from app.core import course_index

        entry = course_index.match(text)
    except Exception:  # pragma: no cover - phụ thuộc môi trường
        return None
    if entry is None or not entry.id:
        return None
    return entry.code


# --- Tầng 1: tên file --------------------------------------------------------


def _from_filename(path: Path) -> DocMeta:
    # Mọi regex chạy trên bản đã thay _- bằng khoảng trắng: '\b' của regex coi
    # '_' là ký tự chữ, nên tìm trên tên gốc kiểu 'x_20261' sẽ trượt hết.
    stem = path.stem.replace("_", " ").replace("-", " ")
    normalized = vn_norm(stem)
    meta = DocMeta()

    code = _COURSE_CODE.search(stem)
    if code:
        meta.course_code = f"{code.group(1)}{code.group(2)}".upper()
        meta.sources.append("filename:course")
    else:
        # Không có mã tường minh → thử tên/tên gọi tắt theo danh mục môn:
        # 'oop_lthdt.pdf', 'giai-tich-1_de-thi.pdf' đều nhận được.
        catalog_code = _course_from_catalog(stem)
        if catalog_code:
            meta.course_code = catalog_code
            meta.sources.append("filename:catalog")

    for pattern, doc_type in _TYPE_KEYWORDS:
        if pattern.search(normalized):
            meta.doc_type = doc_type
            meta.sources.append("filename:type")
            break

    semester = _SEMESTER.search(stem)
    if semester:
        meta.semester = f"{semester.group(1)}{semester.group(2)}"
        meta.year = int(semester.group(1))
        meta.sources.append("filename:semester")
    else:
        year = _YEAR.search(stem)
        if year:
            meta.year = int(year.group(1))

    meta.title = _title_from_filename(path)
    return meta


_HEX_TOKEN = re.compile(r"\b[0-9a-f]{6,}\b", re.IGNORECASE)  # tiền tố upload 'f3b0208d'
_LEADING_INDEX = re.compile(r"^\d{1,3}\s+")                   # '16 lap trinh...'
_TRAILING_CODE = re.compile(r"\s+\d{3,4}$")                   # '... 0193'


def _title_from_filename(path: Path) -> str:
    """Tên file bỏ mã môn/học kỳ/rác upload, đổi gạch thành cách — tiêu đề tạm."""
    spaced = path.stem.replace("_", " ").replace("-", " ")
    spaced = _HEX_TOKEN.sub(" ", spaced)
    spaced = _COURSE_CODE.sub(" ", spaced)
    spaced = _SEMESTER.sub(" ", spaced)
    spaced = " ".join(spaced.split())
    spaced = _LEADING_INDEX.sub("", spaced)
    spaced = _TRAILING_CODE.sub("", spaced)
    return spaced.strip() or path.stem


# --- Tầng 2: cấu trúc thư mục ------------------------------------------------


def _from_directories(path: Path, files_root: Path) -> DocMeta:
    meta = DocMeta()
    try:
        parts = path.relative_to(files_root).parts[:-1]  # bỏ tên file
    except ValueError:
        return meta

    for part in parts:
        code = _COURSE_CODE.fullmatch(part.strip())
        if code and meta.course_code is None:
            meta.course_code = f"{code.group(1)}{code.group(2)}".upper()
            meta.sources.append("dir:course")
            continue
        mapped = _DIR_TYPE.get(vn_norm(part).replace(" ", "_"))
        if mapped and meta.doc_type is None:
            meta.doc_type = mapped
            meta.sources.append("dir:type")
            continue
        # Thư mục đặt theo tên/tên gọi tắt môn: files/OOP/..., files/giai tich 1/...
        if meta.course_code is None:
            catalog_code = _course_from_catalog(part)
            if catalog_code:
                meta.course_code = catalog_code
                meta.sources.append("dir:catalog")

    return meta


# --- Tầng 3: manifest.csv ----------------------------------------------------
# Cột: file_path,title,course_code,doc_type,semester,lecturer,access
# file_path tương đối so với files_root, dùng '/'.


def _from_manifest(path: Path, files_root: Path) -> DocMeta:
    manifest_path = files_root / MANIFEST_NAME
    meta = DocMeta()
    if not manifest_path.is_file():
        return meta

    try:
        relative = str(path.relative_to(files_root)).replace("\\", "/")
    except ValueError:
        return meta

    try:
        with open(manifest_path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("file_path") or "").strip().replace("\\", "/") != relative:
                    continue
                if row.get("title", "").strip():
                    meta.title = row["title"].strip()
                    meta.sources.append("manifest:title")
                if row.get("course_code", "").strip():
                    meta.course_code = row["course_code"].strip().upper()
                doc_type = row.get("doc_type", "").strip()
                if doc_type in VALID_TYPES:
                    meta.doc_type = doc_type
                semester = row.get("semester", "").strip()
                if len(semester) == 5 and semester.isdigit() and semester[4] in "123":
                    meta.semester = semester
                if row.get("lecturer", "").strip():
                    meta.lecturer = row["lecturer"].strip()
                access = row.get("access", "").strip()
                if access in {"public", "faculty", "course", "staff"}:
                    meta.access = access
                meta.sources.append("manifest")
                break
    except (OSError, csv.Error, UnicodeDecodeError):
        pass  # manifest hỏng không được làm hỏng mẻ nạp

    return meta


# --- Tầng 4: nội dung file ---------------------------------------------------
# Trang bìa giáo trình/đề cương thường ghi đủ tên môn lẫn loại tài liệu.

# Cửa sổ hẹp cho MÔN (tên môn ở bìa, tránh khớp nhầm môn được nhắc tới ở giữa
# tài liệu kiểu "xem thêm Giải tích 1"), rộng hơn cho LOẠI.
_CONTENT_COURSE_CHARS = 1500
_CONTENT_TYPE_CHARS = 4000
_TITLE_MAX_CHARS = 200

# Dòng banner cơ quan trên bìa — không bao giờ là tiêu đề tài liệu.
_BANNER_PREFIXES = (
    "cong hoa xa hoi", "doc lap", "uy ban", "truong ", "hoc vien", "khoa ",
    "bo giao duc", "bo lao dong", "ngan hang nha nuoc", "so :", "so:",
    "nghe:", "trinh do:", "luu hanh noi bo", "ha noi", "tp.", "nam 20",
)
# Đầu đề văn bản hành chính — tiêu đề = đầu đề + dòng 'Về việc …' ngay sau.
_ADMIN_HEADS = {
    "quyet dinh", "thong bao", "quy che", "quy dinh", "thong tu",
    "nghi dinh", "cong van", "ke hoach", "huong dan",
}
_SUBJECT_PREFIXES = ("ve viec", "v/v", "ban hanh", "ve ")
# Chức danh ký ban hành đứng ngay dưới phần chủ đề — KHÔNG thuộc tiêu đề.
_SIGNER_PREFIXES = (
    "giam doc", "hieu truong", "pho hieu truong", "chu tich", "truong khoa",
    "truong phong", "bo truong", "thu truong",
)
# Nhãn học liệu — tiêu đề = nhãn + tên môn/mô đun ở dòng kế.
_MATERIAL_HEADS = {
    "giao trinh", "bai giang", "tap bai giang", "de cuong", "de cuong chi tiet",
    "slide bai giang", "bai tap",
}
_MODULE_LABEL = re.compile(r"^(mo\s*dun|mon\s*hoc|hoc\s*phan)\s*[:\-]\s*(.+)$")


def title_from_content(front_lines: list[str]) -> str | None:
    """Đào TIÊU ĐỀ THẬT từ các dòng đầu tài liệu (front matter).

    Bốn mẫu theo thứ tự tin cậy giảm dần:
    A. Văn bản hành chính: 'QUYẾT ĐỊNH' + 'Về việc …' ngay dưới.
    B. Học liệu: 'GIÁO TRÌNH' + 'MÔ ĐUN: LẬP TRÌNH WEB NÂNG CAO' gần dưới.
    C. Markdown H1 ('# …').
    D. Dòng CHỮ HOA có dấu tiếng Việt dài nhất trong 40 dòng đầu (bỏ banner
       cơ quan) — bìa sách in tên sách to, hoa, giữa trang.
    """
    lines = [
        " ".join(l.split())
        for l in front_lines[:40]
        if l.strip() and not re.search(r"\.{4,}", l)  # bỏ dòng mục lục chấm-dẫn
    ]
    normed = [vn_norm(l) for l in lines]

    # A — văn bản hành chính
    for i, norm in enumerate(normed):
        if norm in _ADMIN_HEADS:
            subject_parts: list[str] = []
            for j in range(i + 1, min(i + 4, len(lines))):
                follow = normed[j]
                if not subject_parts and not follow.startswith(_SUBJECT_PREFIXES):
                    break
                if (
                    follow.startswith("can cu")
                    or follow.startswith(_SIGNER_PREFIXES)  # 'GIÁM ĐỐC HỌC VIỆN…'
                    or norm_is_banner(follow)
                ):
                    break
                subject_parts.append(lines[j])
                # dòng chủ đề kết thúc khi đã có dấu chấm/đủ dài
                if lines[j].rstrip().endswith((".", "!", "?")):
                    break
            if subject_parts:
                title = f"{lines[i].title()} {' '.join(subject_parts)}"
                return _clamp_title(title)

    # B — học liệu có nhãn
    for i, norm in enumerate(normed):
        if norm in _MATERIAL_HEADS:
            for j in range(i + 1, min(i + 6, len(lines))):
                module = _MODULE_LABEL.match(normed[j])
                if module:
                    # Lấy phần tên từ dòng GỐC (giữ dấu): cắt sau dấu ':'
                    raw = lines[j].split(":", 1)[-1].split("-", 1)[-1].strip()
                    if len(raw) >= 4:
                        return _clamp_title(f"{lines[i].title()} {raw.title()}")
                if norm_is_banner(normed[j]):
                    continue
                # dòng hoa-có-dấu ngay dưới nhãn cũng là ứng viên tên
                if _caps_titleish(lines[j]):
                    return _clamp_title(f"{lines[i].title()} {lines[j].title()}")
            return _clamp_title(lines[i].title())

    # C — markdown H1
    for line in lines:
        if line.startswith("# "):
            return _clamp_title(line[2:].strip())

    # D — dòng hoa có dấu dài nhất, không phải banner
    candidates = [
        l for l, n in zip(lines, normed)
        if _caps_titleish(l) and not norm_is_banner(n)
    ]
    if candidates:
        return _clamp_title(max(candidates, key=len))
    return None


def norm_is_banner(norm_line: str) -> bool:
    return any(norm_line.startswith(p) for p in _BANNER_PREFIXES)


def _caps_titleish(line: str) -> bool:
    """Dòng kiểu tên-sách-in-hoa: 12-120 ký tự, hoa áp đảo, CÓ dấu tiếng Việt,
    KHÔNG chứa chữ lạ (mojibake TCVN3 'MӢ RӜNG' không được thành tiêu đề)."""
    if not 12 <= len(line) <= 120:
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 8:
        return False
    from ingest.quality import _VN_LETTERS

    if any(ch.lower() not in _VN_LETTERS for ch in letters):
        return False
    upper_ratio = sum(1 for ch in letters if ch == ch.upper()) / len(letters)
    has_diacritic = any(ch.lower() in _TITLE_DIACRITICS for ch in letters)
    return upper_ratio >= 0.8 and has_diacritic


_TITLE_DIACRITICS = set(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
)


def _clamp_title(title: str) -> str:
    title = " ".join(title.split())
    return title[:_TITLE_MAX_CHARS].strip()


def enrich_from_content(meta: DocMeta, extraction) -> None:
    """Điền từ NỘI DUNG: tiêu đề thật (luôn thử — trừ khi manifest đã đặt),
    môn/loại chỉ điền chỗ trống. Không ghi đè tầng 1-3 cho course/type."""
    head_text = " ".join(p.text for p in extraction.pages[:3])
    head = " ".join(head_text.split())

    # Tiêu đề: tên file chỉ là tạm — tiêu đề thật nằm ở bìa. Manifest thắng.
    if "manifest:title" not in meta.sources:
        front = list(extraction.front_matter) or head_text.splitlines()
        content_title = title_from_content(front)
        if content_title:
            meta.title = content_title
            meta.sources.append("content:title")

    if not head:
        return

    if meta.course_code is None:
        # Chỉ nhận qua danh mục (mã/tên/alias đều là cụm trong chỉ mục) —
        # tự nó bảo đảm mã tồn tại, không cần regex thô dễ vớ nhầm 'ISO9001'.
        catalog_code = _course_from_catalog(head[:_CONTENT_COURSE_CHARS])
        if catalog_code:
            meta.course_code = catalog_code
            meta.sources.append("content:catalog")

    if meta.doc_type is None:
        normalized = vn_norm(head[:_CONTENT_TYPE_CHARS])
        for pattern, doc_type in _TYPE_KEYWORDS:
            if pattern.search(normalized):
                meta.doc_type = doc_type
                meta.sources.append("content:type")
                break


# --- Tầng 5: LLM phân loại ---------------------------------------------------

_LLM_META_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "course_code": {"type": "string"},
        "doc_type": {
            "type": "string",
            "enum": sorted(VALID_TYPES),
        },
        "confidence": {"type": "number"},
    },
    "required": ["confidence"],
}

_LLM_META_SYSTEM = """Bạn phân loại tài liệu học tập của một trường đại học Việt Nam.
Dựa vào TÊN FILE và TRÍCH ĐOẠN ĐẦU của tài liệu, xác định:
- course_code: mã môn — BẮT BUỘC chọn từ DANH MỤC MÔN được cấp. Không chắc thuộc môn nào thì bỏ trống.
- doc_type: loại tài liệu (giao_trinh, slide, de_cuong, de_thi, bai_tap, tai_lieu_tham_khao, quy_che). Không chắc thì bỏ trống trường này.
- confidence: 0..1 — mức chắc chắn CHUNG cho các trường đã điền. Điền bừa sẽ gây xếp nhầm tài liệu, thà bỏ trống với confidence thấp."""

_LLM_MIN_CONFIDENCE = 0.7


def classify_with_llm(
    filename: str, excerpt: str, candidates: list[dict] | None = None
) -> DocMeta | None:
    """Tầng cuối — Flash-Lite. Chỉ gọi khi 4 tầng máy móc bó tay (offline, chậm
    cũng được). Kết quả bị loại nếu confidence thấp hoặc mã môn ngoài danh mục.
    """
    from app.llm import vertex

    if candidates is None:
        try:
            from app.db import fetch_all

            candidates = fetch_all(
                "SELECT code, name, aliases FROM courses WHERE active = 1 LIMIT 300"
            )
        except Exception:  # pragma: no cover
            return None
    if not candidates:
        return None

    catalog_lines = []
    valid_codes = set()
    for row in candidates:
        code = str(row["code"]).upper()
        valid_codes.add(code)
        aliases = str(row.get("aliases") or "").strip()
        suffix = f" (tên gọi khác: {aliases})" if aliases not in ("", "[]") else ""
        catalog_lines.append(f"- {code}: {row['name']}{suffix}")

    prompt = (
        f"TÊN FILE: {filename}\n\n"
        f"TRÍCH ĐOẠN ĐẦU:\n{' '.join(excerpt.split())[:1500]}\n\n"
        f"DANH MỤC MÔN:\n" + "\n".join(catalog_lines)
    )

    response = vertex.generate_json(
        system_instruction=_LLM_META_SYSTEM,
        prompt=prompt,
        response_schema=_LLM_META_SCHEMA,
        max_output_tokens=100,
    )
    if response is None:
        return None

    data = response.data
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if confidence < _LLM_MIN_CONFIDENCE:
        return None

    meta = DocMeta()
    code = str(data.get("course_code") or "").strip().upper()
    if code and code in valid_codes:  # LLM không được bịa môn ngoài danh mục
        meta.course_code = code
        meta.sources.append("llm:course")
    doc_type = str(data.get("doc_type") or "").strip()
    if doc_type in VALID_TYPES:
        meta.doc_type = doc_type
        meta.sources.append("llm:type")

    if not meta.sources:
        return None
    return meta


def _merge(base: DocMeta, extra: DocMeta, override: bool = False) -> None:
    for attr in ("title", "course_code", "doc_type", "semester", "lecturer", "year"):
        value = getattr(extra, attr)
        if value and (override or not getattr(base, attr)):
            setattr(base, attr, value)
    if extra.access != "public" and (override or base.access == "public"):
        base.access = extra.access
    base.sources.extend(extra.sources)
