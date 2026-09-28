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


BUILDERS = {
    "topics": build_topics,
    "intents": build_intents,
    "encyclopedia": build_encyclopedia,
    "reading": build_reading,
    "triplets": build_triplets,
    "company": build_company,
}


def main() -> None:
    """모두 만들거나, 열쇠를 주면 그것만 만든다: build.py topics reading"""
    keys = sys.argv[1:] or list(BUILDERS)
    for key in keys:
        BUILDERS[key]()


if __name__ == "__main__":
    main()
