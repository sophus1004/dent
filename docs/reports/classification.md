<div align="center">

# DENT 기술 보고서: 분류 학습 데이터 정제

텍스트 분류 학습 데이터의 품질 문제는 모델을 어떻게 해치며, DENT는 왜 이 방법과 이 순서로 고치는가

[요약](#요약) · [서론](#1-서론) · [데이터와 검사](#2-데이터와-검사) · [문제와 피해](#3-문제마다-그대로-두면-무엇이-나빠지나) · [정제 과정](#4-정제-과정과-그-근거) · [설계 값](#5-설계-값과-그-출처) · [한계](#6-한계와-논의) · [참고 문헌](#참고-문헌)

[검색 보고서](retrieval.md) · [생성 보고서](generation.md) · [README](../../README.md)

</div>

| 항목 | 내용 |
|---|---|
| 대상 | DENT 0.1.0 분류 모듈(`classification`) |
| 데이터 | 문장(`text`)과 라벨(`label`)의 짝 |
| 작성일 | 2026-10-12 |

---

## 요약

텍스트 분류기는 학습 데이터에 있는 것을 그대로 배운다. 틀린 라벨, 같은 문장에 붙은 두 라벨, 몇몇 문장의 과한 반복,
한 하위 주제에 몰린 라벨까지 배우고, 그 결과는 정확도 하락 · 소수 라벨 무시 · 보지 못한 하위 주제에서의 실패로 나타난다.
DENT의 분류 모듈은 이 문제들을 일곱 가지 검사(라벨 충돌 · 중복 · 짧은 문장 · 라벨 균형 · 근접 중복 · 오라벨 의심 · 의미 쏠림)로 재고,
싼 판단부터 비싼 판단 순서(해시 → 규칙 → 임베딩 → 교차 검증 분류기 → 판정 모델 → LLM → 사람)로 고친다.

용어: **판정 모델**은 한 건마다 판정의 확률을 주는 모델(Jev)이다. **도우미**는 진단을 읽고 흐름 순서대로 규칙 · 판정 모델 · LLM으로 고치는 기능이고(채팅이 아니다),
끝난 뒤 남은 문제는 사람이 골라 **AI로 고치기**로 고친다. **문**은 새 데이터를 만들거나 다음 단계로 넘어가기 전에 앞 단계의 검사를 다시 재는 관문이다.
고친 것은 모두 기록하고 되돌릴 수 있다.

이 보고서는 검사마다 "그대로 두면 무엇이 나빠지는가"의 실증을, 과정의 원칙마다 "그렇게 하지 않으면 무엇이 잘못되는가"의 근거를 문헌에서 모았다.
아울러 어디까지가 문헌의 근거이고 어디부터가 DENT의 공학적 판단인지, 그리고 문헌이 DENT의 설계에 던지는 반론을 함께 적는다.

---

## 1. 서론

### 1.1 네 가지 전제

| 전제 | 근거 |
|---|---|
| **모델은 틀린 것도 배운다** | 신경망은 라벨을 100% 무작위로 바꾼 CIFAR-10도 훈련 오류 0까지 외우고, 시험 오류는 잡음이 늘수록 꾸준히 나빠져 완전 무작위면 찍기 수준이 된다[1]. 쉬운 패턴을 먼저 배우고 잡음은 나중에 외운다[2] |
| **양보다 질** | 지시 데이터 52k를 9k로 거른 쪽이 원래 데이터보다 나았다[3]. 웹 데이터 15T 토큰에서 교육적인 글 1.3T만 거른 데이터가 MMLU를 33%에서 37%로 올렸고, 다른 공개 데이터셋(Matrix)의 최종 MMLU에 약 1/10의 토큰으로 이르렀다[4] |
| **문제는 늦게, 뒤에서 커진다** | 고위험 AI 실무자 53명 중 92%가 데이터 문제가 뒤 단계에서 커지는 일(data cascade)을 겪었다[5]. 데이터 오류는 퍼지기 전에 일찍 잡아야 원인을 찾고 되돌릴 수 있다[6] |
| **지나친 거르기도 손해다** | 거를 양은 학습 계산량과 함께 정해야 하고[7], 학습하기 애매한 예가 분포 밖 일반화에 가장 크게 기여한다[8] |

네 전제를 합치면 DENT의 방침이 나온다: **학습 전에 모델을 해치는 문제를 찾아 고치되, 확실한 것만 자동으로 고치고 애매한 것은 사람이 고른다.**

### 1.2 이 보고서가 답하는 질문

1. 분류 데이터의 어떤 문제가 모델을 해치는가, 그 피해를 잰 실증이 있는가
2. 왜 이 방법과 이 순서로 고치는가
3. 어디까지가 근거이고 어디부터가 판단인가

### 1.3 근거의 등급

주장마다 근거의 성격을 표시한다.

| 표시 | 뜻 |
|---|---|
| **[직접]** | 텍스트 분류(또는 같은 종류의 과제)에서 잰 실증이 있다 |
| **[간접]** | 다른 영역(언어 모델 사전학습 · 이미지 분류)에서 잰 실증을 빌린다 |
| **[판단]** | 학술 근거를 찾지 못했고, 기전에 기댄 공학적 판단이다 |

---

## 2. 데이터와 검사

### 2.1 데이터 모양

한 줄은 문장 하나와 라벨 하나다. DENT는 학습 · 검증 · 평가로 나누지 않고 모든 줄을 한 덩어리로 정리한다(원본에 분할이 있어도 합친다).
같은 문장인지는 유니코드 NFC 정규화와 공백 줄이기를 거친 문장의 SHA-256으로 가린다. 공백의 수 · 종류(탭 · 줄바꿈)나 앞뒤 공백,
자모 조합 방식만 다른 문장은 같은 문장이다. 띄어 쓴 자리가 다르면 다른 문장이다.

### 2.2 일곱 가지 검사

| 검사 | 정의 | 등급 | 재는 방법 |
|---|---|---|---|
| 라벨 충돌 | 같은 문장에 라벨이 둘 이상 | 0 통과 · 1% 미만 주의 · 그 위 심각 | 해시 묶음 |
| 중복 | 문장 · 라벨이 모두 같은 줄의 여분 | 2% 미만 통과 · 10% 미만 주의 | 해시 묶음 |
| 짧은 문장 | 5자 미만 | 있으면 주의 | 길이 |
| 라벨 균형 | 가장 많은 라벨 ÷ 가장 적은 라벨 | 1.5 이하 통과 · 2.5 이하 주의 | 집계 |
| 근접 중복 | 임베딩 코사인 ≥ 0.92인 쌍(이웃 10개 안) | 라벨이 다른 쌍이 있으면 주의 | 임베딩 |
| 오라벨 의심 | 교차 검증 분류기가 지금 라벨 확률 < 0.2, 다른 라벨 ≥ 0.5로 본 문장(문장 5개 미만 라벨은 재지 않음) | 대기 비율 1% 미만 통과 · 3% 미만 주의 | 임베딩 + 분류기 + 판정 모델 |
| 의미 쏠림 | 라벨마다 KMeans 8무리 중 가장 큰 무리 ≥ 40%(문장 40개 미만 라벨은 재지 않음) | 쏠린 라벨이 있으면 주의 | 임베딩 + 군집 |

앞의 넷은 글의 모양만 보고 진단을 열 때마다 SQL 집계로 잰다. 뒤의 셋은 임베딩이 필요한 **뜻 분석**에서 잰다.
오라벨 의심은 분류기가 고른 후보를 판정 모델에 한 번 더 묻고(최대 2,000건), 판정 모델도 다른 라벨을 고르고 지금 라벨 확률을 0.2 미만으로 보면 '확인'으로 둔다.

---

## 3. 문제마다: 그대로 두면 무엇이 나빠지나

### 3.1 오라벨 · 라벨 충돌

**기전.** 틀린 라벨은 모델에게 틀린 정답을 가르친다. 같은 입력에 정답이 둘이면 교차 엔트로피를 가장 낮추는 예측이 두 라벨의 비율이 되어,
그 문장과 비슷한 문장에서 확신이 흔들린다.

**근거.**
- 신경망은 무작위 라벨도 훈련 오류 0까지 외운다[1]. **[간접]**
- BERT는 **무작위로** 넣은 잡음에는 강해서, 잡음 70%에서도 조기 종료로 약 10%p만 떨어졌다. 그러나 규칙으로 단 라벨처럼 입력에 따라 생기는
  **체계적** 잡음에서는 깨끗한 라벨 대비 최대 35%p 떨어졌고, 오래 학습할수록 잡음을 외워 더 나빠졌다[9]. **[직접]**
- 오라벨을 찾아 지운 뒤 학습하면 나아졌다: CIFAR-10 잡음 40%에서 57~61% → 83~87%, Amazon 리뷰 텍스트(임베딩 + 로지스틱 회귀)에서 84.4% → 90.2%
  (저자가 의심 예를 모아 잡음을 약 24%로 높인 설정, 50에폭)[10]. **[직접]**
- 평가 데이터의 라벨 오류는 모델 비교를 뒤집는다. 시험셋 10개의 평균 오류는 3.3% 이상이었고,
  틀린 시험 예가 6%만 늘어도 ImageNet에서 ResNet-18이 ResNet-50을 앞섰다[11]. **[간접]**
- 라벨 충돌(같은 문장 · 다른 라벨)만 따로 잰 실증은 찾지 못했다. 같은 겉모양에 다른 라벨이 붙은 것을 오류 후보로 보는 방법은
  주석 오류 탐지의 오래된 갈래다[12]. **[판단]**

**한계.** 라벨이 엇갈리는 까닭이 실수가 아니라 문장 자체의 애매함일 수 있다. 사람 사이의 이견을 라벨 분포(soft label)로 학습하는 편이
다수결보다 낫다는 연구가 있다[13][14]. 6장에서 다시 다룬다.

### 3.2 중복

**기전.** 같은 줄이 여러 번 들어가면 그 줄이 손실에서 여러 배의 가중을 받는다. 몇몇 예만 많이 되풀이되면 모델 용량이 그 예를 외우는 데 쓰인다.

**근거.**
- 데이터의 0.1%를 100번 되풀이하면(나머지 90%의 토큰은 고유) 800M 모델의 성능이 400M 모델 수준으로 떨어졌다[15]. **[간접]**
- 중복을 지운 언어 모델은 외운 글을 1/10로 내면서 정확도는 같거나 나았다[16]. **[간접]**
- 반대로 데이터 **전체**를 고르게 4번까지 되풀이하는 것은 고유 데이터와 손실 차이가 거의 없었다[17].
  해가 되는 것은 '일부만 많이'의 반복이고, 중복 검사가 찾는 것이 바로 이 모양이다. **[간접]**

### 3.3 근접 중복

**기전.** 표현만 조금 다른 같은 뜻의 문장이 여러 번 들어가면, 계산이 낭비되고 그 뜻에 가중이 몰린다.

**근거.**
- 임베딩 공간에서 코사인 문턱으로 근접 중복을 지운 결과, 이미지-글 데이터의 50%를 지워도 성능 손실이 거의 없었고 학습 시간은 절반이 되었다[18]. **[간접]**
- 글자 기준 중복 제거(MinHash)를 한 데이터에서 임베딩 근접 중복을 지우고 군집으로 다양하게 고른 데이터가 6.7B 언어 모델의 학습 효율을 20% 높였다[19]. **[간접]**

**한계.** 라벨이 **다른** 근접 쌍은 경계를 알려 주는 정보일 수 있다. 평균 성능이 좋은 지우기 방법이 가장 약한 클래스의 정확도를 크게 떨어뜨렸다는 보고가 있다[20].
DENT는 라벨이 같은 쌍만 규칙으로 하나를 남기고, 라벨이 다른 쌍은 LLM과 사람이 본다(4.2절).

### 3.4 짧은 문장

**기전.** 몇 글자짜리 문장은 라벨을 가를 근거를 거의 담지 못해, 모델에게는 잡음에 가깝다.

**근거.** 텍스트 분류에서 짧은 문장의 피해를 따로 잰 실증은 찾지 못했다. 웹 데이터 정제에서 짧은 줄을 버리는 규칙이 쓰였으나[21],
규칙 묶음 전체의 효과라 이 검사에 직접 대응하지 않는다. **[판단]**

**한계.** 짧아도 뜻이 분명한 문장(의도 분류의 "환불")은 실제 입력의 일부다. 그래서 DENT는 짧은 문장을 일괄로 지우지 않고 주의로만 두며,
도우미의 LLM이 보고 뜻이 없다고 판단한 것을 뺀다.

### 3.5 라벨 불균형

**기전.** 손실이 많은 라벨에 쏠려 적은 라벨을 덜 맞히고, 극단에서는 적은 라벨을 아예 예측하지 않는다.

**근거.**
- MNIST · CIFAR-10 · ImageNet에서 불균형은 성능을 해쳤고, 적은 라벨을 늘리는 것(오버샘플링)이 거의 모든 경우에 가장 나았다.
  이 연구의 불균형 비율 정의(가장 많은 라벨 수 ÷ 가장 적은 라벨 수)는 DENT의 라벨 균형과 같다[22]. **[간접]**
- 대부분의 학습기가 많은 라벨로 치우치며, 딥러닝에서의 실증은 아직 적다[23]. **[간접]**

**한계.** 운영 환경의 분포가 원래 불균형하면 균형을 맞춰 학습한 모델이 적은 라벨을 지나치게 예측할 수 있다.
DENT가 완전한 균형이 아니라 목표 배율(1.2 · 1.5 · 2배)까지만 채우는 이유다.

### 3.6 의미 쏠림

**기전.** 한 라벨의 문장이 한 하위 주제에 몰리면 모델은 "이 주제 = 이 라벨"이라는 지름길을 배운다.
학습에서 보지 못한 하위 주제의 문장이 오면 라벨이 아니라 주제를 보고 틀린다.

**근거.**
- 지름길 학습: 분포 안에서는 잘 되다가 분포 밖에서 무너지는 규칙을 신경망이 즐겨 배운다[24]. **[간접]**
- 자연어 추론 데이터에서 가설 문장만 보고도 SNLI의 67%를 맞혔다. 주석자가 남긴 낱말 패턴이 지름길이 된 것이다[25]. **[직접]**
- 이 지름길이 통하지 않는 예에서 MNLI로 학습한 모델들은 대부분 정확도 10% 미만이었다(찍기는 50%)[26]. **[직접]**
- 클래스의 하위 집단 일부로만 학습하면 보지 못한 하위 집단에서 정확도가 대부분 30% 넘게 떨어졌다[27]. 의미 쏠림과 가장 가까운 실험이다. **[간접]**

**고친 효과.** 지름길이 통하지 않는 예를 학습에 더하면 그런 예에서 크게 나아졌다[26]. DENT는 쏠림을 AI로 고칠 수 없는 문제로 두고
"데이터 더하기"를 할 일로 보인다. LLM이 만든 문장은 보기로 준 문장의 하위 주제를 따라가기 쉬워, 새 문장으로는 쏠림을 풀기 어렵다(6장).

### 3.7 새 문장으로 균형 맞추기

**효과.** 라벨마다 문장을 만들고 원본으로 학습한 분류기로 거른 데이터가, 라벨당 5개뿐인 ATIS에서 BERT 정확도를 53.3% → 75.7%로 올렸다.
이득은 라벨당 50개 이하에서 났다[28]. **[직접]**

**위험.** 합성 데이터만으로 학습하면 거의 모든 과제에서 실제 데이터보다 낮았고, 주관적인 과제일수록 차이가 컸다[29].
라벨만 주고 만든 합성 문장은 다양성이 낮고 치우친다[30]. **[직접]**

**거르기의 효과.** 생성 확률로 거른 합성 데이터가 같은 수를 무작위로 고른 것보다 GLUE 평균이 높았다[31]. **[직접]**

---

## 4. 정제 과정과 그 근거

### 4.1 전체 흐름

```
가져오기 ─ 진단(모양 검사 4) ─ 뜻 분석(근접 중복 · 오라벨 의심 · 의미 쏠림) ─ 제안([수락] · [유지])
   └─ 도우미: 계획 → 라벨 충돌 → 오라벨 의심 → 짧은 문장 → 중복 → 근접 중복 → 보고
        └─ 남은 것(사람이 고름) → AI로 고치기 · 새 문장(라벨 균형) ─ 내보내기(출처 칸 포함)
```

### 4.2 싼 판단부터: 해시 → 규칙 → 임베딩 → 분류기 → 판정 모델 → LLM → 사람

| 층 | 맡는 일 | 잘 잡는 것 | 놓치는 것 |
|---|---|---|---|
| 해시 | 중복 · 라벨 충돌 | 글자가 같은 것 | 한 글자라도 다르면 |
| 규칙 | 짧은 문장 · 라벨 균형 재기, 중복 여분 빼기, 같은 라벨 근접 중복 하나 남기기 | 모양 | 뜻 |
| 임베딩 + 이웃 | 근접 중복, 의미 지도 | 바꿔 쓴 문장 | 맞고 틀림 |
| 분류기(교차 검증) | 오라벨 후보 | 데이터 전체에서 튀는 것 | 한 건마다의 근거 |
| 판정 모델 | 후보 확인 · 충돌 무리의 라벨 고르기 · 새 문장의 라벨 확인 | 한 건마다의 확률 | 애매한 문장 |
| LLM | 남은 애매한 것만 판단 · 새 문장 만들기 | 문맥 판단 | 비싸고 실행마다 다름 |
| 사람 | 제안 수락 · 남은 것 고르기 | 뜻에 대한 최종 판단 | 느림 |

**하지 않으면.** 판단이 필요 없는 일까지 비싼 층에 맡기면 비용이 들고, 같은 입력에도 결과가 달라져 재현할 수 없다.

**근거.**
- 단순한 분류기로 대부분을 먼저 거르고 복잡한 분류기는 남은 것만 보는 계단 구조[32], 싼 LLM부터 부르는 계단으로 같은 성능을 최대 98% 적은 비용에 낸 결과[33].
- 글자 기준 중복을 지운 데이터에 임베딩 근접 중복 제거 → 군집을 더한 데이터 정제가 언어 모델 학습 효율을 높였다[19]. DENT의 해시 → 임베딩 → 군집 순서와 같은 모양이다.
- 기계가 먼저 거칠게 훑고 사람은 그럴듯한 것만 확인하는 혼합 방식이, 기계만 · 사람만보다 효율과 정확도가 모두 나았다[34].
- 같은 입력에 같은 답을 내도록(결정적으로) 설정한 LLM도 실행마다 정확도가 최대 15% 달랐다[35]. 규칙으로 되는 일을 LLM에 맡기지 않는 이유다.

**한계.** 규칙이 판단보다 낫다는 근거는 없다. 판단이 필요 없는 고치기에만 규칙을 쓴다는 **범위 제한**이 핵심이다.

### 4.3 두 판단이 같을 때만 바꾼다

오라벨 의심은 분류기와 판정 모델이 같은 다른 라벨을 고르고 판정 모델이 지금 라벨 확률을 0.2 미만으로 볼 때만 규칙으로 바꾸고,
판정 모델이 지금 라벨을 고르면 유지한다. 둘이 엇갈리거나 판정 모델의 확신이 약하면 LLM이 보고,
LLM도 애매하면 도우미 실행에서는 보류하고 끝난 뒤의 AI로 고치기에서는 지금 라벨을 유지한다.

**근거.**
- 교차 검증 분류기로 오라벨을 거르는 연구에서, 여러 분류기가 모두 동의할 때만 지우는 합의 필터는 좋은 데이터를 덜 버리는 보수적인 방식이었다[36].
- 오류가 서로 독립인 분류기들의 합의는 한 분류기보다 덜 틀린다[37]. 분류기(임베딩 위의 통계 모델)와 판정 모델(문장을 직접 읽는 모델)은 틀리는 까닭이 다르다.
- 자동으로 짚은 라벨 오류 후보 가운데 사람이 확인한 실제 오류는 평균 51%였다[11]. 한 판단만으로 바꾸면 맞는 라벨의 절반 가까이를 바꿀 수 있다.

### 4.4 흐름 순서와 닿을 때 재기

도우미는 라벨 충돌 → 오라벨 의심 → 짧은 문장 → 중복 → 근접 중복 순서로 돈다. 순서는 LLM이 아니라 모듈이 정하고,
각 단계는 실행 처음이 아니라 **그 단계에 닿은 때** 다시 재서 돌지 정한다.

**하지 않으면.** 앞 단계의 고치기가 뒤 단계의 문제를 만들거나 없앤다. 라벨 충돌을 하나로 통일하면 같은 문장 · 같은 라벨의 새 중복이 생기고,
오라벨을 바꾸면 근접 중복 쌍의 라벨 관계가 바뀐다. 처음에 잰 값으로 판단하면 이미 풀린 문제를 고치거나 새로 생긴 문제를 놓친다.

**근거.**
- 여러 오류 탐지 도구를 돌리는 **순서**가 결과를 크게 바꿨다. 순서를 정해 돌린 쪽이 모든 결과를 합친 것보다 정밀도가 평균 28% 높았다[38].
- 일부만 고친 데이터로 판단하면 거짓 추세(심슨의 역설)가 나와 고치기가 도움이 됐는지 알 수 없다[39]. 시스템의 무엇이든 바꾸면 모든 것이 바뀐다[40].

**한계.** "충돌 → 중복" 순서 자체를 실험한 문헌은 없다. 고치기가 만드는 문제를 뒤 단계가 받게 둔다는 것은 기전에 기댄 판단이다. **[판단]**

### 4.5 문: 심각이 남으면 새 문장을 만들지 않는다

새 문장(라벨 균형)은 다른 검사에 심각이 남아 있으면 만들지 않는다. 만들 때는 그 라벨의 학습에 쓰는 문장을 보기로 주고,
임베딩이 연결돼 있으면 그 라벨의 학습에 쓰는 문장(최대 2만) · 앞서 만든 문장과 코사인 ≥ 0.92인 것을 버리고,
판정 모델이 연결돼 있으면 목표 라벨을 고르고 그 확률이 0.8 이상인 것만 더한다.

**하지 않으면.** 오라벨 · 충돌이 남은 문장을 보기로 삼으면 그 결함이 새 문장으로 번진다. 같은 보기를 되풀이하면 주제가 쏠린다.

**근거.**
- LLM이 스스로 만든 지시 데이터 200건 중 모든 칸이 맞은 것은 54%였다[41]. 만든 데이터는 다시 걸러야 한다.
- 원본의 라벨 잡음이 클수록 합성 데이터의 질과 쓸모가 떨어졌다(표 데이터 합성기)[42]. **[간접]**
- 이미 있는 지시와 겹치는(ROUGE-L ≥ 0.7) 생성 지시를 버려 다양성을 지켰다[41]. DENT는 같은 생각을 임베딩 유사도로 옮겼다.
- 원본으로 학습한 분류기의 확신으로 생성 문장을 거른 방식[28]을, DENT는 판정 모델의 확률로 한다.

**한계.** 모델 붕괴 문헌[43]은 생성 데이터로 **되풀이해** 학습하는 경우라 한 번 더하는 경우에는 직접 해당하지 않는다.
다만 AI로 고치기로 새 문장을 되풀이해 만들면 앞서 만든 새 문장이 다음 보기가 될 수 있다.
"심각할 때만 막는다"는 경계는 판단이다. **[판단]**

### 4.6 만든 것은 드러내고, 고친 것은 되돌린다

- **출처.** 새 문장은 출처를 '새 문장'으로 적고, 내보내는 파일에는 `source`(original · synthetic) 칸을 넣는다.
  진짜 데이터를 버리지 않고 합성 데이터를 쌓으면 붕괴를 피하지만[44], 그러려면 무엇이 합성인지 가를 수 있어야 한다.
  데이터셋 문서화는 수집 과정과 출처를 적으라고 권한다[45].
- **제안과 사람.** 뜻 분석의 오라벨 의심은 [수락]하기 전에는 데이터를 바꾸지 않는다. 오류 탐지는 사람이 볼 후보를 좁히는 일이다[12].
- **기록과 되돌리기.** 도우미가 바꾼 것은 전 · 후를 남기고 카드 하나 또는 실행 전체 단위로 되돌린다.
  도우미는 문장을 지우지 않고 학습에서 뺄 뿐이며, 되돌리면 다시 들어간다(되돌린 새 문장은 휴지통으로 간다).
  사람이 지운 문장도 먼저 휴지통에 가고, [휴지통 비우기]만 영구히 지운다.
  오류를 일찍 잡으면 파이프라인을 작동하던 상태로 되돌릴 수 있다[6]. 되돌리기가 모델 품질을 올린다는 실증은 없고, 감사 · 재현성의 근거다. **[판단]**

---

## 5. 설계 값과 그 출처

| 값 | DENT | 출처 |
|---|---|---|
| 교차 검증 겹 수 | 5 (층화) | 오라벨 탐지 라이브러리 cleanlab의 기본값과 같다. 원 논문은 이미지 실험에 4겹, 텍스트(로지스틱 회귀) 실험에 5겹을 썼다[10] |
| 분류기 | 임베딩 위의 로지스틱 회귀 | 확률을 내는 어떤 모델이든 쓸 수 있다는 틀[10] |
| 오라벨 의심 문턱 | 지금 라벨 < 0.2 · 다른 라벨 ≥ 0.5 | **DENT가 정함.** 원 논문은 라벨마다 평균 자기 확신을 문턱으로 쓴다[10] |
| 근접 중복 | 코사인 ≥ 0.92, 이웃 10 | **DENT가 정함**(bge-m3 기준). 문헌도 문턱을 데이터 · 모델마다 맞춘다[18] |
| 의미 쏠림 | KMeans 8무리 · 가장 큰 무리 ≥ 40% | 8은 scikit-learn의 기본 무리 수와 같다. 40%는 **DENT가 정함** |
| 판정 모델 기준 | 충돌 통일 · 새 문장 통과는 ≥ 0.8, 오라벨 '확인'은 다른 라벨을 고르고 지금 라벨 < 0.2 | **DENT가 정함** |
| 라벨 균형 | 1.5 이하 통과 · 2.5 이하 주의, 목표 배율 1.2 · 1.5 · 2 | 비율 정의는 [22]와 같다. 경계는 **DENT가 정함** |
| 중복 · 충돌 · 오라벨 경계 | 2% · 10%, 1%, 1% · 3% | **DENT가 정함** |
| 짧은 문장 | 5자 미만 | **DENT가 정함** |
| 새 문장 상한 | 라벨당 100 · 모두 300, 보내는 앞선 문장 30개 | **DENT가 정함** |

숫자 문턱 대부분은 문헌에서 가져온 값이 아니다. 문헌도 이런 문턱은 데이터와 모델마다 맞추라고 한다.
근접 중복 · 오라벨 문턱은 bge-m3 임베딩으로 한국어 문장 표본과 내장 예시 데이터(문제를 심고 정답지를 둔 데이터)에서 맞췄고,
검사의 등급 경계는 화면의 ⓘ에서 볼 수 있다.

---

## 6. 한계와 논의

1. **라벨 충돌을 하나로 통일하는 것은 '정답은 하나'라는 가정이다.** 사람 사이의 이견은 정보일 수 있고, 라벨 분포로 학습하면
   분포 밖 일반화와 강건성이 나아졌다[13][14]. DENT는 통일하되, 두 라벨 모두 맞을 만큼 애매한 문장은 도우미 실행에서는 보류로 사람에게 넘기고,
   AI로 고치기에서는 학습에서 뺀다(되돌릴 수 있다).
2. **무작위 잡음과 체계적 잡음의 피해는 다르다.** 균일 잡음은 데이터만 충분하면 크게 해롭지 않지만[46], 입력에 따라 생기는 잡음은 크게 해롭다[9].
   충돌 · 오라벨 의심이 찾는 것은 후자에 가깝다.
3. **지나친 거르기.** 애매한 예가 일반화에 기여하므로[8] 오라벨 의심을 모두 지우면 손해일 수 있다. DENT가 의심을 제안으로 두고 바로 지우지 않는 이유다.
4. **새 문장의 주제 되풀이.** 앞서 만든 문장 일부만 "다시 쓰지 않을 것"으로 보내므로, 많이 만들면 앞쪽 주제를 표현만 바꿔 되풀이할 수 있다.
   하위 주제 · 문체 · 길이 같은 속성을 바꿔 가며 묻는 방법[30]이 직접적인 해법이다.
5. **사람 확인의 편향.** 사람도 제안을 따라가는 자동화 편향이 있다. 틀린 조언이 주어지면 잘못된 결정의 위험이 1.26배였다[47].
   제안을 어떻게 보이는지도 설계 대상이다.
6. **근거의 영역.** 중복 · 근접 중복 · 불균형 · 하위 집단의 근거는 언어 모델 사전학습과 이미지 분류에서 왔다.
   텍스트 분류에서 직접 잰 근거는 라벨 잡음 · 지름길 · 합성 데이터에 있다.

---

## 7. 결론

분류 데이터의 일곱 가지 문제는 모두 "모델이 데이터에 있는 것을 그대로 배운다"는 한 기전으로 모델을 해친다. 틀린 라벨은 틀린 정답이 되고,
반복은 가중이 되고, 쏠림은 지름길이 된다. DENT는 이 문제를 싼 판단부터 비싼 판단 순서로 고치고, 두 판단이 같을 때만 바꾸며,
흐름 순서대로 닿은 때 다시 재고, 심각이 남으면 새 데이터를 만들지 않으며, 만든 것은 드러내고 고친 것은 되돌린다.
이 원칙들은 데이터 정제 · 라벨 잡음 · 합성 데이터 문헌의 실증과 맞물려 있고, 숫자 문턱과 일부 순서는 공학적 판단으로 남아 있다.

---

## 참고 문헌

본문의 수치는 각 문헌의 본문 또는 초록에서 옮겼다. 학회 발표가 확인되지 않은 것은 arXiv로 적었다.

1. Zhang et al. *Understanding Deep Learning Requires Rethinking Generalization*. ICLR 2017. [arXiv:1611.03530](https://arxiv.org/abs/1611.03530)
2. Arpit et al. *A Closer Look at Memorization in Deep Networks*. ICML 2017. [arXiv:1706.05394](https://arxiv.org/abs/1706.05394)
3. Chen et al. *AlpaGasus: Training a Better Alpaca with Fewer Data*. ICLR 2024. [arXiv:2307.08701](https://arxiv.org/abs/2307.08701)
4. Penedo et al. *The FineWeb Datasets: Decanting the Web for the Finest Text Data at Scale*. NeurIPS 2024 Datasets and Benchmarks. [arXiv:2406.17557](https://arxiv.org/abs/2406.17557)
5. Sambasivan et al. *"Everyone wants to do the model work, not the data work": Data Cascades in High-Stakes AI*. CHI 2021. [Google Research](https://research.google/pubs/everyone-wants-to-do-the-model-work-not-the-data-work-data-cascades-in-high-stakes-ai/)
6. Breck et al. *Data Validation for Machine Learning*. SysML 2019 (현 MLSys). [PDF](https://mlsys.org/Conferences/2019/doc/2019/167.pdf)
7. Goyal et al. *Scaling Laws for Data Filtering — Data Curation cannot be Compute Agnostic*. CVPR 2024. [arXiv:2404.07177](https://arxiv.org/abs/2404.07177)
8. Swayamdipta et al. *Dataset Cartography: Mapping and Diagnosing Datasets with Training Dynamics*. EMNLP 2020. [arXiv:2009.10795](https://arxiv.org/abs/2009.10795)
9. Zhu et al. *Is BERT Robust to Label Noise? A Study on Learning with Noisy Labels in Text Classification*. Insights from Negative Results in NLP (ACL 2022 Workshop). [arXiv:2204.09371](https://arxiv.org/abs/2204.09371)
10. Northcutt, Jiang, Chuang. *Confident Learning: Estimating Uncertainty in Dataset Labels*. JAIR 70, 2021. [arXiv:1911.00068](https://arxiv.org/abs/1911.00068)
11. Northcutt, Athalye, Mueller. *Pervasive Label Errors in Test Sets Destabilize Machine Learning Benchmarks*. NeurIPS 2021 Datasets and Benchmarks. [arXiv:2103.14749](https://arxiv.org/abs/2103.14749)
12. Klie, Webber, Gurevych. *Annotation Error Detection: Analyzing the Past and Present for a More Coherent Future*. Computational Linguistics 49(1), 2023. [arXiv:2206.02280](https://arxiv.org/abs/2206.02280)
13. Uma et al. *Learning from Disagreement: A Survey*. JAIR 72, 2021. [DOI](https://doi.org/10.1613/jair.1.12752)
14. Peterson et al. *Human Uncertainty Makes Classification More Robust*. ICCV 2019. [arXiv:1908.07086](https://arxiv.org/abs/1908.07086)
15. Hernandez et al. *Scaling Laws and Interpretability of Learning from Repeated Data*. 2022. [arXiv:2205.10487](https://arxiv.org/abs/2205.10487)
16. Lee et al. *Deduplicating Training Data Makes Language Models Better*. ACL 2022. [arXiv:2107.06499](https://arxiv.org/abs/2107.06499)
17. Muennighoff et al. *Scaling Data-Constrained Language Models*. NeurIPS 2023. [arXiv:2305.16264](https://arxiv.org/abs/2305.16264)
18. Abbas et al. *SemDeDup: Data-efficient Learning at Web-scale through Semantic Deduplication*. 2023. [arXiv:2303.09540](https://arxiv.org/abs/2303.09540)
19. Tirumala et al. *D4: Improving LLM Pretraining via Document De-Duplication and Diversification*. NeurIPS 2023 Datasets and Benchmarks. [arXiv:2308.12284](https://arxiv.org/abs/2308.12284)
20. Vysogorets et al. *DRoP: Distributionally Robust Data Pruning*. ICLR 2025. [arXiv:2404.05579](https://arxiv.org/abs/2404.05579)
21. Raffel et al. *Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer*. JMLR 21(140), 2020. [arXiv:1910.10683](https://arxiv.org/abs/1910.10683)
22. Buda, Maki, Mazurowski. *A Systematic Study of the Class Imbalance Problem in Convolutional Neural Networks*. Neural Networks 106, 2018. [arXiv:1710.05381](https://arxiv.org/abs/1710.05381)
23. Johnson, Khoshgoftaar. *Survey on Deep Learning with Class Imbalance*. Journal of Big Data 6, 2019. [DOI](https://doi.org/10.1186/s40537-019-0192-5)
24. Geirhos et al. *Shortcut Learning in Deep Neural Networks*. Nature Machine Intelligence 2, 2020. [DOI](https://doi.org/10.1038/s42256-020-00257-z)
25. Gururangan et al. *Annotation Artifacts in Natural Language Inference Data*. NAACL 2018. [ACL Anthology](https://aclanthology.org/N18-2017)
26. McCoy, Pavlick, Linzen. *Right for the Wrong Reasons: Diagnosing Syntactic Heuristics in Natural Language Inference*. ACL 2019. [ACL Anthology](https://aclanthology.org/P19-1334)
27. Santurkar, Tsipras, Madry. *BREEDS: Benchmarks for Subpopulation Shift*. ICLR 2021. [arXiv:2008.04859](https://arxiv.org/abs/2008.04859)
28. Anaby-Tavor et al. *Do Not Have Enough Data? Deep Learning to the Rescue!* AAAI 2020. [arXiv:1911.03118](https://arxiv.org/abs/1911.03118)
29. Li et al. *Synthetic Data Generation with Large Language Models for Text Classification: Potential and Limitations*. EMNLP 2023. [arXiv:2310.07849](https://arxiv.org/abs/2310.07849)
30. Yu et al. *Large Language Model as Attributed Training Data Generator: A Tale of Diversity and Bias*. NeurIPS 2023 Datasets and Benchmarks. [arXiv:2306.15895](https://arxiv.org/abs/2306.15895)
31. Meng et al. *Generating Training Data with Language Models: Towards Zero-Shot Language Understanding*. NeurIPS 2022. [arXiv:2202.04538](https://arxiv.org/abs/2202.04538)
32. Viola, Jones. *Rapid Object Detection using a Boosted Cascade of Simple Features*. CVPR 2001. [DOI](https://doi.org/10.1109/CVPR.2001.990517)
33. Chen, Zaharia, Zou. *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance*. TMLR 2024. [arXiv:2305.05176](https://arxiv.org/abs/2305.05176)
34. Wang et al. *CrowdER: Crowdsourcing Entity Resolution*. PVLDB 5(11), 2012. [arXiv:1208.1927](https://arxiv.org/abs/1208.1927)
35. Atıl et al. *Non-Determinism of "Deterministic" LLM System Settings in Hosted Environments*. Eval4NLP 2025. [ACL Anthology](https://aclanthology.org/2025.eval4nlp-1.12) · [arXiv:2408.04667](https://arxiv.org/abs/2408.04667)
36. Brodley, Friedl. *Identifying Mislabeled Training Data*. JAIR 11, 1999. [arXiv:1106.0219](https://arxiv.org/abs/1106.0219)
37. Dietterich. *Ensemble Methods in Machine Learning*. Multiple Classifier Systems (MCS) 2000, LNCS 1857. [DOI](https://doi.org/10.1007/3-540-45014-9_1)
38. Abedjan et al. *Detecting Data Errors: Where are we and what needs to be done?* PVLDB 9(12), 2016. [PDF](https://www.vldb.org/pvldb/vol9/p993-abedjan.pdf)
39. Krishnan et al. *ActiveClean: Interactive Data Cleaning for Statistical Modeling*. PVLDB 9(12), 2016. [PDF](https://www.vldb.org/pvldb/vol9/p948-krishnan.pdf)
40. Sculley et al. *Hidden Technical Debt in Machine Learning Systems*. NeurIPS 2015. [PDF](https://proceedings.neurips.cc/paper/2015/file/86df7dcfd896fcaf2674f757a2463eba-Paper.pdf)
41. Wang et al. *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. ACL 2023. [arXiv:2212.10560](https://arxiv.org/abs/2212.10560)
42. Kim, Huang, Liu. *An Empirical Study on Impact of Label Noise on Synthetic Tabular Data Generation*. Machine Learning 114, 2025. [DOI](https://doi.org/10.1007/s10994-024-06629-5)
43. Shumailov et al. *AI Models Collapse When Trained on Recursively Generated Data*. Nature 631, 2024. [DOI](https://doi.org/10.1038/s41586-024-07566-y)
44. Gerstgrasser et al. *Is Model Collapse Inevitable? Breaking the Curse of Recursion by Accumulating Real and Synthetic Data*. COLM 2024. [arXiv:2404.01413](https://arxiv.org/abs/2404.01413)
45. Gebru et al. *Datasheets for Datasets*. Communications of the ACM 64(12), 2021. [arXiv:1803.09010](https://arxiv.org/abs/1803.09010)
46. Rolnick et al. *Deep Learning is Robust to Massive Label Noise*. 2017. [arXiv:1705.10694](https://arxiv.org/abs/1705.10694)
47. Goddard et al. *Automation Bias: A Systematic Review of Frequency, Effect Mediators, and Mitigators*. JAMIA 19(1), 2012. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3240751)
