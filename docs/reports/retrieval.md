<div align="center">

# DENT 기술 보고서: 검색 학습 데이터 정제

임베딩 검색 모델의 학습 데이터(질의 · 문서 · 정답 · 하드 네거티브)의 품질 문제는 모델을 어떻게 해치며, DENT는 왜 이 방법과 이 순서로 고치는가

[요약](#요약) · [서론](#1-서론) · [데이터와 검사](#2-데이터와-검사) · [문제와 피해](#3-문제마다-그대로-두면-무엇이-나빠지나) · [정제 과정](#4-정제-과정과-그-근거) · [설계 값](#5-설계-값과-그-출처) · [한계](#6-한계와-논의) · [참고 문헌](#참고-문헌)

[분류 보고서](classification.md) · [생성 보고서](generation.md) · [README](../../README.md)

</div>

| 항목 | 내용 |
|---|---|
| 대상 | DENT 0.1.0 검색 모듈(`retrieval`) |
| 데이터 | 질의 · 문서 · 판정(질의와 문서 한 쌍마다 정답 1~3 또는 오답 0) |
| 작성일 | 2026-10-12 |

---

## 요약

임베딩 검색 모델(dense retriever)은 질의와 정답 문서를 가깝게, 오답 문서를 멀게 놓도록 대조 학습한다. 그래서 오답 자리에 사실은
정답인 문서(거짓 오답)가 있으면 맞는 문서를 밀어내도록 배우고, 너무 쉬운 오답만 있으면 배울 것이 없고, 질의가 문서를 그대로 베끼면
낱말 일치라는 지름길만 배운다. 문서 쪽에서도 긴 문서는 잘리고, 여러 문서에 되풀이된 상용구는 문서 표현을 흐린다.

DENT의 검색 모듈은 이 문제들을 **문서 → 질의 → 하드 네거티브** 세 단계의 열아홉 가지 검사로 재고, 앞 단계의 결과가 뒤 단계의 입력이 되는 순서로 고친다.
이 보고서는 검사마다 그대로 두면 무엇이 나빠지는지의 실증을, 과정의 원칙마다 그렇게 하지 않으면 무엇이 잘못되는지의 근거를 문헌에서 모았다.
검색은 세 모듈 가운데 직접 실증이 가장 많다. 특히 거짓 오답 · 빠진 정답 · 쉬운 오답 · 합성 질의 거르기는 dense 검색에서 잰 수치가 있다.

용어: **판정 모델**은 한 건마다 판정의 확률을 주는 모델(Jev)이다. **도우미**는 진단을 읽고 흐름 순서대로 규칙 · 판정 모델 · LLM으로 고치는 기능이고(채팅이 아니다),
끝난 뒤 남은 문제는 사람이 골라 **AI로 고치기**로 고친다. **문**은 새 데이터를 만들거나 다음 단계로 넘어가기 전에 앞 단계의 검사를 다시 재는 관문이다.
고친 것은 모두 기록하고 되돌릴 수 있다.

---

## 1. 서론

### 1.1 대조 학습이 데이터에 민감한 이유

검색 모델은 질의 q, 정답 d⁺, 오답 d⁻₁…d⁻ₙ의 묶음에서 q와 d⁺의 유사도를 오답들보다 높이도록 학습한다(InfoNCE 계열 손실).
이 손실은 세 가지를 데이터에 기댄다.

| 기대는 것 | 데이터가 틀리면 |
|---|---|
| d⁺가 정말 정답이다 | 엉뚱한 문서를 끌어당긴다 |
| d⁻가 정말 오답이다 | 맞는 문서를 밀어낸다(거짓 오답) |
| d⁻가 q와 충분히 가깝다 | 손실이 0에 가까워 배울 것이 없다(쉬운 오답) |

그래서 검색 데이터의 정제는 대부분 이 세 가지를 바로 세우는 일이고, 문서 정리와 질의 정리는 그 앞 단계다.

### 1.2 근거의 등급

| 표시 | 뜻 |
|---|---|
| **[직접]** | dense 검색 모델의 학습 · 평가에서 잰 실증이 있다 |
| **[간접]** | 다른 검색 방식(BM25 같은 어휘 검색 · 교차 인코더 재순위기)이나 끝단 질의응답에서 잰 실증을 빌린다 |
| **[판단]** | 학술 근거를 찾지 못했고, 기전에 기댄 공학적 판단이다 |

---

## 2. 데이터와 검사

### 2.1 데이터 모양과 흐름

원본 모양은 다섯 가지(문서만 · 질의와 정답 · 질문과 지문과 답 · 질의와 정답과 오답 · 질의와 문서와 점수)이고, 모양에 따라 흐름의 입구가 정해진다.

```
1 문서 ──(질의 만들기)──▶ 2 질의 ──(오답 찾기)──▶ 3 하드 네거티브 ──▶ 내보내기
```

문서의 본문은 바꾸지 않는다. 학습과 색인에 쓰는 **학습 글**은 머리말(제목 › 머리말 칸 › 구획 경로)과, 떼기로 고른 반복 구간을 뺀 본문으로 따로 만든다.
임베딩 · 오답 찾기 · 판정 모델 · 질의 만들기 · 내보내기가 모두 이 학습 글을 쓴다.

### 2.2 열아홉 가지 검사

| 단계 | 검사 | 정의 | 등급 |
|---|---|---|---|
| 1 문서 | 깨진 글자 | 대체 문자(U+FFFD) · 모지바케 · 제어 문자 · HTML 찌꺼기 | 있으면 주의 |
| | 반복 구간 | 문서의 0.5%(최소 3문서) 이상에 되풀이되는 문장 틀(숫자만 다른 것은 한 틀) · 3문서 이상의 메타 모양(이메일 · 웹 주소 · 전화 · 저작권 표시 · 전재 금지) | 고를 틀이 있거나 살피기 전이면 주의 |
| | 긴 문서 | 학습 글이 문서 최대 토큰(기본 512)을 넘음 | 5% 미만 통과 · 20% 미만 주의 · 그 위 심각 |
| | 중복 문서 | 학습 글이 같은 문서(근접 중복 ≥ 0.95 묶음은 수만 보임) | 있으면 주의 |
| | 질의 안 만들 청크 | 목차 · 표만 · 서명란처럼 짧은 · 빈 · 반복 구간뿐인 청크 | 있으면 주의 |
| | 주제 쏠림 | 문서 KMeans 8무리 중 가장 큰 무리 ≥ 40% | 주의 |
| 2 질의 | 질의 없음 | 질의가 하나도 없음 | 심각 |
| | 짧은 · 긴 질의 | 5자 미만 또는 질의 최대 토큰(기본 64)을 넘음 | 있으면 주의 |
| | 판정 충돌 | 같은 쌍이 원본에서 정답이자 오답 | 있으면 주의 |
| | 질의 중복 | 같은 글의 질의 여분 | 2% 미만 통과 · 그 위 주의 |
| | 정답 없음 | 정답이 하나도 없는 질의 | 있으면 심각 |
| | 정답 의심 | 정답이 기준 검색 100위 밖 | 1% 미만 통과 · 3% 미만 주의 · 그 위 심각 |
| | 문맥 의존 | "이 조항에서 …"처럼 문서 없이는 뜻이 통하지 않는 질의 | 1% 미만 통과 · 5% 미만 주의 · 그 위 심각 |
| | 쉬운 쌍 | 질의가 정답 문서의 글을 거의 그대로 베낌(글자 두 개 묶음 겹침 ≥ 0.9) | 상한(기본 30%)을 넘으면 주의 |
| | 빠진 정답 | 판정이 없는 상위 문서인데 판정 모델이 정답(≥ 0.8)으로 본 것 | 있으면 주의 |
| 3 하드 네거티브 | 거짓 오답 | 오답인데 정답 유사도 × 0.95보다 가까움 | 있으면 주의 · 판정 모델이 확인한 것이 있으면 심각 |
| | 정답과 같은 오답 | 정답과 같은 본문 · 같은 중복 묶음의 오답 | 있으면 주의 |
| | 하드 오답 | 순위를 아는 오답 가운데 기준 검색 200위(찾기 범위의 끝) 안의 비율 | 50% 이상 통과 · 아래 주의 |
| | 오답 부족 | 정답이 있는 질의의 오답이 오답 수(기본 7, 학습 묶음 8)보다 적음 | 있으면 주의 |

질의가 필요한 문서(정답으로 쓰인 적도, 만든 질의도, 원본 판정도 없는 문서)는 검사가 아니라 흐름의 '질의 만들기' 할 일로 보인다.
문서의 토큰(긴 문서 · 청크 크기)은 학습할 모델을 정하면 그 토크나이저로 세고, 정하지 않았거나 토크나이저를 받을 수 없으면 글자 수로 어림한다(6장).
'회사 규정' 예시에서 글자 수 어림이 놓친 긴 문서 7개를 토크나이저로 세면 모두 잡았다. **[관찰]**

---

## 3. 문제마다: 그대로 두면 무엇이 나빠지나

### 3.1 거짓 오답

**기전.** 대조 손실은 오답을 질의에서 밀어낸다. 오답 자리의 문서가 사실 정답이면 맞는 문서를 밀어내도록 배운다.
가까운 오답일수록 하드 네거티브로 값지지만, 그만큼 거짓 오답일 확률도 높다.

**근거.**
- MS MARCO에서 거르지 않은 하드 네거티브로 학습하면 MRR@10이 26.03으로, 같은 묶음 안의 다른 질의 정답을 오답으로 쓴 기본 설정(32.39)보다도 낮았다.
  교차 인코더로 거짓 오답을 거른 하드 네거티브는 36.38이었다[1]. **[직접]**
- 상위 K개를 그대로 오답으로 고르면 거짓 오답 비율이 NQ 38.8%, StackExchange 47%였다(LLM 판정).
  오답 점수가 정답 점수의 95%를 넘으면 빼는 방식이 BEIR 세 데이터셋(NQ · HotpotQA · FiQA) 평균 nDCG@10을 0.5407 → 0.5856으로 올려 비교한 방식 가운데 가장 좋았다[2]. **[직접]**
- 더 강한 검색기에서 오답을 뽑으면 오히려 나빠지는 현상이 거짓 오답 때문이었다[3]. **[간접]**

**한계.** 고정된 절대 문턱은 정답 점수에 맞춘 상대 문턱보다 못했다[2]. 지나치게 거르면 정보 있는 오답까지 버린다.

### 3.2 빠진 정답

**기전.** 판정이 얕은 데이터에서는 실제 정답이 판정되지 않은 채 남는다. 오답 찾기는 판정이 없는 상위 문서를 오답으로 고르므로,
빠진 정답은 그대로 거짓 오답이 된다. 평가에서는 맞게 찾은 문서가 틀린 것으로 매겨진다.

**근거.**
- MS MARCO 질문 100개에서 정답 표시가 없는 상위 지문을 사람이 보니 약 70%가 실제 정답이거나 매우 관련이 있었다[1]. **[직접]**
- MS MARCO 질의의 94%가 정답이 하나뿐이고, 평가자들은 신경 순위기의 1위를 표시된 정답보다 더 자주 골랐다[4]. **[직접]**
- LLM으로 다시 판정하니 MS MARCO 학습 쌍의 56%에 거짓 오답이 하나 이상 있었고, 그것을 정답으로 고쳐 달면 BEIR nDCG@10이 0.508 → 0.515로 올랐다.
  같은 문서를 빼기만 한 것(0.511)보다 정답으로 고쳐 단 것이 나았다[5]. **[직접]**

**한계.** 위 연구에서 LLM 판정과 사람 판정의 일치도는 κ 0.32~0.39로 낮았다[5]. 판정 모델의 '빠진 정답'은 제안으로 두는 것이 맞다.
DENT의 도우미는 판정 모델이 확인한 제안만 받고, 확인되지 않은 것은 끝난 뒤 사람이 고른 AI로 고치기에서 LLM이 정한다.

### 3.3 쉬운 오답 · 오답 부족

**기전.** 질의와 먼 오답은 손실이 거의 0이라 기울기도 0에 가깝다. 기울기 분산이 커지고 수렴이 느려진다.

**근거.**
- 정보 없는 오답은 기울기 노름이 작아 학습이 느리고 불안정하다는 이론과 실측. MS MARCO MRR@10은 무작위 오답 0.261, BM25 오답 0.299,
  학습 중인 모델의 근사 이웃에서 뽑은 오답 0.330이었다[6]. **[직접]**
- NQ에서 묶음 안 오답 127개에 BM25 오답 하나만 더해도 top-5 정확도가 55.8 → 65.8로 올랐다[7]. **[직접]**

**한계.** 고정된 하드 네거티브만 쓰면 학습이 불안정해 무작위 오답을 섞은 연구도 있다[8]. DENT는 질의마다 오답 풀(기본 20)을 넓게 찾아 두고, 학습 jsonl에는 풀 전체를, 학습 표에는 가장 하드한 n개를 싣는다.

### 3.4 쉬운 쌍

**기전.** 질의가 정답 문서의 문장을 거의 그대로 베끼면 모델은 뜻이 아니라 낱말 일치를 배운다. 실제 사용자는 문서와 다른 말로 묻는다.

**근거.**
- 질문을 지문을 보고 쓴 SQuAD는 질문과 지문의 낱말 겹침이 인위적으로 커서, dense 검색 시스템(20.2)이 BM25 시스템(33.2)에 끝단 정확도(EM)로 크게 뒤졌다.
  사용자가 지문을 보지 않고 쓴 NQ에서는 반대였다(33.3 대 26.5)[9]. **[간접]**
- dense 검색기들은 정답 여부보다 글자 그대로의 일치를 선호하는 편향을 보였다[10]. **[직접]**

**한계.** 겹침을 모두 없애도 나쁘다. 지문에서 질의를 만들 때 겹치는 구간을 늘 빼면 56.8, 늘 남기면 61.1, 섞으면 62.3이었다(NQ top-20)[11].
DENT가 쉬운 쌍을 지우지 않고 **상한**(기본 30%)만 두는 이유다.

### 3.5 긴 문서와 문서 나누기

**기전.** 인코더는 최대 길이 뒤를 잘라 버린다. 길이 안에 들어오더라도 한 벡터에 여러 주제가 섞이고, 임베딩은 문서 앞부분을 더 담는다.

**근거.**
- 고정 길이 벡터로는 문서가 길수록 구별에 필요한 차원이 커진다는 이론과, 문서가 길수록 dense 검색의 Recall@1이 떨어져 BM25에 뒤지는 실험[12]. **[직접]**
- 임베딩 모델은 문서 앞부분을 더 담고, 미세 조정이 이 편향을 키운다[13]. dense 검색기들은 짧은 문서 · 앞 위치를 선호했고,
  이 편향들이 겹치면 정답 문서를 고르는 비율이 10% 아래로 떨어졌다[10]. **[직접]**
- 문서를 지문으로 나눠 가장 높은 지문의 점수를 쓰면(MaxP) 문서 앞부분만 쓰는 것(FirstP)보다 나았다(Robust04 설명(description) 질의 nDCG@20 0.491 → 0.529)[14]. **[간접]**
- 지문 단위로 나누되 겹치게 나눈 창이 EM +4.7을 냈다[15]. 검색 단위만 바꿔도 비지도 dense 검색기의 Recall@5가 43.0 → 52.7로 올랐다[16]. **[간접] · [직접]**

**한계.** 어디에나 맞는 청크 크기는 없다. 지도 학습한 검색기에서는 잘게 나눈 단위의 이득이 줄어 학습에 쓴 데이터셋 일부에서는 오히려 약간 나빴다[16].
오버랩은 같은 글이 여러 청크에 들어 효율(IoU)을 떨어뜨리고, 작은 임베딩 모델의 한 분할 방식에서는 재현율을 올렸지만 큰 모델에서는 그렇지 않았다[17].
청크는 주변 맥락을 잃는다[18]. DENT가 청크마다 구획 경로를 머리말로 붙이고, 오버랩을 기본으로 두지 않고 선택지로 두는 이유다.
청크에 맥락 글을 붙이면 검색 실패가 줄었다는 보고[19]가 머리말의 근거다.

### 3.6 반복 구간(상용구)

**기전.** 여러 문서에 똑같이 붙은 서명 · 안내 · 저작권 문구는 본문과 상관없는 글이 문서 표현에 섞이게 한다.
같은 문구를 공유한 문서끼리 서로 비슷해지고, 질의에 맞는 문서가 흐려진다.

**근거.**
- 상용구를 지우니 웹 문서 검색(질의 우도 모델)의 nDCG가 0.459 → 0.578로 올랐다[20]. **[간접]**
- 웹 데이터 정제에서 문단을 정규화해(숫자를 자리표시로 바꾸는 것 포함) 해시로 되풀이 문단을 지우면 메뉴 · 쿠키 경고 · 연락처 같은 상용구가 사라졌다[21].
  DENT가 숫자만 다른 문장을 한 틀로 묶는 것과 같은 생각이다. **[간접]**

**한계.** 재현율이 낮은 추출기가 본문까지 지워 검색이 오히려 나빠졌다는 보고가 같은 연구에 있다[20]. 상용구가 dense 임베딩을 서로 끌어당긴다는
직접 실증은 찾지 못했다. 그래서 DENT는 본문을 두고 학습 글에서만 빼며, 떼기 · 남김은 사람이 고른다.

### 3.7 중복 문서

**기전.** 정답의 사본이 다른 질의의 오답이나 같은 질의의 오답으로 들어가면 거짓 오답이 된다. 평가에서는 같은 내용을 여러 번 찾은 것이 점수를 부풀린다.

**근거.**
- 평가에서 중복을 할인하지 않으면 점수가 부풀고, 중복을 걸러 내는 시스템은 최악의 경우 순위가 8~53단계 내려갔다[22]. **[간접]**
- TREC 2004 Terabyte 트랙에 제출된 런의 정답 문서 가운데 16.6%가 내용이 같은 중복이었다[23]. **[간접]**

**한계.** 학습 데이터의 중복 문서를 지운 효과를 dense 검색에서 잰 수치는 찾지 못했다. **[판단]**

### 3.8 문맥 의존 질의와 정답 없는 질의

**기전.** "이 조항에서 …"처럼 문서를 봐야만 뜻이 통하는 질의는 검색 상황에서는 풀 수 없는 학습 쌍이다. 정답 없는 질의는 학습 신호가 없다.

**근거.**
- 앞 문맥이 필요한 대화형 질의 평가에서 가장 좋은 자동 런도 NDCG@3 0.436에 그쳤고, 사람이 문맥 없이 서도록 고친 질의를 쓴 가장 좋은 런은 0.589였다[24]. **[간접]**
- 대량으로 만든 질문을 지문을 보고 거르면 문맥에 기대는 질문이 남아, 질문만 보고 거른 것보다 끝단 정확도가 낮았다(EM 28.3 대 38.6)[25]. **[간접]**

**한계.** 합성 학습 쌍의 문맥 의존을 dense 검색에서 따로 잰 실증은 없다. 문맥 의존 질의를 혼자 서는 질의로 고쳐 쓰는 방법은 [26][27]을 따른다.

### 3.9 질의 없는 문서와 합성 질의

**기전.** 문서만 있는 데이터는 질의가 없어 학습할 수 없다. LLM이 만든 질의로 학습 쌍을 만들 수 있지만, 거르지 않으면 질 낮은 질의가 섞인다.

**근거.**
- LLM이 문서마다 만든 질의로 학습한 검색기가 BEIR 11개 평균 nDCG@10 47.8로, MS MARCO로 학습한 큰 모델(44.9)보다 높았다.
  만든 질의로 원 문서를 되찾지 못하면 버리는 되찾기 거르기가 11개 중 8개에서 평균 +2.5를 냈다[28]. **[직접]**
- 합성 질의를 거르지 않고 모두 쓰면 상위만 고른 것보다 MRR@10이 4점 낮았다[29]. 작은 재순위기는 일관성 검사(되찾기 거르기) 없이는 여러 데이터셋에서 BM25보다 낮았다[30]. **[간접]**
- 생성 질의와 교차 인코더 점수로 도메인에 맞춘 검색기가 도메인 6개 평균 nDCG@10을 45.2 → 51.5로 올렸다[31]. **[직접]**

**한계.** 되찾기 거르기는 생성 질의가 가장 적은 데이터셋에서 오히려 해가 됐다[28]. 합성 질의에 하드 네거티브를 붙이면 거짓 오답에 민감해졌다[31].
DENT가 만든 질의의 오답도 거짓 오답 검사를 똑같이 거치게 하는 이유다.

### 3.10 깨진 글자와 질의 안 만들 청크

**근거.** 어휘 검색에서는 낱말 오류율 5%부터 성능이 유의하게 떨어졌다[32]. dense 검색(BERT 이중 인코더)은 오타가 든 질의에서 MRR@10이 0.296 → 0.141로 떨어져, 오타 없는 질의의 BM25(0.187)보다 낮아졌다[33].
문서 쪽 깨진 글자를 dense에서 잰 실증과, 목차 · 표 · 서명 조각에서 질의를 만들지 않는 효과의 실증은 찾지 못했다. **[간접] · [판단]**

### 3.11 주제 쏠림

**근거.** 다양한 질문으로 학습한 인코더가 드문 개체 질문에서 크게 나았고, 한 관계만 더 학습하면 다른 데이터의 성능이 떨어졌다[34]. **[간접]**
한 데이터셋 안의 주제 쏠림을 직접 재고 고친 실증은 찾지 못했다. DENT는 주의로 보이고 고치기는 데이터를 더하는 사람에게 맡긴다.

### 3.12 교사 점수

정답 · 오답의 0 · 1 라벨 대신 질의와 문서를 함께 읽는 모델(교사)의 점수를 함께 주면 신호가 풍부해진다.
DistilBERT 이중 인코더로 전체 색인을 검색하는 설정에서 교사 하나를 더하니 MRR@10이 0.299 → 0.321(교사 앙상블 0.323)이었고[35],
재순위기 점수로 증류할 때 정적 증류(36.0)보다 함께 학습하며 바뀌는 동적 증류(37.4)가 나았다[36]. **[직접]**
효과는 교사의 질에 기댄다. DENT는 판정 모델의 확률 p를 로짓 log(p/(1−p))로 바꿔 넣는다(학습기가 교사 점수에 softmax를 씌우므로 원래 로짓을 넣는 것과 같다)[37].

---

## 4. 정제 과정과 그 근거

### 4.1 흐름 순서: 앞 단계의 결과가 뒤 단계의 입력

```
1 문서: 글자 · 같은 문서 · 반복 구간 → 문서 나누기(허락) → 청크 고르기
2 질의: 질의 만들기(허락) → 질의 정리 → 정답 확인(판정 충돌 · 정답 없음 · 빠진 정답 · 정답 의심)
3 하드 네거티브: 오답 찾기 → 거짓 오답 확인 → 내보내기
```

**하지 않으면.** 순서를 바꾸면 앞의 결함이 뒤에서 커진다.
- 중복 문서를 묶기 전에 오답을 찾으면 정답의 사본이 오답이 된다(3.7).
- 빠진 정답을 확인하기 전에 오답을 찾으면 실제 정답이 오답이 된다(3.2). 그래서 문서 풀이 늘어 오답을 다시 찾을 때도 정답 확인을 먼저 한다.
- 문서를 나누기 전에 질의를 만들면 긴 문서의 앞부분에서만 질의가 나온다.

**근거.** 여러 오류 탐지 도구를 돌리는 순서가 결과를 크게 바꿨다(순서를 정해 돌리니 모든 결과를 합친 것보다 정밀도 평균 +28%, 재현율 −3.5%)[38]. 데이터 오류는 퍼지기 전에 일찍 잡아야 한다[39].
위의 거짓 오답 · 빠진 정답 실증[1][2][5]이 "정답과 중복을 먼저 바로 세운 뒤 오답을 찾는다"는 순서를 직접 받친다.

### 4.2 싼 판단부터, 두 판단이 같을 때만 바꾼다

| 층 | 맡는 일 |
|---|---|
| 해시 · 규칙 | 같은 질의 · 같은 문서 합치기, 반복 구간 후보 · 메타 모양, 문서 나누기, 질의 안 만들 청크, 문맥 의존 · 쉬운 쌍 모양 |
| 임베딩 | 근접 중복 · 주제 쏠림 · 기준 검색 순위 · 오답 후보(10~200위) |
| 판정 모델 | 빠진 정답 · 거짓 오답 · 정답 의심 확인, 만든 질의 거르기, 교사 점수 |
| LLM | 반복 구간 제안의 까닭 · 문맥 의존 질의 고쳐 쓰기 · 판정 모델도 애매한 판정 · 질의 만들기 |
| 사람 | 반복 구간 떼기 · 남김, 제안 수락, 허락(문서 나누기 · 질의 만들기) |

도우미는 판정 충돌을 풀거나 정답 없는 질의에 정답을 붙이는 것처럼 **정답을 바꾸는 판단**을
판정 모델 하나로 하지 않는다. 도우미 실행에서는 LLM도 같은 답일 때만 바꾸고 엇갈리면 보류한다.
끝난 뒤 사람이 고른 AI로 고치기에서는 판정 모델이 반대로 확신하지 않을 때 LLM의 답을 따르고, 판정 충돌이 엇갈리면 판정을 뗀다.
뜻 분석의 제안(빠진 정답 · 거짓 오답 · 정답 의심)도 도우미는 판정 모델이 확인한 것을 LLM에 다시 묻고 같은 답일 때만 받는다.
LLM에게는 까닭을 먼저 쓰게 하고, 질의가 묻는 바로 그 대상인지(이름이 비슷한 다른 대상이 아닌지) 보게 한다.
'질의와 오답' 예시에서 판정 모델 확인만으로 받으면 37건 가운데 24건이 틀린 정답(왕산악이 만든 현악기에 가야금처럼 이름이 비슷한 문서 등)이었고,
LLM이 같은 답일 때만 받으니 11건 가운데 1건이었다(심은 문제를 고친 질의는 11 → 10). **[관찰]**

**근거.**
- 기준 검색으로 넓게 가져온 뒤 비싼 모델로 다시 매기는 구조가 MRR@10을 크게 올렸다[40]. 넓게 찾고 좁게 확정하는 것이 검색의 기본 구조다.
- 교차 인코더로 거짓 오답을 거른 것이 핵심이었다[1]. 질의와 문서를 함께 읽는 판정 모델이 임베딩 후보를 확인하는 DENT의 구조와 같다.
- LLM 판정과 사람 판정의 일치가 낮았다(κ 0.32~0.39)[5]. 한 판단만으로 정답을 바꾸지 않는 이유다.

### 4.3 학습 글 = 색인 글, 본문은 그대로

반복 구간 떼기와 머리말 붙이기는 본문을 바꾸지 않고 학습 글에서만 한다. 임베딩 · 오답 찾기 · 판정 모델 · 내보내기가 모두 같은 학습 글을 쓰고,
코퍼스 파일에는 학습 글과 함께 정리 규칙(머리말 · 뗀 반복 구간)을 싣는다.

**하지 않으면.** 학습할 때의 문서와 검색할 때 색인하는 문서가 달라지면 학습-서빙 어긋남(training-serving skew)이 생긴다[39].
본문을 직접 고치면 잘못 뗀 구간을 되살릴 수 없다. 규칙 필터가 특정 글을 불균형하게 지운 사례가 있다[41]. **[판단]**에 가깝지만 데이터 문서화 관행[42]과 맞는다.

### 4.4 문과 허락

문서 나누기와 질의 만들기는 허락 단계다. **문**은 허락을 묻기 전에 앞 단계의 검사를 다시 재는 관문이다.
심각(예: 긴 문서가 20% 이상 남음)이 남으면 묻지 않고 막는다. 사람이 문서 나누기를 거절했으면 긴 문서는 문에서 세지 않고,
질의 만들기 허락 카드에 '앞 1,500자만' 경고와 [먼저 나누기]를 둔다.
허락 카드에는 실제 규칙으로 나눠 본 청크 수, 청크 5개로 만든 질의의 시험 결과, 어림 비용을 싣는다.

**하지 않으면.** 긴 문서가 남은 채 질의를 만들면 앞부분 1,500자에서만 질의가 나온다(3.5). 보기로 준 데이터에 결함이 있으면 만든 질의도 그것을 따라가기 쉽다(**[판단]**).

**근거.** LLM이 만든 데이터 가운데 모든 칸이 맞은 것은 54%였다[43]. 합성 질의는 거르지 않으면 나빠졌다[28][29].
허락 전 시험은 이 거르기의 통과율을 사람이 비용과 함께 보고 정하게 하는 장치다. "심각일 때 막는다"는 경계 자체는 **[판단]**이다.

### 4.5 새 항목이 생기면 뜻 분석을 다시 만든다

문서를 나누거나 질의를 만들면 뜻 분석(임베딩 · 근접 중복 · 기준 검색 순위)에 없는 항목이 생긴다. 뜻 분석에 기대는 단계(청크 고르기 · 정답 확인 · 거짓 오답 확인)는
분석을 다시 만든 뒤 그 단계부터 이어 돈다. 문서 풀이 늘면(데이터 추가 · 나누기) 모든 질의의 오답을 다시 찾는다.

**하지 않으면.** 낡은 색인으로 이웃 · 순위를 판단한다. 새 문서가 기존 질의의 정답이어도 모른 채 오답이 될 수 있다.

**근거.** 검색 색인을 30배 드물게 갱신하면 Recall@5가 38.5 → 15.1로 떨어졌다[44]. 색인 갱신이 드물면 하드 네거티브의 질이 떨어지고 학습이 흔들린다[6]. **[간접]**
(두 연구는 학습 중인 모델의 색인이 낡는 문제라, 분석에 새 항목이 빠지는 DENT의 경우는 유추다.)

### 4.6 만든 것은 드러낸다

만든 질의와 그 정답은 출처 `synthetic`, 도우미가 바꾼 판정과 LLM이 고쳐 쓴 원본 질의는 `helper`로 둔다. 내보내는 학습 jsonl과 평가 BEIR의 `queries.jsonl`에는 질의의 출처 칸을 늘 넣는다.
같은 줄에 묶음 칸 `group`도 넣는다. 정답 문서가 같은 원문(같은 본문 · 근접 중복 묶음 · 나누기 전 원문)에서 나왔거나 근접 중복인 질의들의 대표 번호다.
'질문과 지문' 예시(질의 487 · 지문 150)를 학습 80 · 평가 20으로 줄마다 무작위로 나누면 평가 질의의 98%가 학습 쪽과 같은 지문을 정답으로 가졌고,
묶음째 나누면 0%였다. **[관찰]**

**하지 않으면.** 합성 질의가 평가에 섞이면 점수가 부푼다. 합성 질의로 만든 평가셋에서 모든 종류의 시스템이 더 높은 점수를 받았다[45].
AI가 쓴 글은 바꿔 쓰는 공격 앞에서 탐지를 믿기 어려우므로[46], 나중에 가려내기보다 만들 때 표시해야 한다.

---

## 5. 설계 값과 그 출처

| 값 | DENT | 출처 |
|---|---|---|
| 거짓 오답 · 오답 찾기 경계 | 정답 유사도 × 0.95 | 정답 점수의 95%를 넘는 오답을 빼는 방식[2]과 같다 |
| 오답 찾기 범위 | 기준 검색 10~200위, 가까운 것부터 | 근사 이웃 상위 200에서 뽑은 연구[6], FlagEmbedding 오답 찾기의 기본 범위(10~210위)와 거의 같다. 가까운 것부터 고르는 것은 sentence-transformers의 방식과 같다 |
| 오답 수 · 풀 | 오답 7(학습 묶음 8), 풀 20 | **DENT가 정함**(FlagEmbedding 기본은 15) |
| 교사 점수 | 판정 모델 확률의 로짓 | FlagEmbedding이 온도 없이 softmax를 씌우고 재순위기의 원래 로짓을 넣는 것과 같다[37] |
| 판정 모델 확인 | 예 ≥ 0.8 | **DENT가 정함** |
| 근접 중복 | 코사인 ≥ 0.95 | **DENT가 정함** |
| 문서 최대 토큰 | 512 | **DENT가 정함**(BERT 계열의 흔한 입력 상한, bge-m3는 8,192까지 받는다) |
| 청크 오버랩 | 없음 · 32 · 64 · 128토큰(기본 없음) | 겹치는 창이 나았다는 보고[15]와, 효율은 떨어지고 재현율은 모델에 따라 오른다는 보고[17]가 함께 있어 선택지로 둔다 |
| 반복 구간 | 문서의 0.5%(최소 3) · 표본 2만 · 200틀 | **DENT가 정함.** 숫자를 자리표시로 묶는 것은 [21]과 같다 |
| 질의 만들기 | 청크마다 N(기본 2), 후보 2N → N, 되찾기 10위 | 되찾기 거르기는 [28](원 문서 1위)을 따르되 10위로 너그럽게 했다 |
| 질의 유형 · 길이 · 난이도 | 질문형 몫(기본 60%) · 길이 3단 · 난이도 3단 | 길이 · 난이도를 나눠 다양하게 만드는 방식[47]을 따랐다. 원본 질의 3개를 예시로 주는 것은 [29]와 같다 |
| 쉬운 쌍 상한 | 30% | **DENT가 정함.** 상한을 두는 것 자체는 [11]의 결과와 맞는다 |
| 주제 쏠림 | KMeans 8무리 · 40% | 8은 scikit-learn 기본값, 40%는 **DENT가 정함** |
| 기준 검색 지표 | Recall@10 · MRR@10 · nDCG@10(이득 = 등급, 할인 log2(순위 + 1)) | MRR은 [48], MRR@10은 MS MARCO의 공식 지표, nDCG는 [51], nDCG@10은 BEIR의 주 지표[49]. 평가 파일 모양은 BEIR[49] |

---

## 6. 한계와 논의

1. **평가 지표.** 등급이 있는 판정에는 MRR 같은 이진 지표가 모자라[49] nDCG@10을 함께 보인다. 기준 검색은 연결한 임베딩 모델 하나로 잰 값이라,
   학습할 모델의 점수와 다를 수 있다.
2. **평가 정답에 중복 묶음을 넣는 것.** DENT는 내보내는 평가 파일에서 정답의 중복 묶음 전체를 정답으로 두는 것이 기본이다.
   평가 문헌은 오히려 뒤에 나온 중복을 새로운 정보가 없다고 보고 할인하는 쪽이라[22][23], 이 선택의 직접 근거는 없다. **[판단]**
   다른 평가와 견줄 때는 끄고 판정한 문서만 정답으로 낼 수 있다.
3. **판정 모델의 신뢰도.** 도우미는 판정 모델이 확인한 제안도 LLM이 같은 답일 때만 받지만, 두 판정자가 같이 틀리는 경우는 막지 못한다.
   LLM 판정과 사람의 일치가 낮았다는 보고[5]를 보면, 한국어 사람 판정으로 두 판정자의 신뢰도를 다시 재는 일이 남아 있다.
4. **근거의 영역.** 반복 구간 · 깨진 글자 · 중복 문서의 근거는 어휘 검색과 평가 쪽에서 왔다. dense 학습에서 직접 잰 근거는 거짓 오답 · 빠진 정답 · 쉬운 오답 · 합성 질의 · 긴 문서에 있다.
5. **나누지 않는 것.** DENT는 학습 · 평가로 나누지 않는다. 나중에 나누면 같은 원문의 청크 · 같은 청크에서 만든 질의 · 근접 중복을 한쪽에 모아야 한다.
   테스트 질문의 27.5~33.6%가 학습 질문과 거의 같았고, 겹치지 않는 질문에서는 성능이 크게 낮았다는 보고가 있다[50].
   DENT는 나누는 대신 내보내는 파일에 묶음 칸을 넣어(4.6절) 받은 쪽이 묶음째 나누게 한다. 묶음은 정답 문서와 근접 중복으로만 이으므로, 다른 문서를 정답으로 둔 같은 뜻의 질의는 잇지 못할 수 있다.
6. **토큰 세기.** 학습할 모델을 정하지 않았거나 그 토크나이저를 받을 수 없으면(내려받지 못하는 별칭 등) 글자 수로 어림한 토큰으로 잰다.
   언어와 토크나이저에 따라 실제 토큰 수와 차이가 날 수 있다. 질의의 짧은 · 긴 검사는 늘 글자 수로 어림한다.

---

## 7. 결론

검색 데이터의 문제는 대조 학습이 기대는 세 가지(정답은 정답, 오답은 오답, 오답은 충분히 가까움)를 깨뜨려 모델을 해친다.
DENT는 문서 → 질의 → 하드 네거티브 순서로, 앞 단계가 뒤 단계의 입력이 되게 고친다. 정답과 중복을 먼저 바로 세운 뒤 오답을 찾고,
오답은 정답 점수의 95% 경계와 판정 모델로 거르며, 새 항목이 생기면 뜻 분석을 다시 만든 뒤 판단한다.
거짓 오답 · 빠진 정답 · 쉬운 오답 · 합성 질의 거르기는 dense 검색의 직접 실증이 받치고, 반복 구간 · 중복 문서 처리와 여러 숫자 문턱은 간접 근거 또는 공학적 판단이다.

---

## 참고 문헌

본문의 수치는 각 문헌의 본문 또는 초록에서 옮겼다. 학회 발표가 확인되지 않은 것은 arXiv로 적었다.

1. Qu et al. *RocketQA: An Optimized Training Approach to Dense Passage Retrieval for Open-Domain Question Answering*. NAACL 2021. [arXiv:2010.08191](https://arxiv.org/abs/2010.08191)
2. Moreira et al. *NV-Retriever: Improving Text Embedding Models with Effective Hard-Negative Mining*. 2024. [arXiv:2407.15831](https://arxiv.org/abs/2407.15831)
3. Cai et al. *Hard Negatives or False Negatives: Correcting Pooling Bias in Training Neural Ranking Models*. CIKM 2022. [arXiv:2209.05072](https://arxiv.org/abs/2209.05072)
4. Arabzadeh, Vtyurina, Yan, Clarke. *Shallow Pooling for Sparse Labels*. Information Retrieval Journal 25(4), 2022. [arXiv:2109.00062](https://arxiv.org/abs/2109.00062)
5. Thakur et al. *Hard Negatives, Hard Lessons: Revisiting Training Data Quality for Robust Information Retrieval with LLMs*. Findings of EMNLP 2025. [arXiv:2505.16967](https://arxiv.org/abs/2505.16967)
6. Xiong et al. *Approximate Nearest Neighbor Negative Contrastive Learning for Dense Text Retrieval*. ICLR 2021. [arXiv:2007.00808](https://arxiv.org/abs/2007.00808)
7. Karpukhin et al. *Dense Passage Retrieval for Open-Domain Question Answering*. EMNLP 2020. [arXiv:2004.04906](https://arxiv.org/abs/2004.04906)
8. Zhan et al. *Optimizing Dense Retrieval Model Training with Hard Negatives*. SIGIR 2021. [arXiv:2104.08051](https://arxiv.org/abs/2104.08051)
9. Lee, Chang, Toutanova. *Latent Retrieval for Weakly Supervised Open Domain Question Answering*. ACL 2019. [arXiv:1906.00300](https://arxiv.org/abs/1906.00300)
10. Fayyaz et al. *Collapse of Dense Retrievers: Short, Early, and Literal Biases Outranking Factual Evidence*. ACL 2025. [arXiv:2503.05037](https://arxiv.org/abs/2503.05037)
11. Ram et al. *Learning to Retrieve Passages without Supervision*. NAACL 2022. [arXiv:2112.07708](https://arxiv.org/abs/2112.07708)
12. Luan, Eisenstein, Toutanova, Collins. *Sparse, Dense, and Attentional Representations for Text Retrieval*. TACL 9, 2021. [arXiv:2005.00181](https://arxiv.org/abs/2005.00181)
13. Coelho et al. *Dwell in the Beginning: How Language Models Embed Long Documents for Dense Retrieval*. ACL 2024. [ACL Anthology](https://aclanthology.org/2024.acl-short.35)
14. Dai, Callan. *Deeper Text Understanding for IR with Contextual Neural Language Modeling*. SIGIR 2019. [arXiv:1905.09217](https://arxiv.org/abs/1905.09217)
15. Wang et al. *Multi-passage BERT: A Globally Normalized BERT Model for Open-domain Question Answering*. EMNLP 2019. [arXiv:1908.08167](https://arxiv.org/abs/1908.08167)
16. Chen et al. *Dense X Retrieval: What Retrieval Granularity Should We Use?* EMNLP 2024. [arXiv:2312.06648](https://arxiv.org/abs/2312.06648)
17. Smith, Troynikov. *Evaluating Chunking Strategies for Retrieval*. Chroma Technical Report, 2024. [Chroma Research](https://www.trychroma.com/research/evaluating-chunking)
18. Günther et al. *Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models*. 2024. [arXiv:2409.04701](https://arxiv.org/abs/2409.04701)
19. Anthropic. *Introducing Contextual Retrieval*. 2024. [anthropic.com](https://www.anthropic.com/engineering/contextual-retrieval)
20. Vogels, Ganea, Eickhoff. *Web2Text: Deep Structured Boilerplate Removal*. ECIR 2018. [arXiv:1801.02607](https://arxiv.org/abs/1801.02607)
21. Wenzek et al. *CCNet: Extracting High Quality Monolingual Datasets from Web Crawl Data*. LREC 2020. [ACL Anthology](https://aclanthology.org/2020.lrec-1.494/)
22. Fröbe, Bittner, Potthast, Hagen. *The Effect of Content-Equivalent Near-Duplicates on the Evaluation of Search Engines*. ECIR 2020. [DOI](https://doi.org/10.1007/978-3-030-45442-5_2)
23. Bernstein, Zobel. *Redundant Documents and Search Effectiveness*. CIKM 2005. [DOI](https://doi.org/10.1145/1099554.1099733)
24. Dalton, Xiong, Callan. *TREC CAsT 2019: The Conversational Assistance Track Overview*. 2020. [arXiv:2003.13624](https://arxiv.org/abs/2003.13624)
25. Lewis et al. *PAQ: 65 Million Probably-Asked Questions and What You Can Do With Them*. TACL 9, 2021. [arXiv:2102.07033](https://arxiv.org/abs/2102.07033)
26. Choi et al. *Decontextualization: Making Sentences Stand-Alone*. TACL 9, 2021. [DOI](https://doi.org/10.1162/tacl_a_00377)
27. Elgohary, Peskov, Boyd-Graber. *Can You Unpack That? Learning to Rewrite Questions-in-Context*. EMNLP-IJCNLP 2019. [DOI](https://doi.org/10.18653/v1/D19-1605)
28. Dai et al. *Promptagator: Few-shot Dense Retrieval From 8 Examples*. ICLR 2023. [arXiv:2209.11755](https://arxiv.org/abs/2209.11755)
29. Bonifacio et al. *InPars: Unsupervised Dataset Generation for Information Retrieval*. SIGIR 2022. [DOI](https://doi.org/10.1145/3477495.3531863) · [arXiv:2202.05144](https://arxiv.org/abs/2202.05144)
30. Boytsov et al. *InPars-Light: Cost-Effective Unsupervised Training of Efficient Rankers*. TMLR 2024. [arXiv:2301.02998](https://arxiv.org/abs/2301.02998)
31. Wang, Thakur, Reimers, Gurevych. *GPL: Generative Pseudo Labeling for Unsupervised Domain Adaptation of Dense Retrieval*. NAACL 2022. [arXiv:2112.07577](https://arxiv.org/abs/2112.07577)
32. Bazzo et al. *Assessing the Impact of OCR Errors in Information Retrieval*. ECIR 2020. [DOI](https://doi.org/10.1007/978-3-030-45442-5_13)
33. Zhuang, Zuccon. *Dealing with Typos for BERT-based Passage Retrieval and Ranking*. EMNLP 2021. [arXiv:2108.12139](https://arxiv.org/abs/2108.12139)
34. Sciavolino et al. *Simple Entity-Centric Questions Challenge Dense Retrievers*. EMNLP 2021. [arXiv:2109.08535](https://arxiv.org/abs/2109.08535)
35. Hofstätter et al. *Improving Efficient Neural Ranking Models with Cross-Architecture Knowledge Distillation*. 2020. [arXiv:2010.02666](https://arxiv.org/abs/2010.02666)
36. Ren et al. *RocketQAv2: A Joint Training Method for Dense Passage Retrieval and Passage Re-ranking*. EMNLP 2021. [arXiv:2110.07367](https://arxiv.org/abs/2110.07367)
37. FlagEmbedding (BAAI). 학습 코드와 오답 찾기 · 교사 점수 스크립트. [GitHub](https://github.com/FlagOpen/FlagEmbedding)
38. Abedjan et al. *Detecting Data Errors: Where are we and what needs to be done?* PVLDB 9(12), 2016. [PDF](https://www.vldb.org/pvldb/vol9/p993-abedjan.pdf)
39. Breck et al. *Data Validation for Machine Learning*. SysML 2019 (현 MLSys). [PDF](https://mlsys.org/Conferences/2019/doc/2019/167.pdf)
40. Nogueira, Cho. *Passage Re-ranking with BERT*. 2019. [arXiv:1901.04085](https://arxiv.org/abs/1901.04085)
41. Dodge et al. *Documenting Large Webtext Corpora: A Case Study on the Colossal Clean Crawled Corpus*. EMNLP 2021. [arXiv:2104.08758](https://arxiv.org/abs/2104.08758)
42. Gebru et al. *Datasheets for Datasets*. Communications of the ACM 64(12), 2021. [arXiv:1803.09010](https://arxiv.org/abs/1803.09010)
43. Wang et al. *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. ACL 2023. [arXiv:2212.10560](https://arxiv.org/abs/2212.10560)
44. Guu et al. *REALM: Retrieval-Augmented Language Model Pre-Training*. ICML 2020. [arXiv:2002.08909](https://arxiv.org/abs/2002.08909)
45. Rahmani et al. *Synthetic Test Collections for Retrieval Evaluation*. SIGIR 2024. [arXiv:2405.07767](https://arxiv.org/abs/2405.07767)
46. Sadasivan et al. *Can AI-Generated Text be Reliably Detected?* TMLR 2025. [arXiv:2303.11156](https://arxiv.org/abs/2303.11156)
47. Wang et al. *Improving Text Embeddings with Large Language Models*. ACL 2024. [arXiv:2401.00368](https://arxiv.org/abs/2401.00368)
48. Voorhees. *The TREC-8 Question Answering Track Report*. TREC-8, NIST, 1999. [PDF](https://trec.nist.gov/pubs/trec8/papers/qa_report.pdf)
49. Thakur et al. *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. NeurIPS 2021 Datasets and Benchmarks. [arXiv:2104.08663](https://arxiv.org/abs/2104.08663)
50. Lewis, Stenetorp, Riedel. *Question and Answer Test-Train Overlap in Open-Domain Question Answering Datasets*. EACL 2021. [arXiv:2008.02637](https://arxiv.org/abs/2008.02637)
51. Järvelin, Kekäläinen. *Cumulated Gain-Based Evaluation of IR Techniques*. ACM Transactions on Information Systems 20(4), 2002. [doi:10.1145/582415.582418](https://doi.org/10.1145/582415.582418)
