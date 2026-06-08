from __future__ import annotations

from datetime import date, datetime
import hashlib
import random
from zoneinfo import ZoneInfo

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
MONTH_BRANCHES = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
HOUR_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
ELEMENT = {"甲":"목","乙":"목","寅":"목","卯":"목","丙":"화","丁":"화","巳":"화","午":"화","戊":"토","己":"토","辰":"토","戌":"토","丑":"토","未":"토","庚":"금","辛":"금","申":"금","酉":"금","壬":"수","癸":"수","亥":"수","子":"수"}
DAY_STEM_TRAITS = {"甲":"직진형 리더", "乙":"유연한 전략가", "丙":"에너지 발산형", "丁":"섬세한 집중형", "戊":"안정 추구형", "己":"실용적 관리형", "庚":"원칙주의자", "辛":"예민한 완벽주의", "壬":"흐름 타는 전략가", "癸":"깊이 있는 사색형"}
CONTROLS = {"목":"금", "화":"수", "토":"목", "금":"화", "수":"토"}
GENERATES = {"목":"수", "화":"목", "토":"화", "금":"토", "수":"금"}
DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]
ZODIAC = [(120,"염소자리"),(219,"물병자리"),(321,"물고기자리"),(420,"양자리"),(521,"황소자리"),(621,"쌍둥이자리"),(723,"게자리"),(823,"사자자리"),(923,"처녀자리"),(1023,"천칭자리"),(1122,"전갈자리"),(1222,"사수자리"),(1232,"염소자리")]
SIGN_META = {
    "양자리": ("불", "시작", "빠른 결단과 선점"), "사자자리": ("불", "고정", "존재감과 주도권"), "사수자리": ("불", "변화", "확장과 이동"),
    "황소자리": ("흙", "고정", "자산·감각·지속성"), "처녀자리": ("흙", "변화", "분석·정리·개선"), "염소자리": ("흙", "시작", "책임·성과·장기전"),
    "쌍둥이자리": ("공기", "변화", "정보·소통·전환"), "천칭자리": ("공기", "시작", "관계·균형·협상"), "물병자리": ("공기", "고정", "독립성·네트워크·기획"),
    "게자리": ("물", "시작", "보호·가족·정서"), "전갈자리": ("물", "고정", "집중·통제·심층 변화"), "물고기자리": ("물", "변화", "직관·공감·경계 흐림"),
}
TAROT = [
    ("The Fool", "새 출발·모험·가벼움", "무계획·도피·위험 과소평가", "지금 무작정 뛰어드는 영역은 무엇인가?", "start"),
    ("The Magician", "자원 활용·기획·실행력", "말뿐인 계획·분산·기술 부족", "내 손에 이미 있는 도구는 무엇인가?", "start"),
    ("The High Priestess", "직관·숨은 정보·침묵", "회피·비밀·판단 보류 과잉", "말하지 않은 정보가 판단을 바꾸는가?", "hidden"),
    ("The Empress", "성장·풍요·관계 자원", "과잉 의존·낭비·감정소비", "키우되 방치한 것은 무엇인가?", "growth"),
    ("The Emperor", "구조·책임·통제", "경직·권위 충돌·과잉 통제", "규칙이 필요한 곳은 어디인가?", "control"),
    ("The Hierophant", "제도·조언·전통적 해법", "낡은 기준·체면·형식주의", "전문가나 제도를 써야 할 문제인가?", "control"),
    ("The Lovers", "선택·관계·가치 정렬", "우유부단·유혹·가치 충돌", "무엇을 선택하면 무엇을 잃는가?", "choice"),
    ("The Chariot", "추진력·승부·방향성", "과속·통제 상실·충돌", "속도보다 방향이 맞는가?", "success"),
    ("Strength", "인내·자기조절·내적 힘", "억누른 분노·자기확신 부족", "힘으로 밀지 말고 조절할 것은?", "success"),
    ("The Hermit", "고립된 탐구·검증·거리두기", "고립 과잉·기회 회피", "혼자 확인해야 할 사실은 무엇인가?", "hidden"),
    ("Wheel of Fortune", "전환점·순환·타이밍", "반복되는 패턴·타이밍 불안", "반복되는 운의 패턴은 무엇인가?", "change"),
    ("Justice", "계약·균형·책임", "불공정·책임 회피·판단 오류", "숫자와 증거로 확인했는가?", "control"),
    ("The Hanged Man", "정지·관점 전환·희생", "무기력·지연 합리화", "멈춰야 보이는 것은 무엇인가?", "pause"),
    ("Death", "종료·전환·재편", "끝내지 못함·미련·정체", "끝내야 다음이 열리는 것은?", "change"),
    ("Temperance", "조율·분할·균형", "흐름 깨짐·극단·조절 실패", "섞고 나눠야 안정되는 것은?", "balance"),
    ("The Devil", "집착·중독·계약의 덫", "속박 자각·유혹에서 거리두기", "끊지 못하는 패턴은 무엇인가?", "shadow"),
    ("The Tower", "붕괴·해체·진실 폭로", "붕괴 회피·내부 변화 지연", "억지로 유지하는 것은 무엇인가?", "change"),
    ("The Star", "회복·희망·장기 비전", "기대 과잉·현실성 부족", "회복에 시간이 필요한 영역은?", "growth"),
    ("The Moon", "불안·착각·잠재의식", "혼란에서 깨어남·진실 확인", "불안과 사실을 구분했는가?", "shadow"),
    ("The Sun", "성과·명료함·자신감", "과신·노출 부담·성과 지연", "밝혀야 힘이 생기는 것은?", "success"),
    ("Judgement", "평가·부활·결정", "과거 회피·판단 지연", "이제 결론내야 할 일은?", "choice"),
    ("The World", "완성·확장·마무리", "미완성·마무리 실패", "완료해야 다음 단계가 열리는 것은?", "success"),
]


def _gz(index: int) -> str:
    return STEMS[index % 10] + BRANCHES[index % 12]


def _year_index(y: int, m: int, d: int) -> int:
    yy = y - 1 if (m, d) < (2, 4) else y
    return (yy - 1900 + 36) % 60


def _month_offset(m: int, d: int) -> int:
    md = m * 100 + d
    if md >= 1207 or md < 106: return 10
    if md < 204: return 11
    if md < 306: return 0
    if md < 405: return 1
    if md < 506: return 2
    if md < 606: return 3
    if md < 707: return 4
    if md < 808: return 5
    if md < 908: return 6
    if md < 1008: return 7
    if md < 1107: return 8
    return 9


def _day_index(y: int, m: int, d: int) -> int:
    return (date(y, m, d) - date(1900, 1, 1)).days + 10


def _hour_branch(hour: int) -> int:
    return 0 if hour == 23 else ((hour + 1) // 2) % 12


def chart_from_ymdh(y: int, m: int, d: int, hour: int = 12) -> dict[str, str]:
    year_idx = _year_index(y, m, d)
    year_stem = year_idx % 10
    moff = _month_offset(m, d)
    month_start = {0:2, 5:2, 1:4, 6:4, 2:6, 7:6, 3:8, 8:8, 4:0, 9:0}[year_stem]
    month_stem = (month_start + moff) % 10
    day_idx = _day_index(y, m, d)
    day_stem = day_idx % 10
    hb = _hour_branch(hour)
    hour_start = {0:0, 5:0, 1:2, 6:2, 2:4, 7:4, 3:6, 8:6, 4:8, 9:8}[day_stem]
    return {"year": _gz(year_idx), "month": STEMS[month_stem] + MONTH_BRANCHES[moff], "day": _gz(day_idx), "hour": STEMS[(hour_start + hb) % 10] + HOUR_BRANCHES[hb]}


def profile_parts(profile) -> tuple[int, int, int, int]:
    y, m, d = [int(x) for x in profile.birth_date.split("-")]
    hour = int(profile.birth_time.split(":")[0]) if getattr(profile, "birth_time", "") else 12
    return y, m, d, hour


def balance(chart: dict[str, str]) -> dict[str, int]:
    out = {"목": 0, "화": 0, "토": 0, "금": 0, "수": 0}
    for pillar in chart.values():
        for ch in pillar:
            e = ELEMENT.get(ch)
            if e:
                out[e] += 1
    return out


def _strength(day_el: str, bal: dict[str, int]) -> str:
    return "신강" if bal.get(day_el, 0) >= 3 else "신약"


def _useful(day_el: str, strength: str) -> tuple[str, str]:
    return (CONTROLS[day_el], GENERATES[day_el]) if strength == "신강" else (GENERATES[day_el], CONTROLS[day_el])


def _annual(day_stem: str) -> str:
    if day_stem in "甲乙": return "재성 압박이 커져 기회와 지출이 같이 온다."
    if day_stem in "丙丁": return "비겁이 강해져 경쟁·독립심·자기주장이 커진다."
    if day_stem in "戊己": return "관성 자극으로 직장·규칙·공식관계 변화가 생긴다."
    if day_stem in "庚辛": return "인성 운이 들어와 학습·계획·준비가 성과의 전제다."
    return "식상 운이 강해져 표현·창업·새 시도가 활발해진다."


def _context(question: str, annual: str, useful: str, avoid: str) -> str:
    q = question or ""
    if any(w in q for w in ["돈", "재물", "투자", "주식", "매매"]):
        return f"재물 초점: {annual} 다만 {avoid} 과잉이면 충동 지출·물타기 위험이 커진다.\n실행: 새 진입 전 손절가와 최대금액을 먼저 적는다."
    if any(w in q for w in ["연애", "관계", "결혼", "상대"]):
        return f"관계 초점: 세운 작용이 감정표현과 책임 분담을 흔든다. {useful} 방식으로 말보다 반복 행동을 본다.\n실행: 이번 주 확인할 행동 기준 1개만 정한다."
    if any(w in q for w in ["직장", "일", "시험", "공부", "이직", "업무"]):
        return f"직장 초점: {annual} 평판보다 산출물과 절차가 중요하다. {avoid} 과잉 판단은 줄인다.\n실행: 오늘 완료할 문서·성과물 1개를 먼저 끝낸다."
    if any(w in q for w in ["오늘", "오늘의", "일진", "운세"]):
        return f"오늘 초점: 큰 결정보다 {useful} 보완 행동이 유리하다. {avoid} 쪽으로 과열되면 말·소비·감정반응이 커진다.\n실행: 약속·매매·대화는 10분 지연 후 결정한다."
    return f"종합 초점: {annual} 원국의 약한 {useful}을 보완하면 안정된다.\n실행: 오늘 할 일 1개와 버릴 일 1개를 동시에 정한다."


def reading(name: str, profile, question: str = "") -> str:
    y, m, d, hour = profile_parts(profile)
    c = chart_from_ymdh(y, m, d, hour)
    bal = balance(c)
    dm = c["day"][0]
    day_el = ELEMENT[dm]
    strength = _strength(day_el, bal)
    useful, avoid = _useful(day_el, strength)
    today = ""
    if any(w in question for w in ["오늘", "오늘의", "일진", "운세"]):
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        tc = chart_from_ymdh(now.year, now.month, now.day, now.hour)
        today = f"오늘 만세력: {tc['year']}년 {tc['month']}월 {tc['day']}일 {tc['hour']}시\n"
    btxt = " ".join(f"{k}{v}" for k, v in bal.items())
    annual = _annual(dm)
    body = _context(question, annual, useful, avoid)
    return (f"사주 리딩 [{c['day']}]\n━━━━━━━━\n사주팔자: {c['year']} {c['month']} {c['day']} {c['hour']}\n{today}일간: {dm} — {DAY_STEM_TRAITS[dm]}\n오행: {btxt} | {strength}\n용신: {useful} / 기신: {avoid}\n2026 병오년: {annual}\n─────\n{body}")[:760]


def zodiac(month: int, day: int) -> str:
    md = month * 100 + day
    current = "염소자리"
    for cutoff, sign in ZODIAC:
        if md < cutoff:
            return current
        current = sign
    return "염소자리"


def astrology(name: str, profile, question: str = "") -> str:
    y, m, d, hour = profile_parts(profile)
    sign = zodiac(m, d)
    element, mode, theme = SIGN_META[sign]
    day_name = DAY_NAMES[(date(y, m, d).weekday() + 1) % 7]
    q = question or "종합"
    if any(w in q for w in ["돈", "투자", "주식", "재물"]):
        action = f"재물 실행: {theme} 성향을 수익보다 리스크 한도 설정에 쓴다. 즉흥 진입은 줄인다."
    elif any(w in q for w in ["연애", "결혼", "관계"]):
        action = f"관계 실행: {mode} 성향이 반복되는 반응을 만든다. 상대의 말보다 약속 이행을 본다."
    elif any(w in q for w in ["직장", "일", "공부", "시험"]):
        action = f"일 실행: {theme}를 성과물 1개로 좁힌다. 평가받을 형태로 제출해야 흐름이 열린다."
    else:
        action = f"실행 조언: {element}/{mode} 성향을 과하게 쓰지 말고, 오늘 선택 기준 1개만 고정한다."
    return f"{name} 점성술\n━━━━━━━━\n태양궁: {sign} | 원소 {element} | 양식 {mode}\n출생요일: {day_name}요일 | 시간대: {hour:02d}시권\n핵심 성향: {theme}\n질문 초점: {q}\n─────\n{action}"


def _tarot_context(question: str) -> str:
    if any(w in question for w in ["돈", "투자", "매매", "주식"]): return "재물 흐름"
    if any(w in question for w in ["연애", "관계", "결혼"]): return "감정·신뢰·연결"
    if any(w in question for w in ["직장", "이직", "일", "시험"]): return "역할·책임·평가"
    return "전반적 흐름"


def tarot(user_id: str, question: str = "") -> str:
    seed = f"{user_id}:{datetime.now(ZoneInfo('Asia/Seoul')).date()}:{question}"
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    cards = rng.sample(TAROT, 3)
    dirs = [rng.choice([True, False]) for _ in cards]
    def line(card, upright):
        return card[1] if upright else card[2]
    past_cat = cards[0][4]
    if past_cat == "success": flow = "과거의 성공 패턴이 현재 정체를 만들 수 있다."
    elif past_cat in {"change", "shadow"}: flow = "정리되지 않은 이전 충격이나 불안이 현재 판단에 남아 있다."
    elif past_cat == "start": flow = "새 출발 에너지가 있었지만 아직 방향을 고정하지 못했다."
    else: flow = "이전 선택의 기준이 현재 문제를 해석하는 틀이 되고 있다."
    ctx = _tarot_context(question)
    return (f"타로 3카드 리딩\n━━━━━━━━\n과거 에너지: {cards[0][0]} ({'정' if dirs[0] else '역'})\n→ {line(cards[0], dirs[0])}\n현재 막힌 지점: {cards[1][0]} ({'정' if dirs[1] else '역'})\n→ {line(cards[1], dirs[1])}\n실행 조언: {cards[2][0]} ({'정' if dirs[2] else '역'})\n→ {line(cards[2], dirs[2])}\n─────\n흐름 해석: {flow} 현재 막힘을 정리해야 미래 카드의 조언이 실행된다.\n질문 초점: {ctx} 관점으로 읽어야 한다.\n핵심 질문: {cards[2][3]}\n주의: 타로는 판단 보조 도구입니다. 투자·법률·건강은 사실 확인 우선.")[:800]
