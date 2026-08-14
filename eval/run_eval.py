"""Bộ eval — phanh chất lượng của dự án (GĐ4, khung khởi động).

    python -m eval.run_eval                 # chạy toàn bộ eval/questions.jsonl
    python -m eval.run_eval --keep-cache    # không xóa cache trước khi chạy
    python -m eval.run_eval --only C        # chỉ chạy các câu id bắt đầu bằng C

Nguyên tắc (mục 10 CLAUDE.md): mọi thay đổi prompt hoặc tham số truy hồi phải
chạy lại bộ này trước khi coi là xong. Bộ câu hiện là ~40 câu hạt giống theo
kho demo — nạp kho thật thì mở rộng dần lên 200 câu (80 tra cứu / 70 nội dung /
30 ngoài phạm vi / 20 nhiều lượt) bằng cách thêm dòng JSONL, không cần sửa code.

Mỗi dòng questions.jsonl:
    id, category, user (0 = khách), message, context?,
    expect_intent?          — intent router phải trả
    expect_branch?          — branch nằm trong danh sách này
    must_contain? [..]      — câu trả lời phải chứa (so KHÔNG dấu, không hoa thường)
    must_cite?              — một citation.title phải chứa chuỗi này
    note?                   — ghi chú hiện trong báo cáo khi fail

Gọi thẳng core (không qua HTTP) — đo được cả khi chưa chạy uvicorn.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.access import ANONYMOUS, load_identity  # noqa: E402
from app.vn import vn_norm  # noqa: E402


def load_questions(path: Path, only: str | None) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if only and not str(row.get("id", "")).startswith(only):
                continue
            rows.append(row)
    return rows


def check(row: dict, outcome) -> list[str]:
    """Trả về danh sách lỗi — rỗng là PASS."""
    problems: list[str] = []

    expect_intent = row.get("expect_intent")
    if expect_intent and outcome.route.intent.value != expect_intent:
        problems.append(f"intent={outcome.route.intent.value} (mong {expect_intent})")

    expect_branch = row.get("expect_branch")
    if expect_branch and outcome.branch not in expect_branch:
        problems.append(f"branch={outcome.branch} (mong {'/'.join(expect_branch)})")

    answer_norm = vn_norm(outcome.answer)
    for needle in row.get("must_contain", []):
        if vn_norm(needle) not in answer_norm:
            problems.append(f"thiếu '{needle}' trong câu trả lời")

    must_cite = row.get("must_cite")
    if must_cite:
        titles = " | ".join(vn_norm(c.title) for c in outcome.citations)
        if vn_norm(must_cite) not in titles:
            problems.append(f"không trích nguồn chứa '{must_cite}' (có: {titles or 'rỗng'})")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval")
    parser.add_argument("--file", type=Path, default=Path(__file__).parent / "questions.jsonl")
    parser.add_argument("--keep-cache", action="store_true",
                        help="không xóa query_cache trước khi chạy")
    parser.add_argument("--only", default=None, help="chỉ chạy id có tiền tố này")
    args = parser.parse_args()

    questions = load_questions(args.file, args.only)
    if not questions:
        print("Không có câu hỏi nào."); return 2

    # Import muộn — để --help không cần DB.
    from app import db
    from app.core import chat as chat_core
    from app.schemas import ChatContext

    db.warm_pool()
    if not args.keep_cache:
        db.execute("DELETE FROM query_cache")
        print("Đã xóa query_cache — đo định tuyến thật, không dính cache cũ.")

    # Làm ấm Vertex để câu đầu không gánh phí bắt tay.
    from app.llm import embeddings, vertex
    embeddings.embed_query("khởi động eval")
    vertex.generate_text(system_instruction="Trả lời 1 ký tự.", prompt="1", max_output_tokens=5)

    results: list[dict] = []
    session = f"eval-{int(time.time())}"

    for index, row in enumerate(questions, 1):
        identity = ANONYMOUS if not row.get("user") else load_identity(int(row["user"]))
        context = ChatContext(**row["context"]) if row.get("context") else None

        started = time.perf_counter()
        try:
            outcome = chat_core.handle(session, row["message"], identity, None, context)
            problems = check(row, outcome)
            latency = int((time.perf_counter() - started) * 1000)
            results.append({
                "row": row, "problems": problems, "latency": latency,
                "branch": outcome.branch, "router": outcome.route.source,
                "cost": outcome.cost_usd,
            })
            mark = "PASS" if not problems else "FAIL"
            print(f"[{index:>2}/{len(questions)}] {mark} {row['id']} "
                  f"({outcome.branch}/{outcome.route.source}, {latency}ms)"
                  + (f" — {'; '.join(problems)}" if problems else ""))
        except Exception as exc:  # noqa: BLE001 — eval phải chạy hết bộ
            results.append({
                "row": row, "problems": [f"EXCEPTION: {exc}"], "latency": 0,
                "branch": "error", "router": "-", "cost": 0,
            })
            print(f"[{index:>2}/{len(questions)}] FAIL {row['id']} — EXCEPTION: {exc}")

    # ----- Tổng kết ----------------------------------------------------------
    passed = [r for r in results if not r["problems"]]
    print("\n" + "=" * 64)
    print(f"TỔNG: {len(passed)}/{len(results)} PASS "
          f"({100 * len(passed) // max(1, len(results))}%)")

    by_category: dict[str, list[dict]] = {}
    for r in results:
        by_category.setdefault(r["row"].get("category", "?"), []).append(r)
    for category, rows in sorted(by_category.items()):
        ok = sum(1 for r in rows if not r["problems"])
        print(f"  {category:<12} {ok}/{len(rows)}")

    branches: dict[str, int] = {}
    for r in results:
        branches[r["branch"]] = branches.get(r["branch"], 0) + 1
    print("Phân bố branch:", ", ".join(f"{k}={v}" for k, v in sorted(branches.items())))

    latencies = sorted(r["latency"] for r in results if r["latency"])
    if latencies:
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"Độ trễ: p50={p50}ms · p95={p95}ms · "
              f"trung bình={int(statistics.mean(latencies))}ms")
    total_cost = sum(r["cost"] for r in results)
    print(f"Chi phí cả bộ: {total_cost:.6f} USD")

    failures = [r for r in results if r["problems"]]
    if failures:
        print("\nCHI TIẾT FAIL:")
        for r in failures:
            note = r["row"].get("note")
            print(f"  {r['row']['id']}: {'; '.join(r['problems'])}"
                  + (f"  [ghi chú: {note}]" if note else ""))

    # Lưu báo cáo để so sánh giữa các lần chạy.
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"report-{stamp}.json"
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            [{**{"id": r["row"]["id"], "category": r["row"].get("category")},
              "pass": not r["problems"], "problems": r["problems"],
              "branch": r["branch"], "router": r["router"],
              "latency_ms": r["latency"]} for r in results],
            handle, ensure_ascii=False, indent=1,
        )
    print(f"\nBáo cáo: {report_path}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
