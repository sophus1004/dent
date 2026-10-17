<div align="center">

# DENT 기술 보고서: 생성(LLM) 학습 데이터 정제

LLM 학습 대화 데이터(지시 · 응답 · 선호 쌍 · 도구 호출)의 품질 문제는 모델을 어떻게 해치며, DENT는 왜 이 방법과 이 순서로 고치는가

[요약](#요약) · [서론](#1-서론) · [데이터와 검사](#2-데이터와-검사) · [문제와 피해](#3-문제마다-그대로-두면-무엇이-나빠지나) · [정제 과정](#4-정제-과정과-그-근거) · [설계 값](#5-설계-값과-그-출처) · [한계](#6-한계와-논의) · [참고 문헌](#참고-문헌)

[분류 보고서](classification.md) · [검색 보고서](retrieval.md) · [README](../../README.md)

</div>

| 항목 | 내용 |
|---|---|
| 대상 | DENT 0.1.0 생성 모듈(`generation`) |
| 데이터 | 지시만 · 지시와 응답(SFT) · 선호 쌍(DPO) · 도구 호출 대화 |
| 작성일 | 2026-10-12 |

---

## 요약

지시 학습(SFT)과 선호 학습(DPO)은 적은 데이터로 모델의 말투와 행동을 크게 바꾼다. 그래서 데이터의 작은 결함이 모델의 행동으로 그대로 옮겨 간다.
해로운 예 10개가 안전 정렬을 무너뜨리고, 데이터의 반복은 모델의 반복으로 10배 넘게 커지며, 거절이 많은 데이터는 멀쩡한 요청도 거절하게 하고,
선호 쌍의 라벨 잡음이 많은 구간에서는 잡음이 10%p 늘 때 승률이 20~30%p 떨어진다.

DENT의 생성 모듈은 이 문제들을 **1 지시 → 2 응답 → 3 선호** 세 단계의 스물다섯 가지 검사로 재고, 앞 단계의 결과가 뒤 단계의 입력이 되는 순서로 고친다.
이 보고서는 검사마다 그대로 두면 무엇이 나빠지는지의 실증을, 과정의 원칙마다 그렇게 하지 않으면 무엇이 잘못되는지의 근거를 문헌에서 모았다.
아울러 판정 모델과 LLM을 판정자로 쓸 때의 편향을 DENT에서 직접 겪은 일과 함께 적는다.

용어: **판정 모델**은 한 건마다 판정의 확률을 주는 모델(Jev)이다. **도우미**는 진단을 읽고 흐름 순서대로 규칙 · 판정 모델 · LLM으로 고치는 기능이고(채팅이 아니다),
끝난 뒤 남은 문제는 사람이 골라 **AI로 고치기**로 고친다. **문**은 새 데이터를 만들거나 다음 단계로 넘어가기 전에 앞 단계의 검사를 다시 재는 관문이다.
고친 것은 모두 기록하고 되돌릴 수 있다.

---

## 1. 서론

### 1.1 왜 대화 데이터는 작은 결함에 민감한가

| 전제 | 근거 |
|---|---|
| **적은 데이터가 행동을 바꾼다** | 고른 1,000건만으로 미세조정한 65B 모델의 응답이 사람 평가에서 GPT-4와 같거나 낫다는 판정을 43% 받았다[1]. 적은 데이터의 결함도 그만큼 크게 옮겨 간다 |
| **양보다 질** | 지시 데이터 52k를 LLM 판정으로 9k로 거른 쪽이 네 시험셋 모두에서 원래 데이터보다 나았고, 7B 학습 시간은 80분에서 14분으로 줄었다[2] |
| **결함은 증폭된다** | 학습 데이터의 반복을 모델이 10배 넘게 키웠다[3]. 해로운 예 10개로 해로운 답의 비율이 1.8%에서 88.8%가 되었다[4] |
| **지나친 거르기도 손해다** | 안전 예를 뺀 무해한 데이터로 미세조정해도 해로운 답이 늘었고[4](뺀 것과 안 뺀 것을 견준 실험은 아니다), 안전 예가 너무 많으면 과잉 거절이 된다[5]. 짧은 답이 맞는 지시도 있다 |

### 1.2 근거의 등급

| 표시 | 뜻 |
|---|---|
| **[직접]** | LLM의 지시 학습 · 선호 학습에서 잰 실증이 있다 |
| **[간접]** | 사전학습이나 다른 과제에서 잰 실증을 빌린다 |
| **[판단]** | 학술 근거를 찾지 못했고, 기전에 기댄 공학적 판단이다 |
| **[관찰]** | DENT를 실제 판정 모델 · LLM과 내장 예시로 돌리며 겪은 일이다(수가 적다) |

---

## 2. 데이터와 검사

### 2.1 데이터 모양과 흐름

원본 모양 여섯 가지(messages · ShareGPT · Alpaca · 턴마다 칸 · 선호 쌍 · 두 응답과 선호 점수)를 받아 대화로 바꾼다.
대화는 맥락(주 응답 앞의 메시지, 도구 호출 포함)과 응답 여럿, 선호 쌍(고른 응답 · 버린 응답)으로 저장한다.

```
1 지시 ──(응답 만들기)──▶ 2 응답 ──(비교 만들기)──▶ 3 선호 ──▶ 내보내기(SFT · 선호 · 지시만 jsonl)
```

입구는 가져온 원본으로 정한다(원본 쌍 → 3, 원본 응답 → 2, 지시만 → 1). 응답이나 비교를 만들어도 입구는 바뀌지 않는다.

### 2.2 스물다섯 가지 검사

| 단계 | 검사 | 정의 | 등급 |
|---|---|---|---|
| 1 지시 | 대화 구조 | 역할 순서가 틀림(도구 결과는 그 도구를 부른 응답 뒤에만) | 있으면 심각 |
| | 빈 지시 | 사용자 말이 비어 있음 | 있으면 심각 |
| | 같은 지시 | 맥락이 글자까지 같은 대화의 여분 | 2% 미만 통과 · 10% 미만 주의 |
| | 근접 중복 | 지시 임베딩 코사인 ≥ 0.92(이웃 10개 안) | 2% 미만 통과 · 10% 미만 주의 |
| | 개인정보 | 휴대전화 · 주민번호 · 카드번호 · 이메일 · 비밀값 모양 | 있으면 주의 |
| | 평가셋 오염 | 지시가 평가셋 문항과 낱말 8-gram으로 겹침(문항 낱말 가운데 50%가 넘는 낱말), 또는 임베딩이 가까운 평가 문항을 LLM이 같은 문제(바꿔 씀 · 옮김)로 판정 | 있으면 심각, 평가 문항의 2%가 넘게 겹치면 출처 의심 |
| | 과제 쏠림 | 지시 KMeans 8무리 중 가장 큰 무리 ≥ 40% | 주의 |
| 2 응답 | 응답 없음 | 지시만 있는 대화 | 있으면 주의(할 일: 응답 만들기) |
| | 빈 응답 | 응답 글이 비어 있음(도구를 부른 응답은 예외) | 있으면 심각 |
| | 도구 호출 형식 | 인자 JSON 깨짐 · 정의에 없는 도구 · 인자 빠짐 · 모르는 인자 · 형 틀림 · 짝 없는 호출과 결과 | 있으면 심각 |
| | 잘린 응답 | 문장 중간에서 끝남 | 있으면 심각 |
| | 번역된 코드 | 코드 안의 이름 · 문법이 한국어로 바뀜 | 1% 미만 통과 · 5% 미만 주의 |
| | 반복 루프 | 4~30자 조각이 다섯 번 이상 잇달아 나옴 | 있으면 주의 |
| | 거절 · 상투구 | "AI 언어 모델로서" · 거절 문구 · 다른 모델 이름 | 1% 미만 통과 · 5% 미만 주의 |
| | 짧은 응답 | 5자 미만 | 있으면 주의 |
| | 반복 문장 | 25자 넘는 같은 문장 · 줄이 세 번 이상(목록 · 코드 뺌) | 1% · 5% |
| | 언어 섞임 | 코드 밖 글이 한국어가 아님: 로마자 60자 넘고 한글 몫 40% 미만, 또는 한자 · 가나 4자 이상(코드가 든 응답은 재지 않음) | 1% · 5% |
| | 깨진 글자 | 대체 문자 · 다른 문자 체계 | 있으면 주의 |
| | 긴 대화 | 학습할 모델의 토크나이저로 센 대화가 최대 길이를 넘음(학습할 모델이 없으면 응답 6,000자로 어림) | 1% · 5% |
| | 지시 불일치 | 판정 모델이 '따르지 않음'(따름 ≤ 0.2)으로 확인한 한 턴 · 글 응답 | 1% 미만 통과 · 3% 미만 주의 |
| | 안전 | 판정 모델이 '해로움' ≥ 0.8로 본 응답(표본 최대 2,000) | 있으면 주의 |
| | 안전 예시 | 해로운 요청에 거절로 답한 대화: 거절 표시가 있는 대화 가운데 사람 · 도우미가 마땅한 거절로 둔 것 | 대화 100건 이상인데 거절이 하나도 없으면 주의 |
| 3 선호 | 같은 쌍 · 동점 | 고른 응답과 버린 응답이 같거나 원본 점수가 같음 | 있으면 주의 |
| | 판정 반대 | 판정 모델이 버린 응답을 0.3 이상 더 따른다고 봄 | 있으면 주의 |
| | 작은 마진 | 두 응답의 따름 점수 차이 < 0.1 | 20% 이상이면 주의 |
| | 길이 편향 | 고른 응답이 더 긴 쌍의 몫 | 70% 이상이면 주의 |

---

## 3. 문제마다: 그대로 두면 무엇이 나빠지나

### 3.1 1단계 지시

**같은 지시 · 근접 중복.** 몇몇 예만 많이 되풀이되면 모델 용량이 그 예를 외우는 데 쓰인다.
데이터의 0.1%를 100번 되풀이하면 800M 모델이 400M 모델 수준으로 떨어졌고[6], 중복을 지우면 외운 글을 1/10로 냈다[7]. **[간접]**
데이터 **전체**를 고르게 4번까지 되풀이하는 것은 해가 작았다[8]. 해가 되는 것은 '일부만 많이'의 반복이다.
SFT의 지시 중복만 따로 잰 실증은 찾지 못했다.

**개인정보.** 모델은 학습 글의 개인 정보를 다시 낸다. 한 사람의 이름 · 이메일 · 전화 · 팩스 · 주소를 뽑아냈고, 학습 데이터의 한 문서에만 든 이름 · 전화 · 이메일도 뽑아냈으며[9],
10번 나온 글은 1번 나온 글보다 평균 약 1,000배 자주 생성되었다[10]. 가리기(스크러빙)는 위험을 줄이지만 놓친 개인정보는 샌다[11]. **[간접]**
대규모 데이터 정제도 정규식으로 이메일 · 전화를 찾아 표지로 바꾼다[12]. DENT의 방식이 이와 같다.

**평가셋 오염.** 평가 문항이나 그것을 바꿔 쓴 글로 학습하면 점수가 부풀어 실제 실력을 알 수 없다.
질문을 바꿔 쓴 시험 문항으로 학습한 Llama-2 13B의 MMLU가 54.8에서 89.9가 되었고[13], 새로 만든 같은 난이도의 수학 문항에서는 점수가 최대 8% 떨어졌다[14]. **[직접]**
DENT의 기준은 "평가 문항 토큰 가운데 50%가 넘는 토큰이 같은 학습 예와 8-gram으로 겹치면 겹친 문항"으로 보는 Tülu 3의 기준[15]과 같은 방식이다(Tülu 3는 토큰, DENT는 낱말 단위).
n-gram 대조는 말을 바꾸거나 다른 언어로 옮긴 문항을 놓친다. [13]은 임베딩으로 가까운 학습 예를 찾고 LLM이 바꿔 쓴 것인지 판정하는 방식을 냈고, DENT도 같은 방식으로
8-gram에 걸리지 않은 지시마다 임베딩이 가까운 평가 문항을 LLM에 묻는다. DENT가 만든 측정 셋(평가 문항 20, 학습 지시 45: 베낌 · 바꿔 씀 · 영어로 옮김 각 5,
주제만 같은 다른 문제 10, 상관없음 20)에서 8-gram은 오염 15건 가운데 5건, 임베딩 유사도(≥ 0.9)만으로는 7건, 임베딩 후보 + LLM 판정은 15건을 오탐 없이 잡았다. **[관찰]**
Tülu 3의 데이터셋 단위 규칙(어느 평가셋이든 문항의 2%가 넘게 겹치면 그 학습셋을 오염으로 봄)[15]은 진단에 '출처 의심'으로 보인다. 겹친 지시만 빼도 같은 출처의 나머지가 남기 때문이다.

**과제 쏠림.** 지시가 한 종류에 몰리면 다른 능력이 덜 자란다. 지시의 뜻 · 의도 태그가 넓게 퍼질수록 MT-Bench 점수가 올랐고,
다양하고 복잡한 6K로 학습한 모델이 125K로 학습한 모델을 조금 넘었다(6.44 대 6.39)[16]. 어떤 단일 데이터셋도 모든 평가에서 최선이 아니었다[17]. **[직접]**
쏠림을 남겼을 때의 피해를 직접 잰 수치는 찾지 못했다.

### 3.2 2단계 응답

**반복 루프 · 반복 문장.** 학습 데이터의 반복과 생성 글의 반복이 강하게 상관하고, 반복 비율이 다른 데이터로 학습한 모델은 데이터의 반복을 10배 넘게 키웠다.
지시 학습에서도 반복이 5.54%인 Alpaca로 미세조정한 Llama2-7B는 생성 글의 15.08%가 반복이었다(약 2.7배)[3]. 문맥에 반복이 많을수록 그 문장을 다시 낼 확률이 오르는 자기 강화가 기전이다[18]. **[직접]**
목록 · 표 · 코드의 정당한 반복은 오탐이 될 수 있어, DENT는 반복 문장을 목록 항목과 코드 블록을 빼고 재고, 루프는 글자가 든 조각만 센다(들여쓰기 공백 · 표 구분선 뺌).

**지시 불일치.** 지시에 맞지 않는 응답은 엉뚱한 대응을 가르친다. ChatGPT 판정으로 52k를 9k로 거른 데이터가 원래 데이터보다 나았고,
원래 데이터로 학습한 13B와 견준 사람 평가(160문항)에서 승 63 · 무 64 · 패 33이었다[2].
지시를 따르기 어려운 정도(IFD)로 고른 Alpaca의 5% · WizardLM의 10%가 전체 데이터로 학습한 모델보다 나았다(GPT-4 판정)[19]. **[직접]**

**안전.** 해로운 예 몇 개로 정렬이 무너진다. 해로운 예 10개로 미세조정하니 GPT-3.5 Turbo의 해로운 답 비율이 1.8%에서 88.8%, 100개로 91.8%가 되었다.
해로운 예 없이 안전 예 1,902건을 뺀 Alpaca로만 미세조정해도 5.5%에서 31.8%로 올랐다[4]. **[직접]**

**거절 · 상투구.** 거절이 많으면 멀쩡한 요청도 거절하는 과잉 안전을 배운다. 안전 예 수백 개(3%)는 안전을 크게 올리면서 능력을 크게 해치지 않았지만,
안전 예 2,000개로 학습한 모델은 안전한 질문 50개 가운데 절반이 넘는 질문에 과잉 안전 반응을 보였다[5]. GPT-4도 안전한 질문 250개 가운데 6.4%를 완전히 거절했다[20]. **[직접]**
그래서 지울 것은 "멀쩡한 요청에 대한 거절"이고, 마땅한 거절은 남겨야 한다. 마땅한 거절이 하나도 없는 것도 문제라(안전 예시 없음),
DENT는 거절 검사에서 마땅한 거절로 남긴 대화를 '안전 예시'로 세어 보인다. 요청이 해로운지를 판정 모델에 직접 묻는 방법도 재 봤으나,
새로 쓴 해로운 요청 20개 · 민감한 주제의 멀쩡한 요청 20개에서 가장 나은 문구도 해로운 요청 13개만 잡고 멀쩡한 요청 5개를 잘못 잡아(0.8 기준) 쓰지 않았다. **[관찰]** DENT는 판정 모델이 지시를 따른 답으로 확인한 것만 규칙으로 남기고,
나머지는 LLM이 "해롭거나 할 수 없는 요청을 거절한 것인가"를 본다. 판정 모델의 '마땅한 거절' 점수는 멀쩡한 요청의 거절에도 높게 나올 때가 있어 참고만 한다. **[관찰]**
SFT 데이터에서 거절 문구를 지운 학술 연구는 찾지 못했다.

**짧은 응답.** 가장 긴 응답 1,000개로 학습한 모델이 가장 짧은 1,000개로 학습한 모델을 승률 97%로 이겼고, 엄선한 1k 데이터셋들도 이겼다[21]. **[직접]**
다만 이 비교에 쓴 판정자(GPT-4 · PaLM-2)가 긴 답을 좋아하는 편향이 섞여 있을 수 있고, 짧아도 맞는 답이 있다. DENT는 짧은 응답을 주의로만 두고, 판정 모델이 지시를 따른 답으로 본 것(분류 과제의 0 · 1 등)은 남기며,
나머지는 LLM이 "지시가 짧은 답을 바라는가"를 본다.

**언어 섞임.** 영어 위주로 지시 학습하면 다른 언어로 물어도 영어로 답하는 언어 혼동이 생긴다.
단일 언어 생성에서 영어만으로 SFT하면 질문 언어로 답하는 비율이 86.2%에서 77.8%로 떨어졌고, 다국어 SFT(그래도 90%는 영어)로는 98.3%였다[22]. **[직접]**
섞인 응답을 빼서 나아진다는 직접 실험은 아니다.

**도구 호출 형식.** 틀린 호출로 학습하면 없는 도구 · 인자를 지어내는 법을 배운다. LLM이 만든 도구 호출 데이터의 검증 통과율은 34~84%였고,
형식 → 실행 → 의미 3단계로 검증한 데이터로 학습한 7B 모델이 도구 호출 평가에서 여러 GPT-4 모델을 넘었다[23].
규칙 · 모델 두 겹 검증을 거친 데이터가 검증 없는 데이터보다 확실히 나았다[24]. **[직접]**
DENT의 검사는 이 가운데 형식 검사(없는 도구 · 빠진 인자 · 모르는 인자 · 형)에 해당하고, 평가의 구문 트리 검사[25]와 같은 축을 본다.

**빈 · 잘린 응답, 긴 대화.** 응답이 잘리거나 학습 최대 길이를 넘으면 응답 끝과 끝 표지(EOS)가 사라진다. 모델은 문장 중간에서 끝내거나 멈추지 못하는 법을 배운다.
SFT에서 이 피해를 잰 학술 실증은 찾지 못했다. 사전학습에서 불필요한 문서 잘림을 없애니 맥락 따르기가 상대 최대 16.8% 나아지고
코드 생성의 환각(정의되지 않은 이름)이 최대 58.3% 줄었다는 보고가 간접 근거다[26].
빈 응답은 대규모 지시 데이터 정제에서도 거른다[15]. **[판단] · [간접]**

**번역된 코드.** 지시 데이터를 기계 번역하면 코드 안의 변수 이름 일부까지 번역되는 일이 보고되었다[27]. 학습 피해를 잰 수치는 찾지 못했다. **[판단]**

### 3.3 3단계 선호

**같은 쌍 · 동점.** DPO 손실의 기울기는 고른 응답과 버린 응답의 로그 확률 기울기 차이에 비례한다[28]. 두 응답이 같으면 그 차이가 0이 되어
배울 것 없이 학습 묶음의 자리만 차지한다(손실은 log 2로 고정된다). 동점 쌍을 보통 DPO에 넣으면 과제 성능이 떨어졌다[29]. **[직접]**

**판정 반대(라벨 뒤집힘).** 선호 라벨의 잡음은 정렬을 크게 해친다. 잡음 비율이 0.3 미만일 때는 10%p 늘어도 승률 하락이 10%p 미만이었지만,
0.4를 넘는 구간에서는 10%p 늘 때 승률이 20~30%p 떨어졌다[30].
보상 모델 열 개의 점수로 선호 강도를 재니 HH-RLHF의 약 25%는 점수 차의 평균이 0 아래라 라벨이 거꾸로일 수 있었고,
선호 강도 하위 쌍의 라벨을 뒤집자 보상 모델의 검증 정확도가 올랐다[31]. **[직접]**

**작은 마진.** 두 응답의 차이가 작은 쌍은 신호가 약하고 뒤집힌 쌍이 섞이기 쉽다. 보상 마진이 큰 쌍으로 고른 10%가 전체 데이터보다,
전체가 두 응답이 거의 같은 쌍보다 나았다(Llama-3-8B, AlpacaEval 2 LC 승률 19.53% · 17.32% · 13.93%)[32]. 대형 모델 학습에서도 비슷한 응답 쌍은 버렸다[33]. **[직접]**

**길이 편향.** 고른 응답이 늘 더 길면 모델은 질이 아니라 길이를 배운다. 길이만 보상해도 RLHF 향상을 거의 그대로 재현했고,
선호 데이터에서 "긴 쪽 고르기"만으로 55.7~63.1%를 맞혔다[34]. 길이를 규제하지 않은 DPO 모델의 답은 선호 데이터의 응답보다 평균 약 두 배 길었다[35]. **[직접]**
길이의 일부는 진짜 질이므로[21], DENT는 쌍을 빼지 않고 데이터셋의 모양으로 보이기만 한다(손실이나 평가에서 길이를 통제하는 편이 안전하다[35]).

---

## 4. 정제 과정과 그 근거

### 4.1 흐름 순서: 지시 → 응답 → 선호

```
계획 → 1 지시: 대화 구조 · 빈 지시 · 같은 지시 · 개인정보 · 오염 · 근접 중복(규칙)
     → (문) → 2 응답: 응답 만들기(허락) · 도구 호출 · 빈 응답 · 루프 · 거절 · 짧은 응답 · 지시 불일치 · 안전
     → (문) → 3 선호: 비교 만들기(허락) · 같은 쌍 · 동점 · 판정 반대 → 보고
```

**하지 않으면.** 오염 · 개인정보가 남은 지시로 응답을 만들면 그 결함이 응답과 쌍으로 번진다. 응답이 틀린 채 쌍을 만들면 틀린 응답이 '고른 응답'이 된다.
DPO 성능은 고른 응답의 질이 크게 좌우한다는 보고[36]가 "응답을 먼저 고친 뒤 쌍을 만든다"는 순서를 받친다.
단계는 그 단계에 닿은 때 다시 잰다. 앞의 고치기가 뒤의 문제를 만들기 때문이다. 예를 들어 루프를 자르면 잘린 응답이 생긴다.

**근거.** 오류 탐지 도구를 돌리는 순서가 결과를 크게 바꿨고(순서를 정해 돌리니 모든 결과를 합친 것보다 정밀도 평균 +28%, 재현율 −3.5%)[37],
데이터 오류는 퍼지기 전에 일찍 잡아야 한다[38].
"오염 · 개인정보 → 응답 → 쌍" 순서 자체를 실험한 문헌은 없다. **[판단]**

### 4.2 싼 판단부터: 규칙 → 판정 모델 → LLM → 사람

| 층 | 맡는 일 |
|---|---|
| 규칙 | 지시 단계 전부(구조 · 빈 지시 · 같은 지시 · 개인정보 가리기 · 오염 · 근접 중복 빼기), 도구 호출 형식 · 빈 응답 빼기, 루프 자르기, 같은 쌍 빼기 |
| 판정 모델 | 거절 · 짧은 응답으로 잡힌 것과 지시 불일치 후보에 '지시를 따르나', 응답 표본(최대 2,000)에 '해로운가', 거절로 잡힌 것에 '마땅한 거절인가', 쌍의 두 응답을 따로 점수 |
| LLM | 판정 모델이 괜찮다고 보지 않은 거절 · 짧은 응답, 지시 불일치 · 안전 · 판정 반대의 판정, 평가셋 오염 후보가 같은 문제인지, 응답 · 비교 만들기, 다시 쓰기 |
| 사람 | 검토(숫자 키로 결정), 남은 것 고르기, 허락 |

**근거.** 싼 단계부터 부르는 LLM 계단으로 같은 성능을 최대 98% 적은 비용에 냈다[39]. 같은 입력에 같은 답을 내도록(결정적으로) 설정한 LLM도 실행마다 정확도가 최대 15% 달라[40],
규칙으로 되는 일을 LLM에 맡기지 않는다. 도구 호출 데이터의 검증도 형식 검사 → 실행 → LLM의 의미 검사 순서였다[23].

### 4.3 판정자의 편향을 막는다

판정 모델과 LLM은 판정자로서 편향이 있다. DENT는 다음 다섯 가지를 겪고 과정을 바꿨다.

| 겪은 일 [관찰] | 문헌 | DENT의 방법 |
|---|---|---|
| 두 응답을 한 번에 보이고 어느 쪽이 나은지 물으면 판정 모델이 대부분 뒤의 응답을 골랐다(두 순서의 판정이 같은 쌍은 30쌍 중 9~10쌍) | LLM 판정자는 자리 편향이 있고, 순서를 바꾸면 GPT-4도 일치가 65%였다. 원문의 대처는 순서를 바꿔 두 번 묻는 것이고, 응답 하나씩 점수 매기기도 사람 판단과 잘 맞았다[41]. ChatGPT가 판정할 때 순서만 바꿔도 80문항 중 66문항에서 결과가 엇갈렸다[42] | 쌍은 두 응답을 **따로** 물어 점수를 견준다(자리를 없애는 쪽을 골랐다) |
| 판정 모델의 '따르지 않음' 점수를 보이고 LLM에게 빼기를 맡기자 맞는 여러 턴 답을 여러 건 뺐다. 점수 없이 중립적으로 물으면 같은 LLM이 맞는 답 · 바꿔 단 답을 모두 맞혔다 | 판정자는 밖에서 준 힌트를 따른다. "85%가 A가 낫다고 믿는다"는 한 문장에 평가에 쓴 LLM 대부분(15개 중 11개)이 끌렸다[43] | LLM에게 판정 모델의 점수를 **보이지 않고** "마지막 말에 맞게 답했나"를 예 · 아니오 · 모름으로 묻는다. 거절 · 짧은 응답 · 안전도 점수 없이 남길지를 묻는다 |
| 판정 모델은 여러 턴의 뒷말 · 코드만 있는 답을 '따르지 않음'으로 자주 잘못 봤고, 판정 반대 쌍의 절반은 판정 모델의 오판이었다 | 판정자 확인만으로 바꾸면 맞는 데이터를 바꾼다. 자동 탐지 후보 중 실제 오류는 51%였다[44] | 여러 턴 · 코드 응답은 '의심'으로만 두고, 지시 불일치 · 판정 반대는 LLM이 한 번 더 본다 |
| LLM에게 판정 반대 쌍의 두 응답을 한 번 보이고 고르게 하면 18쌍 가운데 12쌍만 맞았다(세 번 돌린 합) | 위의 자리 편향[41][42] | 두 응답의 **순서를 바꿔 두 번** 묻고 둘이 같을 때만 뒤집는다(엇갈리면 보류). 같은 쌍에서 18쌍 모두 맞았다 |
| LLM이 판정을 먼저 쓰고 까닭을 뒤에 쓰면, 까닭에서는 답이 틀렸다고 해 놓고 판정은 남김인 일이 있었다(짧은 응답 21건 중 15건만 맞음) | 판정자에게 먼저 풀어 보게 하면(chain-of-thought) 수학 · 추론 문제의 채점이 나아졌다[41] | **까닭을 먼저** 쓰고 그에 맞게 판정하게 한다(같은 21건 중 18건) |

### 4.4 문과 허락: 만들기 전에 다시 잰다

응답 만들기와 비교 만들기는 허락 단계다. 응답 단계에 들어가기 전, 선호 단계에 들어가기 전에 앞 단계들의 검사를 다시 재고, 심각이 남으면 묻지 않고 막는다.
허락 카드에는 시험 5건의 결과와 비용 어림을 싣는다.

**하지 않으면.** 오염된 지시에 응답을 만들면 오염이 응답으로 번지고, 결함 있는 대화를 보기로 삼은 생성은 결함을 물려받는다.

**근거.** LLM이 만든 지시 데이터 200건 중 모든 칸이 맞은 것은 54%였다[45]. 만든 것은 다시 검사해야 한다.
DENT는 만든 응답을 같은 규칙(빈 · 잘림 · 루프 · 언어 섞임 · 번역된 코드)으로 다시 재서 걸리면 한 번 더 만들고, 도우미와 AI로 고치기에서는 그래도 걸리면 넣지 않는다
(검토에서 한 건을 만들 때는 마지막 글을 넣고 진단이 다시 잰다). 응답 · 비교 만들기는 도구 호출 대화를 건너뛴다(호출을 지어내지 않게).
"심각일 때 막는다"는 경계 자체는 **[판단]**이다.

### 4.5 비교 만들기와 그 한계

선호 데이터인데 쌍이 없는 지시에는 LLM이 다른 구성의 응답을 쓰고, 판정 모델이 두 응답을 따로 본 점수가 높은 쪽을 고른 응답으로 둔다(판정 모델이 없으면 건너뛴다).
응답마다 따로 점수를 매겨 높은 것과 낮은 것으로 쌍을 만드는 방식[46]과 같다. 여러 응답을 한 프롬프트에 함께 넣고 매기는 방식[47]은
DENT가 겪은 자리 편향(4.3절) 때문에 피했다. 이 방식의 원 논문은 오히려 함께 매기기를 권한다.

**학습할 모델로 만들기.** 학습할 모델이 직접 만든 응답(on-policy)이나 버린 응답의 확률을 낮추는 방법이 오프라인 방법보다 낫다는 연구가 있다[48].
그래서 연결 설정에 '학습할 모델'(LLM과 같은 방식, 선택)을 두면, 비교 만들기는 그 모델에 지침 없이 대화를 그대로 보내 평소처럼 답한 응답을 받는다.
지침('다른 구성으로 써라')을 붙이면 그 모델이 평소에 내는 응답이 아니게 되기 때문이다. 학습할 모델이 없으면 연결한 LLM이 주 응답과 다르게 쓴다.

**한계.** 그 학습 효과는 DENT 안에서 재지 못했다(학습 환경이 필요하다).
DPO는 학습 데이터와 모델 출력의 분포 차이에 민감해, 기준 모델을 선호 데이터의 안전한 응답으로 한 번 더 SFT해 분포를 맞추니 안전률이 55.4%에서 71.8%로,
모델이 만든 응답으로 DPO를 되풀이하니 99.9%로 올랐다[49]. 학습할 모델을 연결하지 않으면 연결한 LLM이 응답을 쓰므로, 학습할 모델에게는 분포 밖일 수 있다.
on-policy 데이터의 효과가 모델마다 크게 달랐다는 반론도 있다(정적 데이터 대비 Llama-3 3배, Zephyr 0.4배)[50].

### 4.6 만든 것은 드러내고, 고친 것은 되돌린다

- **출처와 묶음.** 만든 응답 · 다시 쓴 응답 · 만든 쌍은 출처를 `synthetic`으로 두고, 내보내는 SFT · 선호 · 지시만 jsonl에 `source` 칸을 넣는다.
  줄마다 묶음 칸 `group`(같은 맥락이거나 근접 중복으로 이어진 지시들의 대표 번호)도 넣어, 받은 쪽이 묶음째 나누면 거의 같은 지시가 학습 · 평가 양쪽에 들어가지 않는다.
  진짜 데이터를 버리지 않고 합성 데이터를 쌓으면 모델 붕괴를 피하지만[51], 그러려면 무엇이 합성인지 가를 수 있어야 한다.
  LLM 판정자는 자기가 만든 글을 알아보고 더 높게 매긴다[52]. 합성 응답을 가려 두어야 평가에서 따로 볼 수 있다.
- **기록과 되돌리기.** 검토의 결정과 도우미의 고치기는 결정 기록에 남고 카드 하나 · 실행 전체 · 결정 한 건 단위로 되돌린다. 만든 응답을 되돌리면 그 응답으로 만든 쌍도 지운다.
  영구 삭제는 없다. 오류를 일찍 잡으면 작동하던 상태로 되돌릴 수 있다[38].
- **그대로 두기도 기록한다.** 규칙이 잡았지만 사람이 그대로 둔 것은 따로 모아 다시 물을 수 있다. 진단 줄마다 그대로 둔 수를 보여, 규칙의 오탐을 살피는 데 쓴다.

---

## 5. 설계 값과 그 출처

| 값 | DENT | 출처 |
|---|---|---|
| 평가셋 오염 | 낱말 8-gram, 문항 낱말 가운데 50%가 넘는 낱말이 겹치면 오염, 8낱말보다 짧은 문항은 3낱말 이상일 때만 통째로 견줌 | Tülu 3의 문항 기준과 같은 방식이다[15]. 짧은 문항을 통째로 견주는 것은 GPT-3(13-gram보다 짧으면 문항 전체, 데이터셋마다 N = 8~13)와 비슷하다[53] |
| 뜻으로 견주는 오염 | 지시마다 코사인 ≥ 0.65인 평가 문항 위 3개를 LLM에(실행마다 최대 2,000쌍), LLM이 없으면 코사인 ≥ 0.9만 오염 | 임베딩 후보 + LLM 판정은 [13]의 방식이다. 값은 bge-m3로 잰 측정 셋에서 정했다(옮긴 문항이 가장 먼 것 0.68, 주제만 같은 문제가 가장 가까운 것 0.75). **DENT가 정함** |
| 데이터셋 단위 오염 | 평가 문항의 2%가 넘게 겹치면 출처 의심 | Tülu 3[15] |
| 쌍 판정 | 판정 모델은 두 응답을 따로 묻고, LLM은 순서를 바꿔 두 번 물어 둘이 같을 때만 | 응답 하나씩 점수 매기기는 사람 판단과 잘 맞았고, 순서를 바꿔 두 번 묻는 것은 원문의 대처다[41]. 자리 편향을 없애려는 선택은 DENT의 판단이다 |
| 비교 만들기 | 따로 매긴 점수가 높은 쪽을 고른 응답으로 | [46]과 같다 |
| 지시 불일치 | 지시 · 응답 유사도가 가장 낮은 10%(최소 20)를 후보로, 판정 모델 따름 ≤ 0.2를 확인 | **DENT가 정함** |
| 안전 | 판정 모델 '해로움' ≥ 0.8, 표본 최대 2,000 | **DENT가 정함.** 안전 분류 연구[54]는 고정 문턱 없이 순위 지표로 보고한다 |
| 판정 반대 · 작은 마진 | 따름 차이 ≤ −0.3 · < 0.1(20% 이상이면 주의) | **DENT가 정함** |
| 길이 편향 | 고른 응답이 더 긴 쌍 ≥ 70% | **DENT가 정함.** 실제 선호 데이터에서 긴 쪽 고르기의 정확도는 55.7~63.1%였다[34] |
| 근접 중복 · 과제 쏠림 | 코사인 ≥ 0.92 · KMeans 8무리 40% | 분류 모듈과 같은 값. **DENT가 정함**(8은 scikit-learn 기본값) |
| 루프 · 반복 문장 | 4~30자 조각 다섯 번 이상 · 25자 넘는 문장 세 번 이상 | **DENT가 정함.** 데이터 정제의 반복 규칙[55]은 문서 단위 몫을 쓴다 |
| 비율 경계 | 중복 2% · 10%, 응답 검사 1% · 5%, 지시 불일치 1% · 3% | 분류 · 검색의 비슷한 검사와 맞췄다. **DENT가 정함** |
| 응답 만들기 | 같은 규칙에 걸리면 한 번 더, 동시 4건, 시험 5건 | **DENT가 정함** |
| 안전 예시 | 대화 100건 이상인데 거절이 없으면 주의 | 3% 안팎의 안전 예가 안전을 크게 올렸다[5]. 100건이면 3건쯤이라 그보다 작으면 없는 것이 우연일 수 있다. **DENT가 정함** |

---

## 6. 한계와 논의

1. **오염 대조의 한계.** 뜻으로 견주는 경계(후보 코사인 0.65 · LLM 없이 0.9)는 bge-m3와 작은 측정 셋으로 정해, 다른 임베딩 모델에서는 맞지 않을 수 있다.
   LLM에 묻는 후보는 실행마다 2,000쌍까지이고 넘는 쌍은 임베딩 유사도로만 본다. 평가셋은 사람이 가져와 표시해야 대조한다(공개 평가셋을 스스로 찾지 않는다).
   수만 바꾼 문제처럼 '같은 문제'인지 애매한 경우는 LLM의 판단에 맡긴다.
2. **비교 만들기의 분포.** 4.5절. 학습할 모델을 연결하면 그 모델의 응답으로 만들지만, 학습할 모델이 학습 중에 바뀌면(되풀이 DPO) 한 번 만든 쌍은 다시 분포 밖이 된다.
   학습할 모델을 서버로 띄워 둬야 하고(OpenAI 방식 주소), 만든 응답을 판정 모델이 고르는 단계는 그대로다.
3. **판정 반대 뒤집기는 판정자가 맞을 때만 이득이다.** 판정 반대로 잡은 쌍의 절반을 판정 모델이 오판한 [관찰]처럼, 판정자가 틀리면 뒤집기는 잡음을 더한다.
   DENT가 판정 반대를 판정 모델만으로 정하지 않고 LLM이 보게 하는 이유다.
4. **판단 단계의 한계.** 지시 불일치 · 거절 · 짧은 응답 · 안전은 점수를 보이지 않는 중립 판정으로, 판정 반대는 두 순서로 묻도록 바꿨다(4.3절).
   그래도 계산이 틀린 짧은 답처럼 LLM이 직접 풀어 봐야 아는 것은 놓친다(측정에서 남은 3건). 판정 모델이 '해롭다'고 보지 않은 응답은 LLM에 가지 않는다.
5. **근거가 없는 검사.** 잘린 응답 · 긴 대화 · 번역된 코드의 학습 피해는 학술 실증이 없고 기전에 기댄다. 짧은 응답 · 과제 쏠림의 근거는 '긴 것 · 다양한 것을 고르면 낫다'는
   반대 방향의 실험이다.
6. **판정 모델의 안전 질문 범위.** '해로운가'를 일반적으로 묻는다. 위험 분류 체계로 학습한 안전 분류기[54]와는 다르며, 성적 역할극 같은 지시의 범위는 정하지 않았다.
7. **사람 확인의 편향.** 사람도 미리 붙은 제안을 따라가는 경향이 있다. 미리 붙인 주석이 주석자를 편향시켰다는 보고가 있다[56].

---

## 7. 결론

대화 데이터의 결함은 적은 데이터로 크게 바뀌는 지시 학습 · 선호 학습을 거쳐 모델의 행동으로 증폭된다. 반복은 반복을, 해로운 예는 해로운 답을,
과한 거절은 과잉 거절을, 뒤집힌 쌍은 거꾸로 된 선호를 낳는다. DENT는 지시 → 응답 → 선호 순서로, 앞 단계의 결과가 뒤 단계의 입력이 되게 고치고,
만들기 전에 다시 재서 심각이 남으면 막고, 만든 것은 같은 규칙으로 다시 재고 출처로 드러낸다. 판정 모델과 LLM의 편향은 DENT가 직접 겪은 일이며,
문헌의 자리 편향 · 힌트 편향 실증과 맞물려 '따로 묻기' · '순서를 바꿔 두 번 묻기' · '점수 없이 묻기' · '까닭 먼저' · '판정 모델의 판정을 LLM이 한 번 더 보기'로 이어졌다.
숫자 문턱 대부분과 일부 순서는 공학적 판단으로 남아 있다.

---

## 참고 문헌

본문의 수치는 각 문헌의 본문 또는 초록에서 옮겼다. 학회 발표가 확인되지 않은 것은 arXiv로 적었다.

1. Zhou et al. *LIMA: Less Is More for Alignment*. NeurIPS 2023. [arXiv:2305.11206](https://arxiv.org/abs/2305.11206)
2. Chen et al. *AlpaGasus: Training a Better Alpaca with Fewer Data*. ICLR 2024. [arXiv:2307.08701](https://arxiv.org/abs/2307.08701)
3. Li et al. *Repetition In Repetition Out: Towards Understanding Neural Text Degeneration from the Data Perspective*. NeurIPS 2023. [arXiv:2310.10226](https://arxiv.org/abs/2310.10226)
4. Qi et al. *Fine-tuning Aligned Language Models Compromises Safety, Even When Users Do Not Intend To!* ICLR 2024. [arXiv:2310.03693](https://arxiv.org/abs/2310.03693)
5. Bianchi et al. *Safety-Tuned LLaMAs: Lessons From Improving the Safety of Large Language Models that Follow Instructions*. ICLR 2024. [arXiv:2309.07875](https://arxiv.org/abs/2309.07875)
6. Hernandez et al. *Scaling Laws and Interpretability of Learning from Repeated Data*. 2022. [arXiv:2205.10487](https://arxiv.org/abs/2205.10487)
7. Lee et al. *Deduplicating Training Data Makes Language Models Better*. ACL 2022. [arXiv:2107.06499](https://arxiv.org/abs/2107.06499)
8. Muennighoff et al. *Scaling Data-Constrained Language Models*. NeurIPS 2023. [arXiv:2305.16264](https://arxiv.org/abs/2305.16264)
9. Carlini et al. *Extracting Training Data from Large Language Models*. USENIX Security 2021. [arXiv:2012.07805](https://arxiv.org/abs/2012.07805)
10. Kandpal, Wallace, Raffel. *Deduplicating Training Data Mitigates Privacy Risks in Language Models*. ICML 2022. [arXiv:2202.06539](https://arxiv.org/abs/2202.06539)
11. Lukas et al. *Analyzing Leakage of Personally Identifiable Information in Language Models*. IEEE S&P 2023. [arXiv:2302.00539](https://arxiv.org/abs/2302.00539)
12. Soldaini et al. *Dolma: an Open Corpus of Three Trillion Tokens for Language Model Pretraining Research*. ACL 2024. [ACL Anthology](https://aclanthology.org/2024.acl-long.840)
13. Yang et al. *Rethinking Benchmark and Contamination for Language Models with Rephrased Samples*. 2023. [arXiv:2311.04850](https://arxiv.org/abs/2311.04850)
14. Zhang et al. *A Careful Examination of Large Language Model Performance on Grade School Arithmetic*. NeurIPS 2024 Datasets and Benchmarks. [arXiv:2405.00332](https://arxiv.org/abs/2405.00332)
15. Lambert et al. *Tülu 3: Pushing Frontiers in Open Language Model Post-Training*. COLM 2025. [arXiv:2411.15124](https://arxiv.org/abs/2411.15124)
16. Lu et al. *#InsTag: Instruction Tagging for Analyzing Supervised Fine-tuning of Large Language Models*. ICLR 2024. [arXiv:2308.07074](https://arxiv.org/abs/2308.07074)
17. Wang et al. *How Far Can Camels Go? Exploring the State of Instruction Tuning on Open Resources*. NeurIPS 2023 Datasets and Benchmarks. [arXiv:2306.04751](https://arxiv.org/abs/2306.04751)
18. Xu et al. *Learning to Break the Loop: Analyzing and Mitigating Repetitions for Neural Text Generation*. NeurIPS 2022. [arXiv:2206.02369](https://arxiv.org/abs/2206.02369)
19. Li et al. *From Quantity to Quality: Boosting LLM Performance with Self-Guided Data Selection for Instruction Tuning*. NAACL 2024. [arXiv:2308.12032](https://arxiv.org/abs/2308.12032)
20. Röttger et al. *XSTest: A Test Suite for Identifying Exaggerated Safety Behaviours in Large Language Models*. NAACL 2024. [arXiv:2308.01263](https://arxiv.org/abs/2308.01263)
21. Zhao et al. *Long Is More for Alignment: A Simple but Tough-to-Beat Baseline for Instruction Fine-Tuning*. ICML 2024. [arXiv:2402.04833](https://arxiv.org/abs/2402.04833)
22. Marchisio et al. *Understanding and Mitigating Language Confusion in LLMs*. EMNLP 2024. [arXiv:2406.20052](https://arxiv.org/abs/2406.20052)
23. Liu et al. *APIGen: Automated Pipeline for Generating Verifiable and Diverse Function-Calling Datasets*. NeurIPS 2024 Datasets and Benchmarks. [arXiv:2406.18518](https://arxiv.org/abs/2406.18518)
24. Liu et al. *ToolACE: Winning the Points of LLM Function Calling*. ICLR 2025. [arXiv:2409.00920](https://arxiv.org/abs/2409.00920)
25. Patil et al. *The Berkeley Function Calling Leaderboard (BFCL): From Tool Use to Agentic Evaluation of Large Language Models*. ICML 2025. [PMLR](https://proceedings.mlr.press/v267/patil25a.html)
26. Ding et al. *Fewer Truncations Improve Language Modeling*. ICML 2024. [arXiv:2404.10830](https://arxiv.org/abs/2404.10830)
27. Weber et al. *Investigating Multilingual Instruction-Tuning: Do Polyglot Models Demand for Multilingual Instructions?* EMNLP 2024. [arXiv:2402.13703](https://arxiv.org/abs/2402.13703)
28. Rafailov et al. *Direct Preference Optimization: Your Language Model is Secretly a Reward Model*. NeurIPS 2023. [arXiv:2305.18290](https://arxiv.org/abs/2305.18290)
29. Chen et al. *On Extending Direct Preference Optimization to Accommodate Ties*. NeurIPS 2025. [arXiv:2409.17431](https://arxiv.org/abs/2409.17431)
30. Gao, Alon, Metzler. *Impact of Preference Noise on the Alignment Performance of Generative Language Models*. 2024. [arXiv:2404.09824](https://arxiv.org/abs/2404.09824)
31. Wang et al. *Secrets of RLHF in Large Language Models Part II: Reward Modeling*. 2024. [arXiv:2401.06080](https://arxiv.org/abs/2401.06080)
32. Deng et al. *Less is More: Improving LLM Alignment via Preference Data Selection*. NeurIPS 2025. [arXiv:2502.14560](https://arxiv.org/abs/2502.14560)
33. Llama Team, AI @ Meta. *The Llama 3 Herd of Models*. 2024. [arXiv:2407.21783](https://arxiv.org/abs/2407.21783)
34. Singhal et al. *A Long Way to Go: Investigating Length Correlations in RLHF*. COLM 2024. [arXiv:2310.03716](https://arxiv.org/abs/2310.03716)
35. Park et al. *Disentangling Length from Quality in Direct Preference Optimization*. Findings of ACL 2024. [arXiv:2403.19159](https://arxiv.org/abs/2403.19159)
36. Pan et al. *What Matters in Data for DPO?* NeurIPS 2025. [arXiv:2508.18312](https://arxiv.org/abs/2508.18312)
37. Abedjan et al. *Detecting Data Errors: Where are we and what needs to be done?* PVLDB 9(12), 2016. [PDF](https://www.vldb.org/pvldb/vol9/p993-abedjan.pdf)
38. Breck et al. *Data Validation for Machine Learning*. SysML 2019 (현 MLSys). [PDF](https://mlsys.org/Conferences/2019/doc/2019/167.pdf)
39. Chen, Zaharia, Zou. *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance*. TMLR 2024. [arXiv:2305.05176](https://arxiv.org/abs/2305.05176)
40. Atıl et al. *Non-Determinism of "Deterministic" LLM System Settings in Hosted Environments*. Eval4NLP 2025. [ACL Anthology](https://aclanthology.org/2025.eval4nlp-1.12) · [arXiv:2408.04667](https://arxiv.org/abs/2408.04667)
41. Zheng et al. *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*. NeurIPS 2023 Datasets and Benchmarks. [arXiv:2306.05685](https://arxiv.org/abs/2306.05685)
42. Wang et al. *Large Language Models are not Fair Evaluators*. ACL 2024. [ACL Anthology](https://aclanthology.org/2024.acl-long.511)
43. Koo et al. *Benchmarking Cognitive Biases in Large Language Models as Evaluators*. Findings of ACL 2024. [arXiv:2309.17012](https://arxiv.org/abs/2309.17012)
44. Northcutt, Athalye, Mueller. *Pervasive Label Errors in Test Sets Destabilize Machine Learning Benchmarks*. NeurIPS 2021 Datasets and Benchmarks. [arXiv:2103.14749](https://arxiv.org/abs/2103.14749)
45. Wang et al. *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. ACL 2023. [arXiv:2212.10560](https://arxiv.org/abs/2212.10560)
46. Yuan et al. *Self-Rewarding Language Models*. ICML 2024. [arXiv:2401.10020](https://arxiv.org/abs/2401.10020)
47. Cui et al. *UltraFeedback: Boosting Language Models with Scaled AI Feedback*. ICML 2024. [arXiv:2310.01377](https://arxiv.org/abs/2310.01377)
48. Tajwar et al. *Preference Fine-Tuning of LLMs Should Leverage Suboptimal, On-Policy Data*. ICML 2024. [arXiv:2404.14367](https://arxiv.org/abs/2404.14367)
49. Xu et al. *Is DPO Superior to PPO for LLM Alignment? A Comprehensive Study*. ICML 2024. [arXiv:2404.10719](https://arxiv.org/abs/2404.10719)
50. Sun et al. *Is On-Policy Data always the Best Choice for Direct Preference Optimization-based LM Alignment?* ICLR 2026. [arXiv:2508.10530](https://arxiv.org/abs/2508.10530)
51. Gerstgrasser et al. *Is Model Collapse Inevitable? Breaking the Curse of Recursion by Accumulating Real and Synthetic Data*. COLM 2024. [arXiv:2404.01413](https://arxiv.org/abs/2404.01413)
52. Panickssery, Bowman, Feng. *LLM Evaluators Recognize and Favor Their Own Generations*. NeurIPS 2024. [arXiv:2404.13076](https://arxiv.org/abs/2404.13076)
53. Brown et al. *Language Models are Few-Shot Learners*. NeurIPS 2020. [arXiv:2005.14165](https://arxiv.org/abs/2005.14165)
54. Inan et al. *Llama Guard: LLM-based Input-Output Safeguard for Human-AI Conversations*. 2023. [arXiv:2312.06674](https://arxiv.org/abs/2312.06674)
55. Rae et al. *Scaling Language Models: Methods, Analysis & Insights from Training Gopher*. 2021. [arXiv:2112.11446](https://arxiv.org/abs/2112.11446)
56. Fort, Sagot. *Influence of Pre-annotation on POS-tagged Corpus Development*. Linguistic Annotation Workshop (LAW IV) 2010. [ACL Anthology](https://aclanthology.org/W10-1807/)
