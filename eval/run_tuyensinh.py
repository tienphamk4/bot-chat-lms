"""Bộ đo cho kho tư vấn tuyển sinh hiện tại.

Bộ eval 200 câu ở `questions.jsonl` viết cho kho tài liệu học tập cũ (Giải
tích 1, OOP, INT3306) — kho đó đã bị thay bằng hai tài liệu tuyển sinh, nên
chạy nó chỉ đo được việc dữ liệu đã đổi. File này đo đúng kho đang có.

Ba tiêu chí, đều máy chấm được:

- `answer`    : phải trả lời được VÀ nhắc đúng đối tượng được hỏi. Tiêu chí
                thứ hai mới là cái khó: hệ thống từng trả lời trôi chảy về
                NGÀNH KHÁC, và người hỏi không có cách nào nhận ra.
- `not_found` : kho không có dữ liệu thì phải nói chưa có, KHÔNG được bịa và
                cũng không được đổ là "ngoài phạm vi".
- `refuse`    : nhờ làm bài hộ thì từ chối.

Chạy: python -m eval.run_tuyensinh
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.core import chat
from app.core.access import ANONYMOUS
from app.schemas import ChatContext
from app.vn import vn_norm

QUESTIONS = Path(__file__).with_name("tuyensinh.jsonl")
NOT_FOUND_MARK = "chua co thong tin"
OUT_OF_SCOPE_MARK = "ngoai pham vi"


def _judge(case: dict, outcome) -> tuple[bool, str]:
    answer = vn_norm(outcome.answer)
    expect = case["expect"]

    if expect == "not_found":
        if OUT_OF_SCOPE_MARK in answer:
            return False, "nói ngoài phạm vi (phải là chưa có thông tin)"
        if NOT_FOUND_MARK not in answer:
            return False, "không nói chưa có — nghi bịa"
        return True, ""

    if expect == "refuse":
        return (outcome.branch in ("static", "refuse"), f"branch={outcome.branch}")

    if NOT_FOUND_MARK in answer:
        return False, "trả lời chưa có trong khi kho có dữ liệu"
    if not outcome.citations:
        return False, "không có trích dẫn nào"
    subjects = [vn_norm(s) for s in case["subject"]]
    if subjects and not any(s in answer for s in subjects):
        return False, f"trả lời không nhắc {case['subject']} — nghi sai đối tượng"
    return True, ""


def main() -> int:
    cases = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    ctx = ChatContext(assistant="tuyen-sinh")

    passed = 0
    for case in cases:
        started = time.time()
        try:
            outcome = chat.handle(f"eval-{case['id']}", case["message"], ANONYMOUS, context=ctx)
        except Exception as exc:  # pragma: no cover - phụ thuộc mạng
            print(f"  {case['id']}  LỖI   {exc}")
            continue
        ok, why = _judge(case, outcome)
        passed += ok
        print(
            f"  {case['id']}  {'ĐẠT ' if ok else 'HỎNG'}  "
            f"[{outcome.branch}] {int((time.time()-started)*1000):>6}ms "
            f"{len(outcome.citations)} nguồn  {case['message'][:44]:<46}{why}"
        )

    print(f"\n{passed}/{len(cases)} đạt ({passed/len(cases)*100:.0f}%)")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
