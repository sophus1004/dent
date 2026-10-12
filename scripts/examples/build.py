"""내장 예시 데이터 만들기 (개발용): 원고(content/)에 문제를 정해진 수만큼 심어 모듈의 example_data/에 파일과 정답지를 쓴다.

    uv run python scripts/examples/build.py

원고는 문제가 없는 깨끗한 글이고, 문제는 이 스크립트만 심는다.
같은 원고 · 같은 씨앗이면 같은 파일이 나온다(다시 만들어도 정답지가 바뀌지 않는다).
정답지(<열쇠>.answers.json)의 planted는 화면의 예시 목록에 보이고, checks는 테스트가 진단의 수와 견준다.
뜻 분석이 있어야 보이는 문제(근접 중복 · 오라벨 · 의미 쏠림 · 거짓 오답 …)는 semantic에 적는다(임베딩 · Jev에 따라 수가 흔들린다).
"""

import csv
import io
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from dent.modules.generation import rules as generation_rules
from dent.modules.generation.importing import context_hash
from dent.modules.retrieval.models import DEFAULT_DOC_MAX_TOKENS, DEFAULT_QUERY_MAX_TOKENS
from dent.modules.retrieval.text_rules import (
    EASY_PAIR_MIN_CHARS,
    EASY_PAIR_OVERLAP,
    LONG_QUERY_CHARS_PER_TOKEN,
    SHORT_QUERY_CHARS,
    document_input,
    estimate_tokens,
    has_broken_text,
    is_context_dependent,
    lexical_overlap,
    skip_generation_reason,
)

ROOT = Path(__file__).resolve().parents[2]
CONTENT = Path(__file__).resolve().parent / "content"
CLASSIFICATION_OUT = ROOT / "src/dent/modules/classification/example_data"
GENERATION_OUT = ROOT / "src/dent/modules/generation/example_data"
RETRIEVAL_OUT = ROOT / "src/dent/modules/retrieval/example_data"

# 씨앗 (같은 원고면 같은 파일)
SEED = 20260927

# 원본 분할 열의 값과 몫 (가져오면 하나로 합쳐진다)
SPLITS = (("train", 0.8), ("validation", 0.1), ("test", 0.1))

# ---------- 분류: 백과 문장 주제 ----------

TOPIC_LABELS = ("역사", "과학", "스포츠", "문화", "경제", "사회", "지리")

# 심는 수
DUPLICATE_ONCE = 20  # 한 번 더 넣는 문장 (여분 1)
DUPLICATE_TWICE = 5  # 두 번 더 넣는 문장 (여분 2)
DUPLICATE_ACROSS_SPLITS = 10  # 위 가운데 사본을 다른 분할에 두는 문장
CLEAR_CONFLICTS = 5  # 분명한 문장에 틀린 라벨 사본을 더한 무리
MISLABELED = 25  # 라벨만 바꾼 문장
NEAR_COPIES = 30  # 끝맺음 · 쉼표만 조금 바꾼 사본 (근접 중복)
NEAR_COPY_OTHER_LABEL = 10  # 위 가운데 라벨도 다르게 둔 것

# 근접 중복 사본: 같은 문장을 두 번 모은 것처럼 끝맺음만 바꾼다(앞 것부터 맞는 것 하나)
LIGHT_ENDINGS = (
    ("하였다.", "했다."),
    ("했다.", "하였다."),
    ("되었다.", "됐다."),
    ("됐다.", "되었다."),
    ("이었다.", "였다."),
    ("였다.", "이었다."),
)

# ---------- 검색: 백과 문서 ----------

# 꼬리말: 출처(이메일) · 저작권 · 전재 금지 (메타 모양) — 문서의 몫
FOOTER_SHARE = 0.6
FOOTER = "출처: 달라 백과 편집부 · 문의 editor@example.com\nⓒ 달라 백과, 무단 전재 및 재배포 금지"
# 머리 문장(메타 없음, 사람이 떼기 · 남김을 고른다) — 문서의 몫
LEAD_SHARE = 0.3
LEAD = "이 문서는 달라 백과 2026년판에 실린 글이다."
# 떼면 같아지는 문서 쌍(웹 주소 줄만 다름) · 가져올 때 합쳐지는 똑같은 줄 · 깨진 글자 문서
DUPLICATE_PAIRS = 5
EXACT_COPIES = 3
BROKEN_DOCUMENTS = 3
URL_LINE = "자세히 보기: https://example.com/wiki/{number}"

# ---------- 검색: 질문과 지문 ----------

EASY_PAIRS = 30  # 지문 문장을 거의 그대로 옮긴 질문
SAME_QUESTIONS = 15  # 분할만 다른 같은 줄 (가져올 때 합쳐진다)

# ---------- 검색: 질의와 오답 ----------

CONFLICTS = 8  # 정답과 같은 글을 오답에도 넣은 질의 (정답이자 오답)
NEAR_SAME_NEGATIVES = 8  # 정답을 조금만 고친 글을 오답에 넣은 질의
EASY_NEGATIVE_QUERIES = 20  # 다른 무리(주제가 딴판)의 문서만 오답으로 둔 질의
NO_NEGATIVE_QUERIES = 20  # 오답 없이 정답만 둔 질의
EMPTY_POSITIVES = 3  # 정답 칸이 빈 줄 (가져올 때 건너뛴다)

# ---------- 검색: 회사 규정과 FAQ ----------

RULE_SIGNATURE = "달라 주식회사 인사팀 · 문의 hr@example.com · 02-0000-0000"
FAQ_FOOTER = "달라마켓 고객센터는 평일 오전 9시부터 오후 6시까지 운영한다."

# 짧은 문장 · 질의의 기준 (진단과 같다: 이보다 짧으면 짧은 것)
SHORT_TEXT_CHARS = 5


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """원고 JSONL 한 파일. 없으면 멈춘다(원고를 먼저 써야 한다)."""
    if not path.exists():
        sys.exit(f"원고가 없습니다: {path.relative_to(ROOT)}")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def pick_split(rng: random.Random) -> str:
    """원본 분할 값 하나 (몫대로)."""
    roll = rng.random()
    total = 0.0
    for name, share in SPLITS:
        total += share
        if roll < total:
            return name
    return SPLITS[-1][0]


def other_split(split: str, rng: random.Random) -> str:
    """다른 분할 값 하나."""
    return rng.choice([name for name, _ in SPLITS if name != split])


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def write_answers(path: Path, answers: dict[str, Any]) -> None:
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_long_document(title: str, header: str, body: str) -> bool:
    """가져온 뒤 '긴 문서'인지: 학습 글(제목 › 머리말 칸 + 본문)의 토큰 어림이 기본 최대 토큰을 넘는지 (진단과 같은 규칙)."""
    training = document_input(
        title=title, header=header, section="", body=body, section_header=False
    )
    return estimate_tokens(training) > DEFAULT_DOC_MAX_TOKENS


def is_short_or_long_query(query: str) -> bool:
    """'짧은 · 긴 질의'인지 (진단과 같은 규칙: 글자 수 < 5 또는 > 최대 토큰 × 글자/토큰)."""
    length = len(query)
    return (
        length < SHORT_QUERY_CHARS or length > DEFAULT_QUERY_MAX_TOKENS * LONG_QUERY_CHARS_PER_TOKEN
    )


def is_easy(query: str, document: str) -> bool:
    """'쉬운 쌍'인지 (진단과 같은 규칙: 공백 뺀 질의 ≥ 10자이고 글자 두 개 묶음 겹침 ≥ 0.9)."""
    compacted = "".join(query.split())
    return (
        len(compacted) >= EASY_PAIR_MIN_CHARS
        and lexical_overlap(query, document) >= EASY_PAIR_OVERLAP
    )


# ---------- 분류 ----------


def light_edit(text: str) -> str | None:
    """끝맺음(했다 ↔ 하였다 …)만 바꾼 사본. 맞는 끝맺음이 없으면 첫 '은 · 는' 뒤에 쉼표를 넣는다.
    둘 다 안 되면(자연스럽게 고칠 곳이 없으면) None."""
    for old, new in LIGHT_ENDINGS:
        if text.endswith(old):
            return text[: -len(old)] + new
    # 주제어 뒤(가장 앞의 '은 ' · '는 ')에 쉼표를 넣는다.
    found = [index for index in (text.find("은 "), text.find("는 ")) if index > 0]
    if found:
        index = min(found)
        return f"{text[: index + 1]},{text[index + 1 :]}"
    return None


def build_topics() -> None:
    """백과 문장 주제: 원고 문장에 중복 · 라벨 충돌 · 짧은 문장 · 오라벨 · 근접 중복을 심고 원본 분할 열을 단다."""
    rng = random.Random(SEED)
    base = []
    extra = []
    for label in TOPIC_LABELS:
        base += [item for item in read_jsonl(CONTENT / "cls_topics" / f"{label}.jsonl")]
    for name in ("extra_a1", "extra_a2", "extra_a3"):
        extra += read_jsonl(CONTENT / "cls_topics" / f"{name}.jsonl")
    texts = [item["text"] for item in base]
    if len(set(texts)) != len(texts):
        sys.exit("분류 원고에 같은 문장이 있습니다.")

    # 원고의 말 바꾼 문장(paraphrase)은 쓰지 않는다: 표현이 많이 달라 임베딩 유사도가 근접 중복 기준(0.92)에
    # 못 미치는 것이 많다(bge-m3로 30쌍 가운데 13쌍). 대신 끝맺음 · 쉼표만 바꾼 사본을 심는다.
    shorts = [item for item in extra if item["kind"] == "short"]
    ambiguous = [item for item in extra if item["kind"] == "ambiguous"]

    # 서로 겹치지 않게 심을 문장을 고른다(문장 하나에 문제 하나).
    free = list(base)
    rng.shuffle(free)
    duplicates = free[: DUPLICATE_ONCE + DUPLICATE_TWICE]
    conflicts = free[len(duplicates) : len(duplicates) + CLEAR_CONFLICTS]
    used = len(duplicates) + len(conflicts)
    mislabeled = free[used : used + MISLABELED]
    near_sources = [
        item
        for item in free[used + MISLABELED :]
        if light_edit(item["text"]) is not None and light_edit(item["text"]) not in texts
    ][:NEAR_COPIES]
    relabel = {
        item["id"]: rng.choice([label for label in TOPIC_LABELS if label != item["label"]])
        for item in mislabeled
    }

    rows: list[dict[str, str]] = []
    for item in base:
        label = relabel.get(item["id"], item["label"])
        rows.append(
            {"text": item["text"], "label": label, "split": pick_split(rng), "topic": item["topic"]}
        )
    split_of = {row["text"]: row["split"] for row in rows}

    duplicate_details = []
    for index, item in enumerate(duplicates):
        copies = 2 if index < DUPLICATE_TWICE else 1
        across = index < DUPLICATE_ACROSS_SPLITS
        for _ in range(copies):
            split = other_split(split_of[item["text"]], rng) if across else split_of[item["text"]]
            rows.append(
                {
                    "text": item["text"],
                    "label": item["label"],
                    "split": split,
                    "topic": item["topic"],
                }
            )
        duplicate_details.append({"text": item["text"], "label": item["label"], "extra": copies})

    conflict_details = []
    for item in conflicts:
        wrong = rng.choice([label for label in TOPIC_LABELS if label != item["label"]])
        rows.append(
            {"text": item["text"], "label": wrong, "split": pick_split(rng), "topic": item["topic"]}
        )
        conflict_details.append(
            {"text": item["text"], "labels": [item["label"], wrong], "kind": "분명"}
        )
    for item in ambiguous:
        for label in item["labels"]:
            rows.append(
                {"text": item["text"], "label": label, "split": pick_split(rng), "topic": "애매"}
            )
        conflict_details.append({"text": item["text"], "labels": item["labels"], "kind": "애매"})

    for item in shorts:
        rows.append(
            {
                "text": item["text"],
                "label": item["label"],
                "split": pick_split(rng),
                "topic": "짧은 문장",
            }
        )

    near_details = []
    other_label_ids = {item["id"] for item in rng.sample(near_sources, NEAR_COPY_OTHER_LABEL)}
    for source in near_sources:
        label = source["label"]
        if source["id"] in other_label_ids:
            label = rng.choice([name for name in TOPIC_LABELS if name != source["label"]])
        copy = str(light_edit(source["text"]))
        rows.append(
            {"text": copy, "label": label, "split": pick_split(rng), "topic": source["topic"]}
        )
        near_details.append({"a": source["text"], "b": copy, "labels": [source["label"], label]})

    rng.shuffle(rows)
    CLASSIFICATION_OUT.mkdir(parents=True, exist_ok=True)
    write_csv(
        CLASSIFICATION_OUT / "topics.csv",
        ["문장", "주제", "분할", "소주제"],
        [[row["text"], row["label"], row["split"], row["topic"]] for row in rows],
    )

    counts = {label: sum(1 for row in rows if row["label"] == label) for label in TOPIC_LABELS}
    ratio = max(counts.values()) / min(counts.values())
    sports = [item for item in base if item["label"] == "스포츠"]
    soccer = sum(1 for item in sports if item["topic"] == "축구")
    extra_records = sum(item["extra"] for item in duplicate_details)
    conflict_records = sum(len(item["labels"]) for item in conflict_details)
    write_answers(
        CLASSIFICATION_OUT / "topics.answers.json",
        {
            "key": "topics",
            "rows": len(rows),
            "planted": [
                {"name": "중복 여분", "count": extra_records, "unit": "건"},
                {"name": "라벨 충돌", "count": len(conflict_details), "unit": "무리"},
                {"name": "짧은 문장", "count": len(shorts), "unit": "건"},
                {"name": "라벨 균형", "count": round(ratio), "unit": "배"},
                {"name": "오라벨", "count": len(mislabeled), "unit": "건"},
                {"name": "근접 중복", "count": len(near_details), "unit": "쌍"},
            ],
            "checks": {
                "duplicates": {"groups": len(duplicate_details), "extra_records": extra_records},
                "conflicts": {"groups": len(conflict_details), "records": conflict_records},
                "short": {"records": len(shorts)},
                "labels": counts,
                "balance_ratio": round(ratio, 2),
                "effective_count": len({row["text"] for row in rows}),
            },
            "semantic": {
                "mislabeled": [
                    {"text": item["text"], "label": relabel[item["id"]], "right": item["label"]}
                    for item in mislabeled
                ],
                "near_duplicates": near_details,
                # 스포츠의 약 2/3가 축구지만, 의미 쏠림 검사(라벨 안 KMeans 8무리 가운데 가장 큰 무리 ≥ 40%)는
                # 축구를 여러 무리로 나눠 잡지 못한다(bge-m3로 가장 큰 무리 20%). 그래서 심은 문제로 세지 않는다.
                "skew": {
                    "label": "스포츠",
                    "topic": "축구",
                    "share": round(soccer / len(sports), 2),
                    "detected": False,
                },
            },
            "details": {
                "duplicates": duplicate_details,
                "conflicts": conflict_details,
                "short": shorts,
            },
        },
    )
    print(f"topics.csv {len(rows)}줄 · 라벨 {counts} · 균형 {ratio:.1f}배")


def build_intents() -> None:
    """고객 문의 의도: 문제가 없는 대조군(엑셀 · 시트 둘)."""
    items = read_jsonl(CONTENT / "cls_intents" / "intents.jsonl")
    normalized = {compact(item["text"]) for item in items}
    if len(normalized) != len(items):
        sys.exit("의도 원고에 같은 문의가 있습니다.")
    if any(len(item["text"].strip()) < SHORT_TEXT_CHARS for item in items):
        sys.exit("의도 원고에 짧은 문의가 있습니다.")
    rng = random.Random(SEED)
    rows = list(items)
    rng.shuffle(rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "문의"
    sheet.append(["문의", "의도"])
    for item in rows:
        sheet.append([item["text"], item["label"]])
    guide = workbook.create_sheet("의도 설명")
    guide.append(["의도", "뜻"])
    for label, meaning in (
        ("환불", "돈을 돌려받는 일"),
        ("배송", "배송 상태 · 날짜 · 주소"),
        ("교환", "다른 크기 · 색 · 상품으로 바꾸기"),
        ("결제", "결제 수단 · 실패 · 영수증"),
        ("회원", "가입 · 로그인 · 탈퇴"),
        ("쿠폰", "쿠폰 · 적립금 · 할인"),
        ("상품 문의", "상품 정보 · 크기 · 재고"),
        ("AS", "고장 · 수리 · 보증"),
    ):
        guide.append([label, meaning])
    CLASSIFICATION_OUT.mkdir(parents=True, exist_ok=True)
    workbook.save(CLASSIFICATION_OUT / "intents.xlsx")
    labels = sorted({item["label"] for item in items})
    write_answers(
        CLASSIFICATION_OUT / "intents.answers.json",
        {
            "key": "intents",
            "rows": len(items),
            "planted": [],
            "checks": {
                "duplicates": {"groups": 0, "extra_records": 0},
                "conflicts": {"groups": 0, "records": 0},
                "short": {"records": 0},
                "labels": {
                    label: sum(1 for item in items if item["label"] == label) for label in labels
                },
                "balance_ratio": 1.0,
                "effective_count": len(items),
            },
        },
    )
    print(f"intents.xlsx {len(items)}줄")


# ---------- 검색 ----------


def break_text(text: str, rng: random.Random) -> str:
    """깨진 글자를 몇 군데 넣는다(대체 문자 · 잘못 풀린 UTF-8)."""
    words = text.split(" ")
    for marker in ("�", "Ã©"):
        position = rng.randrange(1, len(words) - 1)
        words[position] = words[position] + marker
    return " ".join(words)


def build_encyclopedia() -> None:
    """백과 문서만: 꼬리말 · 머리 문장 · 웹 주소 줄 · 떼면 같아지는 쌍 · 똑같은 줄 · 깨진 글자 · 목차 · 표."""
    rng = random.Random(SEED)
    long_docs = read_jsonl(CONTENT / "ret_docs" / "long_a.jsonl") + read_jsonl(
        CONTENT / "ret_docs" / "long_b.jsonl"
    )
    short_docs = read_jsonl(CONTENT / "ret_docs" / "short.jsonl")
    special = read_jsonl(CONTENT / "ret_docs" / "special.jsonl")
    docs = long_docs + short_docs
    rng.shuffle(docs)

    footer_ids = {doc["id"] for doc in rng.sample(docs, round(len(docs) * FOOTER_SHARE))}
    lead_ids = {doc["id"] for doc in rng.sample(docs, round(len(docs) * LEAD_SHARE))}
    plain = [doc for doc in docs if doc["id"] not in footer_ids]
    pair_ids = {doc["id"] for doc in rng.sample(short_docs, DUPLICATE_PAIRS)}
    candidates = [doc for doc in short_docs if doc["id"] not in pair_ids]
    broken_ids = {doc["id"] for doc in rng.sample(candidates, BROKEN_DOCUMENTS)}
    exact_ids = {
        doc["id"]
        for doc in rng.sample([d for d in candidates if d["id"] not in broken_ids], EXACT_COPIES)
    }

    def body(doc: dict[str, Any], *, url_number: int | None = None) -> str:
        text = doc["text"]
        if doc["id"] in broken_ids:
            text = break_text(text, rng)
        if doc["id"] in lead_ids:
            text = f"{LEAD}\n{text}"
        if url_number is not None:
            text = f"{text}\n{URL_LINE.format(number=url_number)}"
        if doc["id"] in footer_ids:
            text = f"{text}\n\n{FOOTER}"
        return text

    rows = []
    number = 1000
    for doc in docs:
        if doc["id"] in pair_ids:
            # 웹 주소 줄만 다른 두 문서: 가져올 때는 따로 들어오고, 웹 주소(메타)를 떼면 학습 글이 같아진다.
            for _ in range(2):
                number += rng.randrange(1, 90)
                rows.append([doc["title"], body(doc, url_number=number), doc["field"]])
            continue
        rows.append([doc["title"], body(doc), doc["field"]])
        if doc["id"] in exact_ids:
            rows.append(rows[-1][:])
    for doc in special:
        rows.append([doc["title"], doc["text"], doc.get("field", "생활")])
    rng.shuffle(rows)
    RETRIEVAL_OUT.mkdir(parents=True, exist_ok=True)
    write_csv(RETRIEVAL_OUT / "encyclopedia.csv", ["제목", "본문", "분야"], rows)

    documents = len(docs) + DUPLICATE_PAIRS + len(special)
    unique_rows = {(title, text, field) for title, text, field in rows}
    long_count = sum(
        1 for title, text, field in unique_rows if is_long_document(title, field, text)
    )
    broken_count = sum(1 for _, text, _ in unique_rows if has_broken_text(text))
    pick_count = sum(1 for _, text, _ in unique_rows if skip_generation_reason(text) is not None)
    write_answers(
        RETRIEVAL_OUT / "encyclopedia.answers.json",
        {
            "key": "encyclopedia",
            "rows": len(rows),
            "planted": [
                {"name": "긴 문서", "count": long_count, "unit": "문서"},
                {"name": "반복 꼬리말", "count": len(footer_ids), "unit": "문서"},
                {"name": "반복 머리 문장", "count": len(lead_ids), "unit": "문서"},
                {"name": "깨진 글자", "count": BROKEN_DOCUMENTS, "unit": "문서"},
                {"name": "떼면 같아지는 문서", "count": DUPLICATE_PAIRS, "unit": "쌍"},
                {"name": "똑같은 줄", "count": EXACT_COPIES, "unit": "줄"},
                {"name": "목차 · 표", "count": len(special), "unit": "문서"},
            ],
            "checks": {
                "documents": documents,
                "merged": EXACT_COPIES,
                "long": long_count,
                "broken": broken_count,
                "pick": pick_count,
                "footer_documents": len(footer_ids),
                "lead_documents": len(lead_ids),
                "url_documents": DUPLICATE_PAIRS * 2,
                "duplicate_pairs": DUPLICATE_PAIRS,
            },
            "details": {
                "footer": FOOTER,
                "lead": LEAD,
                "url": URL_LINE,
                "pairs": sorted(pair_ids),
                "broken": sorted(broken_ids),
                "exact": sorted(exact_ids),
                "plain_documents": len(plain),
            },
        },
    )
    print(f"encyclopedia.csv {len(rows)}줄 · 문서 {documents}")


def build_reading() -> None:
    """질문과 지문(MRC): 쉬운 쌍(지문 문장을 거의 그대로) · 분할만 다른 같은 줄 · 원본 분할 열을 심는다.

    문맥 의존 · 짧은 · 긴 질의와 긴 지문은 원고에 이미 있다(kind · 길이)."""
    rng = random.Random(SEED)
    passages = {
        item["id"]: item
        for name in ("passages_a", "passages_b")
        for item in read_jsonl(CONTENT / "ret_mrc" / f"{name}.jsonl")
    }
    questions = [
        item
        for name in ("questions_a", "questions_b")
        for item in read_jsonl(CONTENT / "ret_mrc" / f"{name}.jsonl")
    ]
    for item in questions:
        if item["answer"] not in passages[item["passage"]]["text"]:
            sys.exit(f"답이 지문에 없습니다: {item['id']}")
    if len({item["question"] for item in questions}) != len(questions):
        sys.exit("질문 원고에 같은 질문이 있습니다.")

    rows = []
    for item in questions:
        passage = passages[item["passage"]]
        rows.append(
            [item["question"], passage["text"], item["answer"], passage["title"], pick_split(rng)]
        )

    easy = []
    normal_passages = [item for item in passages.values() if len(item["text"]) < 1_500]
    for passage in rng.sample(normal_passages, EASY_PAIRS):
        sentences = [
            sentence.strip().rstrip(".")
            for sentence in re.split(r"(?<=[.!?])\s+", passage["text"].replace("\n", " "))
            if 20 <= len(sentence.strip()) <= 90
        ]
        if not sentences:
            continue
        sentence = rng.choice(sentences)
        answer = sentence.split(" ")[0]
        rows.append([sentence, passage["text"], answer, passage["title"], pick_split(rng)])
        easy.append(sentence)

    same = rng.sample([row for row in rows if row[0] not in easy], SAME_QUESTIONS)
    for row in same:
        rows.append([row[0], row[1], row[2], row[3], other_split(row[4], rng)])
    rng.shuffle(rows)
    write_csv(RETRIEVAL_OUT / "reading.csv", ["질문", "지문", "답", "제목", "분할"], rows)

    kinds = {
        kind: sum(1 for item in questions if item["kind"] == kind)
        for kind in ("context", "short", "long")
    }
    long_passages = [
        item for item in passages.values() if is_long_document(item["title"], "", item["text"])
    ]
    queries = len(questions) + len(easy)
    # 진단과 같은 규칙으로 센다(원고의 kind와 어긋나면 원고를 고친다).
    unique_queries = {row[0]: row for row in rows}
    context_count = sum(1 for query in unique_queries if is_context_dependent(query))
    short_long_count = sum(1 for query in unique_queries if is_short_or_long_query(query))
    easy_count = sum(1 for query, row in unique_queries.items() if is_easy(query, row[1]))
    if context_count != kinds["context"] or short_long_count != kinds["short"] + kinds["long"]:
        print(
            f"주의: 문맥 의존 {context_count}(원고 {kinds['context']}) · 짧은 · 긴 {short_long_count}"
        )
    write_answers(
        RETRIEVAL_OUT / "reading.answers.json",
        {
            "key": "reading",
            "rows": len(rows),
            "planted": [
                {"name": "문맥 의존 질의", "count": context_count, "unit": "건"},
                {"name": "짧은 · 긴 질의", "count": short_long_count, "unit": "건"},
                {"name": "쉬운 쌍", "count": easy_count, "unit": "건"},
                {"name": "분할만 다른 같은 줄", "count": SAME_QUESTIONS, "unit": "줄"},
                {"name": "긴 지문", "count": len(long_passages), "unit": "문서"},
            ],
            "checks": {
                "queries": queries,
                "documents": len(passages),
                "context": context_count,
                "short_long": short_long_count,
                "easy_pair": easy_count,
                "long": len(long_passages),
            },
            "details": {"easy": easy, "same": [row[0] for row in same]},
        },
    )
    print(f"reading.csv {len(rows)}줄 · 질의 {queries} · 지문 {len(passages)}")


def nudge(text: str) -> str:
    """정답을 조금만 고친 글 (근접 중복이 되게): 끝맺음만 바꾼다."""
    if text.endswith("다."):
        return text[:-2] + "다고 알려져 있다."
    return text + " 이 점이 잘 알려져 있다."


def build_triplets() -> None:
    """질의 · 정답 · 오답: 거짓 오답 · 판정 충돌 · 정답과 거의 같은 오답 · 쉬운 오답 · 오답 없음 · 정답 빈 줄을 심는다."""
    rng = random.Random(SEED)
    docs = {
        item["id"]: item
        for name in ("docs_a", "docs_b")
        for item in read_jsonl(CONTENT / "ret_triplets" / f"{name}.jsonl")
    }
    queries = [
        item
        for name in ("queries_a", "queries_b")
        for item in read_jsonl(CONTENT / "ret_triplets" / f"{name}.jsonl")
    ]
    clusters: dict[str, list[str]] = {}
    for doc in docs.values():
        if doc["kind"] == "base":
            clusters.setdefault(doc["cluster"], []).append(doc["id"])

    false_negatives = [item for item in queries if item.get("false_negative")]
    rest = [item for item in queries if not item.get("false_negative")]
    rng.shuffle(rest)
    conflicts = rest[:CONFLICTS]
    near_same = rest[CONFLICTS : CONFLICTS + NEAR_SAME_NEGATIVES]
    easy = rest[
        CONFLICTS + NEAR_SAME_NEGATIVES : CONFLICTS + NEAR_SAME_NEGATIVES + EASY_NEGATIVE_QUERIES
    ]
    lone_start = CONFLICTS + NEAR_SAME_NEGATIVES + EASY_NEGATIVE_QUERIES
    lonely = rest[lone_start : lone_start + NO_NEGATIVE_QUERIES]
    special = {
        item["id"]: kind
        for kind, group in (
            ("conflict", conflicts),
            ("near", near_same),
            ("easy", easy),
            ("lonely", lonely),
        )
        for item in group
    }

    rows = []
    for item in queries:
        positive = docs[item["positive"]]["text"]
        negatives = [docs[doc_id]["text"] for doc_id in item["negatives"]]
        kind = special.get(item["id"])
        if item.get("false_negative"):
            negatives = [docs[item["false_negative"]]["text"], *negatives][:3]
        elif kind == "conflict":
            negatives = [positive, *negatives][:3]
        elif kind == "near":
            negatives = [nudge(positive), *negatives][:3]
        elif kind == "easy":
            cluster = docs[item["positive"]]["cluster"]
            others = [doc_id for name, ids in clusters.items() if name != cluster for doc_id in ids]
            negatives = [docs[doc_id]["text"] for doc_id in rng.sample(others, 3)]
        elif kind == "lonely":
            negatives = []
        rows.append([item["query"], positive, *(negatives + ["", "", ""])[:3]])
    for item in rng.sample(rest[lone_start + NO_NEGATIVE_QUERIES :], EMPTY_POSITIVES):
        rows.append([item["query"] + " 알려 줘", "", docs[item["negatives"][0]]["text"], "", ""])
    rng.shuffle(rows)
    write_csv(RETRIEVAL_OUT / "triplets.csv", ["질의", "정답", "오답1", "오답2", "오답3"], rows)

    write_answers(
        RETRIEVAL_OUT / "triplets.answers.json",
        {
            "key": "triplets",
            "rows": len(rows),
            "planted": [
                {"name": "판정 충돌", "count": CONFLICTS, "unit": "건"},
                {"name": "거짓 오답", "count": len(false_negatives), "unit": "건"},
                {"name": "정답과 거의 같은 오답", "count": NEAR_SAME_NEGATIVES, "unit": "건"},
                {"name": "쉬운 오답", "count": EASY_NEGATIVE_QUERIES, "unit": "질의"},
                {"name": "오답 없음", "count": NO_NEGATIVE_QUERIES, "unit": "질의"},
                {"name": "정답 빈 줄", "count": EMPTY_POSITIVES, "unit": "줄"},
            ],
            "checks": {
                "queries": len(queries),
                "skipped": EMPTY_POSITIVES,
                "conflict": CONFLICTS,
                "no_negative": len(queries),
            },
            "semantic": {
                "false_negatives": [item["query"] for item in false_negatives],
                "near_same": [item["query"] for item in near_same],
                "easy": [item["query"] for item in easy],
            },
            "details": {
                "conflicts": [item["query"] for item in conflicts],
                "lonely": [item["query"] for item in lonely],
            },
        },
    )
    print(f"triplets.csv {len(rows)}줄 · 질의 {len(queries)} · 문서 {len(docs)}")


def build_company() -> None:
    """회사 규정과 FAQ: 규정 끝에 반복 서명(이메일 · 전화), FAQ 끝에 반복 문장(메타 없음)을 단다."""
    rng = random.Random(SEED)
    docs = read_jsonl(CONTENT / "ret_policy" / "docs.jsonl")
    rows = []
    for doc in docs:
        tail = RULE_SIGNATURE if doc["kind"] == "규정" else FAQ_FOOTER
        rows.append([doc["title"], f"{doc['text']}\n\n{tail}", doc["kind"]])
    rng.shuffle(rows)
    write_csv(RETRIEVAL_OUT / "company.csv", ["제목", "본문", "종류"], rows)
    rules = [doc for doc in docs if doc["kind"] == "규정"]
    long_rules = [row for row in rows if is_long_document(row[0], row[2], row[1])]
    write_answers(
        RETRIEVAL_OUT / "company.answers.json",
        {
            "key": "company",
            "rows": len(rows),
            "planted": [
                {"name": "반복 서명", "count": len(rules), "unit": "문서"},
                {"name": "반복 안내 문장", "count": len(docs) - len(rules), "unit": "문서"},
                {"name": "긴 문서", "count": len(long_rules), "unit": "문서"},
            ],
            "checks": {
                "documents": len(docs),
                "long": len(long_rules),
                "signature_documents": len(rules),
                "footer_documents": len(docs) - len(rules),
            },
        },
    )
    print(f"company.csv {len(rows)}줄")


# ---------- 생성: 상담 · 글쓰기 SFT ----------

# 심는 수 (원고 76대화에서 서로 다른 대화를 골라 심는다)
GEN_DUPLICATES = 3  # 똑같은 사본 (같은 지시 여분)
GEN_EMPTY_PROMPT = 2  # 사용자 말을 비운 사본
GEN_STRUCTURE = 1  # 사용자 말이 두 번 잇달아 나오는 사본
GEN_PII = 3  # 예약된 이메일(example.com)을 넣은 대화
GEN_EMPTY_ANSWER = 1  # 응답을 비운 대화
GEN_TRUNCATED = 4  # 응답을 문장 가운데서 자른 대화
GEN_CODE_TRANSLATED = 3  # 코드 안의 이름을 한국어로 바꾼 대화
GEN_LOOP = 2  # 끝에 같은 조각을 되풀이한 대화
GEN_REFUSAL = 3  # 멀쩡한 요청을 거절 · 상투구로 답한 대화
GEN_SHORT = 3  # 분류 과제의 답을 한 낱말로 줄인 대화 (맞는 답이라 Jev가 괜찮다고 볼 수 있다)
GEN_REPEAT = 2  # 한 문장을 네 번 되풀이한 대화
GEN_LANG_MIX = 2  # 번역 과제를 영어로 답한 대화
GEN_BROKEN = 2  # 대체 문자(�)가 든 대화
GEN_LONG = 1  # 여러 응답을 이어 6,000자를 넘긴 대화
# 뜻 분석이 있어야 보이는 것 (정답지의 semantic)
GEN_NEAR = 3  # 지시를 조금 바꿔 쓴 사본 (근접 중복)
GEN_MISMATCH = 2  # 응답을 서로 바꿔 단 분류 · 아이디어 대화 짝 (지시 불일치 2 × 2)

# 번역된 코드: 코드 안에서 바꿀 이름 (영어 → 한국어)
CODE_RENAMES = {
    "values": "값들",
    "value": "값",
    "text": "글",
    "total": "합계",
    "numbers": "수들",
    "path": "경로",
    "scores": "점수",
    "ranked": "순위",
    "count": "개수",
}

# 거절 · 상투구 응답
GEN_REFUSALS = (
    "죄송하지만 그 요청은 도와드릴 수 없습니다.",
    "AI 언어 모델로서 저는 개인적인 의견을 드릴 수 없습니다. 다른 도움이 필요하시면 말씀해 주세요.",
    "죄송하지만 이 요청은 제 정책상 이행할 수 없습니다.",
)

# 영어로 답한 번역 (언어 섞임)
GEN_ENGLISH = (
    "Sure! Here is the translation you asked for. The meeting has been rescheduled, so please check your calendar again.",
    "Of course. The sentence simply means that you should water the plants two times every week, nothing more.",
)


def _answer(item: dict[str, Any]) -> str:
    return item["messages"][-1]["content"]


def _with_answer(item: dict[str, Any], answer: str) -> dict[str, Any]:
    return {**item, "messages": [*item["messages"][:-1], {"role": "assistant", "content": answer}]}


def _with_user(item: dict[str, Any], change: Any) -> dict[str, Any]:
    """마지막 사용자 말을 change(글)로 바꾼 사본."""
    messages = [dict(m) for m in item["messages"]]
    last = max(i for i, m in enumerate(messages) if m["role"] == "user")
    messages[last]["content"] = change(messages[last]["content"])
    return {**item, "messages": messages}


def _truncate(text: str) -> str:
    """문장 가운데서 자른다(마지막 줄이 짧지 않게, 끝에 문장 부호가 없게)."""
    cut = text[: max(40, int(len(text) * 0.6))].rstrip()
    while cut and (cut[-1] in ".!?:)" or cut[-1] in "다요죠까네음함임됨"):
        cut = cut[:-1].rstrip()
    return cut


def _translate_code(text: str) -> str:
    """코드 울타리 안의 이름만 한국어로 바꾼다(번역기가 코드까지 옮긴 모양)."""

    def rename(match: re.Match[str]) -> str:
        code = match.group(0)
        for english, korean in CODE_RENAMES.items():
            code = re.sub(rf"\b{english}\b", korean, code)
        return code

    return re.sub(r"```[^\n]*\n.*?```", rename, text, flags=re.DOTALL)


def build_gen_sft() -> None:
    """상담 · 글쓰기 SFT: 원고에 글의 모양 문제를 모두 심고, 뜻으로만 보이는 문제도 조금 심는다(messages jsonl)."""
    items = read_jsonl(CONTENT / "gen_sft" / "sft.jsonl")
    if any(generation_rules.measure(item["messages"]) for item in items):
        sys.exit("생성 원고에 규칙 문제가 있습니다(원고는 깨끗해야 한다).")
    rng = random.Random(SEED)
    by_task: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        by_task.setdefault(item["task"], []).append(index)
    single = [i for i, item in enumerate(items) if len(item["messages"]) == 2]
    used: set[int] = set()

    def take(pool: list[int], count: int, plant: Any = None, key: str = "") -> list[int]:
        """pool에서 아직 쓰지 않은 대화를 count개 고른다. plant · key를 주면 심었을 때 그 검사에 걸리는 것만."""
        free = [index for index in pool if index not in used]
        if plant is not None:
            free = [
                index
                for index in free
                if key in generation_rules.measure(plant(items[index])["messages"])
            ]
        picked = rng.sample(free, count)
        used.update(picked)
        return picked

    rows = [dict(item) for item in items]
    added: list[dict[str, Any]] = []

    def translated(item: dict[str, Any]) -> dict[str, Any]:
        return _with_answer(item, _translate_code(_answer(item)))

    for index in take(by_task["code"], GEN_CODE_TRANSLATED, translated, "code_translated"):
        rows[index] = translated(items[index])
    for index in take(by_task["translate"], GEN_LANG_MIX):
        rows[index] = _with_answer(rows[index], GEN_ENGLISH[len(used) % len(GEN_ENGLISH)])
    for index in take(by_task["classify"], GEN_SHORT):
        rows[index] = _with_answer(rows[index], _answer(items[index]).split(".")[0][:3])
    # 지시 불일치: 분류 대화와 아이디어 대화의 응답을 서로 바꿔 단다(뜻 분석 · Jev가 잡는다).
    # 과제가 먼 짝이라야 Jev가 양쪽 모두 '따르지 않음'으로 본다. 바꾼 뒤 다른 검사에 걸리는 짝은 쓰지 않는다
    # (다른 검사가 잡은 대화는 지시 불일치로 세지 않는다).
    clean_pairs = [
        (left, right)
        for left in by_task["classify"]
        for right in by_task["ideas"]
        if left not in used
        and right not in used
        and not generation_rules.measure(
            _with_answer(items[left], _answer(items[right]))["messages"]
        )
        and not generation_rules.measure(
            _with_answer(items[right], _answer(items[left]))["messages"]
        )
    ]
    swapped = 0
    for left, right in rng.sample(clean_pairs, len(clean_pairs)):
        if swapped == GEN_MISMATCH:
            break
        if left in used or right in used:
            continue
        used.update((left, right))
        swapped += 1
        rows[left] = _with_answer(items[left], _answer(items[right]))
        rows[right] = _with_answer(items[right], _answer(items[left]))
    prose = by_task["writing"] + by_task["explain"] + by_task["ideas"] + by_task["summary"]

    def truncated(item: dict[str, Any]) -> dict[str, Any]:
        return _with_answer(item, _truncate(_answer(item)))

    for index in take(prose, GEN_TRUNCATED, truncated, "truncated"):
        rows[index] = truncated(items[index])
    for offset, index in enumerate(take(prose, GEN_REFUSAL)):
        rows[index] = _with_answer(rows[index], GEN_REFUSALS[offset])
    for index in take(prose, GEN_LOOP):
        rows[index] = _with_answer(rows[index], _answer(items[index]) + " 정말 좋아요" * 8)
    for index in take(prose, GEN_REPEAT):
        sentence = "이 내용은 꼭 기억해 두시면 앞으로 큰 도움이 됩니다."
        rows[index] = _with_answer(rows[index], _answer(items[index]) + ("\n" + sentence) * 4)
    for index in take(prose, GEN_BROKEN):
        rows[index] = _with_answer(rows[index], _answer(items[index]).replace(" ", " \ufffd", 1))
    for index in take(prose, GEN_EMPTY_ANSWER):
        rows[index] = _with_answer(rows[index], "")
    for offset, index in enumerate(take(prose, GEN_PII)):
        contact = f"문의는 help{offset + 1}@example.com으로 해 주세요."
        rows[index] = _with_user(rows[index], lambda text, c=contact: f"{text}\n{c}")
    long_index = take(by_task["math"], GEN_LONG)[0]
    # 코드가 아닌 모든 응답(여러 턴 대화의 응답까지)을 이어 6,000자를 넘긴다(같은 문장이 되풀이되지 않게).
    long_text = "\n\n".join(
        message["content"]
        for item in items
        if item["task"] != "code"
        for message in item["messages"]
        if message["role"] == "assistant"
    )
    rows[long_index] = _with_answer(rows[long_index], long_text)
    clean = [i for i in single if i not in used]
    for index in rng.sample(clean, GEN_DUPLICATES):
        added.append(dict(rows[index]))
    for index in rng.sample(clean, GEN_EMPTY_PROMPT):
        added.append(_with_user(rows[index], lambda _text: ""))
    for index in rng.sample(clean, GEN_STRUCTURE):
        item = rows[index]
        added.append({**item, "messages": [item["messages"][0], *item["messages"]]})
    for index in rng.sample(clean, GEN_NEAR):
        added.append(_with_user(rows[index], lambda text: "간단히 답해 줘. " + text))
    final = rows + added
    rng.shuffle(final)

    checks: dict[str, int] = {}
    for item in final:
        for key in generation_rules.measure(item["messages"]):
            checks[key] = checks.get(key, 0) + 1
    hashes = [context_hash(item["messages"][:-1]) for item in final]
    checks["duplicate"] = len(hashes) - len(set(hashes))
    GENERATION_OUT.mkdir(parents=True, exist_ok=True)
    with (GENERATION_OUT / "sft.jsonl").open("w", encoding="utf-8") as file:
        for item in final:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
    # 심은 문제는 실제로 잰 수로 보인다(심은 것끼리 겹쳐 생긴 것도 센다. 예: 빈 지시 사본끼리 같은 지시).
    names = {
        "duplicate": "같은 지시",
        "empty_prompt": "빈 지시",
        "structure": "대화 구조",
        "pii": "개인정보",
        "empty_answer": "빈 응답",
        "truncated": "잘린 응답",
        "code_translated": "번역된 코드",
        "loop": "반복 루프",
        "refusal": "거절 · 상투구",
        "short": "짧은 응답",
        "repeat": "반복 문장",
        "lang_mix": "언어 섞임",
        "broken": "깨진 글자",
        "long": "긴 응답",
    }
    planted = [(name, checks[key]) for key, name in names.items() if checks.get(key)]
    planted += [("근접 중복", GEN_NEAR), ("지시 불일치", GEN_MISMATCH * 2)]
    write_answers(
        GENERATION_OUT / "sft.answers.json",
        {
            "key": "sft",
            "rows": len(final),
            "planted": [{"name": name, "count": count, "unit": "건"} for name, count in planted],
            "checks": dict(sorted(checks.items())),
            "semantic": {"near_duplicate": GEN_NEAR, "mismatch": GEN_MISMATCH * 2},
        },
    )
    print(f"sft.jsonl {len(final)}줄 · 검사 {checks}")


def build_gen_chat() -> None:
    """달라마켓 상담 여러 턴 대화: 문제가 없는 대조군(ShareGPT 모양 jsonl)."""
    items = read_jsonl(CONTENT / "gen_chat" / "chat.jsonl")
    roles = {"system": "system", "human": "user", "gpt": "assistant"}
    for item in items:
        messages = [
            {"role": roles[turn["from"]], "content": turn["value"]}
            for turn in item["conversations"]
        ]
        if generation_rules.measure(messages):
            sys.exit("대화 원고에 규칙 문제가 있습니다.")
    rng = random.Random(SEED)
    rows = list(items)
    rng.shuffle(rows)
    GENERATION_OUT.mkdir(parents=True, exist_ok=True)
    with (GENERATION_OUT / "chat.jsonl").open("w", encoding="utf-8") as file:
        for item in rows:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_answers(
        GENERATION_OUT / "chat.answers.json",
        {"key": "chat", "rows": len(rows), "planted": [], "checks": {}, "semantic": {}},
    )
    print(f"chat.jsonl {len(rows)}줄")


# ---------- 생성: 지시만 · 도구 호출 · 선호 쌍 ----------

GEN_PROMPT_DUPLICATES = 2  # 똑같은 지시 사본 (같은 지시 여분)
GEN_PROMPT_PII = 1  # 예약된 이메일을 넣은 지시

GEN_TOOL_BAD_JSON = 2  # 인자 JSON을 깨뜨린 호출
GEN_TOOL_UNDEFINED = 2  # 정의에 없는 도구 이름으로 바꾼 호출
GEN_TOOL_MISSING_ARG = 1  # 꼭 있어야 할 인자를 뺀 호출
GEN_TOOL_WRONG_TYPE = 1  # 정수 인자를 글로 바꾼 호출

GEN_PAIR_SAME = 2  # 고른 응답과 버린 응답이 같은 쌍
GEN_PAIR_TIE = 2  # 두 점수가 같은 쌍 (동점)
GEN_PAIR_REVERSED = 3  # 엉뚱한 응답을 고른 쪽에 둔 쌍 (판정 반대, Jev가 잡는다)

# 버린 응답으로 쓰는 흐릿한 답 (지시에 답하지 않는다)
GEN_VAGUE = (
    "좋은 질문입니다. 상황에 따라 다르니 직접 확인해 보시는 것이 좋겠습니다.",
    "그 부분은 여러 가지를 함께 따져 봐야 해서 한마디로 말하기 어렵습니다.",
    "말씀하신 내용은 잘 알겠습니다. 필요하면 다시 물어봐 주세요.",
)


def build_gen_prompts() -> None:
    """지시만 데이터: 응답 없이 지시만 있는 Alpaca 모양(응답 만들기를 해 볼 입구 1). 같은 지시 · 개인정보를 조금 심는다."""
    items = read_jsonl(CONTENT / "gen_prompts" / "prompts.jsonl")
    rng = random.Random(SEED)
    rows = [dict(item) for item in items]
    for index in rng.sample(range(len(rows)), GEN_PROMPT_PII):
        rows[index] = {
            **rows[index],
            "instruction": rows[index]["instruction"] + " 답은 help@example.com으로도 보내 줘.",
        }
    rows += [dict(rows[index]) for index in rng.sample(range(len(items)), GEN_PROMPT_DUPLICATES)]
    rng.shuffle(rows)

    def user_text(row: dict[str, Any]) -> str:
        return row["instruction"] + (f"\n\n{row['input']}" if row.get("input") else "")

    checks: dict[str, int] = {"no_answer": len(rows)}
    for row in rows:
        for key in generation_rules.measure([{"role": "user", "content": user_text(row)}]):
            checks[key] = checks.get(key, 0) + 1
    hashes = [context_hash([{"role": "user", "content": user_text(row)}]) for row in rows]
    checks["duplicate"] = len(hashes) - len(set(hashes))
    GENERATION_OUT.mkdir(parents=True, exist_ok=True)
    with (GENERATION_OUT / "prompts.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(
                json.dumps(
                    {key: value for key, value in row.items() if key != "task"}, ensure_ascii=False
                )
                + "\n"
            )
    planted = [
        ("응답 없음", checks["no_answer"]),
        ("같은 지시", checks["duplicate"]),
        ("개인정보", checks.get("pii", 0)),
    ]
    write_answers(
        GENERATION_OUT / "prompts.answers.json",
        {
            "key": "prompts",
            "rows": len(rows),
            "planted": [
                {"name": name, "count": count, "unit": "건"} for name, count in planted if count
            ],
            "checks": dict(sorted(checks.items())),
            "semantic": {},
        },
    )
    print(f"prompts.jsonl {len(rows)}줄 · 검사 {checks}")


def build_gen_tools() -> None:
    """도구 호출 대화: 달라마켓 상담 도구(주문 · 택배 · 상품 · 매장 · 취소)를 부르는 messages + tools. 호출 형식 문제를 심는다."""
    items = read_jsonl(CONTENT / "gen_tools" / "tools.jsonl")
    for item in items:
        if generation_rules.measure(item["messages"], tools=item["tools"]):
            sys.exit("도구 원고에 규칙 문제가 있습니다.")
    rng = random.Random(SEED)
    rows = [json.loads(json.dumps(item, ensure_ascii=False)) for item in items]
    with_calls = [
        index for index, row in enumerate(rows) if any(m.get("tool_calls") for m in row["messages"])
    ]
    used: set[int] = set()

    def first_call(row: dict[str, Any]) -> dict[str, Any]:
        return next(m for m in row["messages"] if m.get("tool_calls"))["tool_calls"][0]["function"]

    def pick(count: int, fits: Any = None) -> list[int]:
        free = [
            index
            for index in with_calls
            if index not in used and (fits is None or fits(rows[index]))
        ]
        picked = rng.sample(free, count)
        used.update(picked)
        return picked

    for index in pick(GEN_TOOL_BAD_JSON):
        function = first_call(rows[index])
        function["arguments"] = function["arguments"][:-1]
    for index in pick(GEN_TOOL_UNDEFINED):
        function = first_call(rows[index])
        function["name"] = function["name"].replace("get_", "lookup_").replace("_", "", 1) + "_v2"
    for index in pick(
        GEN_TOOL_MISSING_ARG, lambda row: first_call(row)["name"] != "search_products"
    ):
        first_call(rows[index])["arguments"] = "{}"

    def has_price(row: dict[str, Any]) -> bool:
        return '"max_price"' in first_call(row)["arguments"]

    for index in pick(GEN_TOOL_WRONG_TYPE, has_price):
        function = first_call(rows[index])
        arguments = json.loads(function["arguments"])
        arguments["max_price"] = f"{arguments['max_price']}원"
        function["arguments"] = json.dumps(arguments, ensure_ascii=False)
    rng.shuffle(rows)
    checks: dict[str, int] = {}
    for row in rows:
        for key in generation_rules.measure(row["messages"], tools=row["tools"]):
            checks[key] = checks.get(key, 0) + 1
    GENERATION_OUT.mkdir(parents=True, exist_ok=True)
    with (GENERATION_OUT / "tools.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_answers(
        GENERATION_OUT / "tools.answers.json",
        {
            "key": "tools",
            "rows": len(rows),
            "planted": [
                {"name": "도구 호출 형식", "count": checks.get("tool_call", 0), "unit": "건"}
            ],
            "checks": dict(sorted(checks.items())),
            "semantic": {},
        },
    )
    print(f"tools.jsonl {len(rows)}줄 · 검사 {checks}")


def build_gen_pref() -> None:
    """선호 쌍: SFT 원고의 한 턴 대화로 고른 응답(원고의 답) · 버린 응답(다른 과제의 답 · 흐릿한 답 · 앞부분만)을 만든다.

    TRL 선호 모양(chosen · rejected 대화 목록)에 원본 점수(score_chosen · score_rejected)를 함께 둔다.
    같은 쌍 · 동점 · 판정 반대(엉뚱한 답을 고른 쪽에 둠, 뜻 분석의 Jev가 잡는다)를 심는다.
    """
    items = [
        item for item in read_jsonl(CONTENT / "gen_sft" / "sft.jsonl") if len(item["messages"]) == 2
    ]
    rng = random.Random(SEED)
    rows: list[dict[str, Any]] = []
    kinds = ("other", "vague", "partial")
    for index, item in enumerate(items):
        prompt = item["messages"][:-1]
        good = _answer(item)
        kind = kinds[index % len(kinds)]
        if kind == "other":
            bad = _answer(rng.choice([x for x in items if x["task"] != item["task"]]))
        elif kind == "vague":
            bad = GEN_VAGUE[index % len(GEN_VAGUE)]
        else:
            bad = good.split("\n")[0].split(". ")[0].rstrip(".") + "."
        if bad.strip() == good.strip():
            bad = GEN_VAGUE[index % len(GEN_VAGUE)]
        rows.append(
            {
                "chosen": [*prompt, {"role": "assistant", "content": good}],
                "rejected": [*prompt, {"role": "assistant", "content": bad}],
                "score_chosen": 8.0 + (index % 3) * 0.5,
                "score_rejected": 3.0 + (index % 4) * 0.5,
                "kind": kind,
            }
        )
    order = list(range(len(rows)))
    rng.shuffle(order)
    same, tie, reversed_ = (
        order[:GEN_PAIR_SAME],
        order[GEN_PAIR_SAME : GEN_PAIR_SAME + GEN_PAIR_TIE],
        [i for i in order[GEN_PAIR_SAME + GEN_PAIR_TIE :] if rows[i]["kind"] == "other"][
            :GEN_PAIR_REVERSED
        ],
    )
    for index in same:
        rows[index]["rejected"] = [dict(m) for m in rows[index]["chosen"]]
    for index in tie:
        rows[index]["score_rejected"] = rows[index]["score_chosen"]
    for index in reversed_:
        row = rows[index]
        row["chosen"], row["rejected"] = row["rejected"], row["chosen"]
        row["score_chosen"], row["score_rejected"] = row["score_rejected"], row["score_chosen"]
    rng.shuffle(rows)
    if any(generation_rules.measure(row["chosen"]) for row in rows):
        sys.exit("선호 쌍의 고른 응답에 규칙 문제가 있습니다.")
    checks: dict[str, int] = {}
    for row in rows:
        chosen, rejected = row["chosen"][-1]["content"], row["rejected"][-1]["content"]
        marks = generation_rules.measure_pair(
            chosen, rejected, chosen_score=row["score_chosen"], rejected_score=row["score_rejected"]
        )
        for key in marks:
            checks[key] = checks.get(key, 0) + 1
        if len(chosen) > len(rejected):
            checks["length_bias"] = checks.get("length_bias", 0) + 1
    GENERATION_OUT.mkdir(parents=True, exist_ok=True)
    with (GENERATION_OUT / "pref.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(
                json.dumps({k: v for k, v in row.items() if k != "kind"}, ensure_ascii=False) + "\n"
            )
    write_answers(
        GENERATION_OUT / "pref.answers.json",
        {
            "key": "pref",
            "rows": len(rows),
            "planted": [
                {"name": "같은 쌍 · 동점", "count": checks.get("same_pair", 0), "unit": "쌍"},
                {"name": "판정 반대", "count": len(reversed_), "unit": "쌍"},
            ],
            "checks": dict(sorted(checks.items())),
            "semantic": {"judge_reversed": len(reversed_)},
        },
    )
    print(f"pref.jsonl {len(rows)}줄 · 검사 {checks} · 판정 반대 {len(reversed_)}")


BUILDERS = {
    "topics": build_topics,
    "intents": build_intents,
    "encyclopedia": build_encyclopedia,
    "reading": build_reading,
    "triplets": build_triplets,
    "company": build_company,
    "sft": build_gen_sft,
    "chat": build_gen_chat,
    "prompts": build_gen_prompts,
    "tools": build_gen_tools,
    "pref": build_gen_pref,
}


def main() -> None:
    """모두 만들거나, 열쇠를 주면 그것만 만든다: build.py topics reading"""
    keys = sys.argv[1:] or list(BUILDERS)
    for key in keys:
        BUILDERS[key]()


if __name__ == "__main__":
    main()
