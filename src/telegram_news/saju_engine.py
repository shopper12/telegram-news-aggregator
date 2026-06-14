from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
MONTH_BRANCHES = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
HOUR_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
ELEMENT = {"甲":"목","乙":"목","寅":"목","卯":"목","丙":"화","丁":"화","巳":"화","午":"화","戊":"토","己":"토","辰":"토","戌":"토","丑":"토","未":"토","庚":"금","辛":"금","申":"금","酉":"금","壬":"수","癸":"수","亥":"수","子":"수"}
DAY_STEM_TRAITS = {"甲":"직진형 리더", "乙":"유연한 전략가", "丙":"에너지 발산형", "丁":"섬세한 집중형", "戊":"안정 추구형", "己":"실용적 관리형", "庚":"원칙주의자", "辛":"예민한 완벽주의", "壬":"흐름 타는 전략가", "癸":"깊이 있는 사색형"}
CONTROLS = {"목":"금", "화":"수", "토":"목", "금":"화", "수":"토"}
GENERATES = {"목":"화", "화":"토", "토":"금", "금":"수", "수":"목"}
CONTROLLED_BY = {"금":"목", "수":"화", "목":"토", "화":"금", "토":"수"}
GENERATED_BY = {"화":"목", "토":"화", "금":"토", "수":"금", "목":"수"}
DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]

# 십신(十神) 매핑: (일간 오행, 상대 오행) -> 십신명
# 십신은 음양 포함 정확 계산이 필요하지만 오행 수준으로 단순화
SHISHEN_MAP: dict[tuple[str, str], str] = {
    ("목","목"):"비겁", ("목","화"):"식상", ("목","토"):"재성", ("목","금"):"관성", ("목","수"):"인성",
    ("화","화"):"비겁", ("화","토"):"식상", ("화","금"):"재성", ("화","수"):"관성", ("화","목"):"인성",
    ("토","토"):"비겁", ("토","금"):"식상", ("토","수"):"재성", ("토","목"):"관성", ("토","화"):"인성",
    ("금","금"):"비겁", ("금","수"):"식상", ("금","목"):"재성", ("금","화"):"관성", ("금","토"):"인성",
    ("수","수"):"비겁", ("수","목"):"식상", ("수","화"):"재성", ("수","토"):"관성", ("수","금"):"인성",
}

SHISHEN_MEANING = {
    "비겁": "자아·경쟁·독립심",
    "식상": "표현·창의·실행력",
    "재성": "재물·현실감각·여성(남명)",
    "관성": "명예·규율·직업운",
    "인성": "학습·후원·직관",
}

ZODIAC = [(120,"염소자리"),(219,"물병자리"),(321,"물고기자리"),(420,"양자리"),(521,"황소자리"),(621,"쌍둥이자리"),(723,"게자리"),(823,"사자자리"),(923,"처녀자리"),(1023,"천칭자리"),(1122,"전갈자리"),(1222,"사수자리"),(1232,"염소자리")]
SIGN_META = {
    "양자리": ("불", "시작", "빠른 결단과 선점"),
    "사자자리": ("불", "고정", "존재감과 주도권"),
    "사수자리": ("불", "변화", "확장과 이동"),
    "황소자리": ("흙", "고정", "자산·감각·지속성"),
    "처녀자리": ("흙", "변화", "분석·정리·개선"),
    "염소자리": ("흙", "시작", "책임·성과·장기전"),
    "쌍둥이자리": ("공기", "변화", "정보·소통·전환"),
    "천칭자리": ("공기", "시작", "관계·균형·협상"),
    "물병자리": ("공기", "고정", "독립성·네트워크·기획"),
    "게자리": ("물", "시작", "보호·가족·정서"),
    "전갈자리": ("물", "고정", "집중·통제·심층 변화"),
    "물고기자리": ("물", "변화", "직관·공감·경계 흐림"),
}

SIGN_DEFAULT_ADVICE = {
    "양자리": "결단은 빠르지만 지속이 약하다. 오늘은 시작보다 어제 시작한 것을 완결하는 데 에너지를 쓴다.",
    "황소자리": "감각과 자산 감각이 발달했지만 변화를 늦게 받아들인다. 오늘은 고집이 판단을 막는 지점 1개를 확인한다.",
    "쌍둥이자리": "정보 수집은 빠르지만 결론을 미루는 경향이 있다. 오늘은 수집한 정보로 선택 1개를 확정한다.",
    "게자리": "보호 본능이 강해 감정이 판단에 섞인다. 오늘은 감정 반응과 사실 판단을 분리해서 쓴다.",
    "사자자리": "주도권을 쥐고 싶은 욕구가 강하다. 오늘은 나서는 것보다 다른 사람의 에너지를 활용하는 방법을 찾는다.",
    "처녀자리": "분석과 개선 욕구가 강해 완벽주의로 흐를 수 있다. 오늘은 80점짜리 결과물을 제출하는 것이 100점 기다리는 것보다 낫다.",
    "천칭자리": "균형과 관계를 중시하다 결정을 미룬다. 오늘은 모두를 만족시키는 대신 우선순위 1개를 먼저 정한다.",
    "전갈자리": "집중력과 통제 욕구가 강하다. 오늘은 통제할 수 없는 변수에 에너지를 낭비하지 않는다.",
    "사수자리": "확장과 이동 욕구가 강해 마무리가 약하다. 오늘은 새 출발 전에 열린 루프 1개를 닫는다.",
    "염소자리": "장기 목표에 집중하느라 현재를 소홀히 한다. 오늘 결과물이 장기 목표와 연결되는 고리를 명확히 한다.",
    "물병자리": "독립성과 네트워크 기획이 강점이다. 오늘은 혼자 구상하는 것을 한 명에게 말로 설명해본다.",
    "물고기자리": "직관과 공감이 강하지만 경계가 흐려질 수 있다. 오늘은 내 에너지를 어디에 쓸지 시작 전에 적어둔다.",
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


def _solar_from_lunar(y: int, m: int, d: int) -> tuple[int, int, int]:
    try:
        from korean_lunar_calendar import KoreanLunarCalendar
        cal = KoreanLunarCalendar()
        cal.setLunarDate(y, m, d, False)
        solar = cal.getSolarDate()
        return int(solar.year), int(solar.month), int(solar.day)
    except Exception:
        return y, m, d


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
    return {
        "year": _gz(year_idx),
        "month": STEMS[month_stem] + MONTH_BRANCHES[moff],
        "day": _gz(day_idx),
        "hour": STEMS[(hour_start + hb) % 10] + HOUR_BRANCHES[hb],
    }


def profile_parts(profile) -> tuple[int, int, int, int]:
    y, m, d = [int(x) for x in profile.birth_date.split("-")]
    if getattr(profile, "calendar", "solar") == "lunar":
        y, m, d = _solar_from_lunar(y, m, d)
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
    support = bal.get(day_el, 0) + bal.get(GENERATED_BY.get(day_el, ""), 0)
    return "신강" if support >= 3 else "신약"


def _useful(day_el: str, strength: str) -> tuple[str, str]:
    if strength == "신강":
        return CONTROLS[day_el], GENERATED_BY.get(day_el, day_el)
    return GENERATED_BY.get(day_el, day_el), CONTROLS[day_el]


def _shishen_profile(chart: dict[str, str], day_el: str) -> dict[str, int]:
    """각 기둥의 십신 분포를 집계한다."""
    counter: dict[str, int] = {}
    for pillar in chart.values():
        for ch in pillar:
            el = ELEMENT.get(ch)
            if el:
                ss = SHISHEN_MAP.get((day_el, el), "기타")
                counter[ss] = counter.get(ss, 0) + 1
    return counter


def _dominant_shishen(ss_count: dict[str, int]) -> list[str]:
    sorted_ss = sorted(ss_count.items(), key=lambda x: -x[1])
    return [ss for ss, cnt in sorted_ss if cnt >= 2]


def _annual(day_stem: str) -> str:
    if day_stem in "甲乙":
        return "2026 병오년: 식상 운 활성화. 표현력·창업·새 시도에 에너지 쏠림. 재성 부담이 동반되어 수입과 지출이 함께 늘어난다."
    if day_stem in "丙丁":
        return "2026 병오년: 비겁 운 강화. 경쟁·독립심·자기주장이 커진다. 협업보다 단독 판단이 잦아지므로 주요 결정에 외부 검증을 더해야 한다."
    if day_stem in "戊己":
        return "2026 병오년: 인성 운 진입. 학습·계획·준비가 성과의 전제가 된다. 실행보다 설계에 투자하는 해다."
    if day_stem in "庚辛":
        return "2026 병오년: 관성 압박 강화. 직장·직업·공식관계에서 변화 요구가 커진다. 규칙 준수와 책임 이행이 평판을 결정한다."
    return "2026 병오년: 재성 운 활성화. 재물 기회와 지출이 함께 온다. 수입 증가와 충동 지출이 공존하므로 현금 흐름 관리가 핵심이다."


def _clash_analysis(chart: dict[str, str]) -> str:
    """천간충·지지충 간단 감지."""
    clashes: list[str] = []
    stems_in_chart = [p[0] for p in chart.values() if p]
    branches_in_chart = [p[1] for p in chart.values() if len(p) > 1]
    # 천간충: 甲庚, 乙辛, 丙壬, 丁癸. 戊壬·己癸는 충으로 보지 않는다.
    STEM_CLASH = [("甲", "庚"), ("乙", "辛"), ("丙", "壬"), ("丁", "癸")]
    BRANCH_CLASH = [("子","午"),("丑","未"),("寅","申"),("卯","酉"),("辰","戌"),("巳","亥")]
    for a, b in STEM_CLASH:
        if a in stems_in_chart and b in stems_in_chart:
            clashes.append(f"천간충({a}-{b})")
    for a, b in BRANCH_CLASH:
        if a in branches_in_chart and b in branches_in_chart:
            clashes.append(f"지지충({a}-{b})")
    if not clashes:
        return ""
    return f"충: {' / '.join(clashes)} — 해당 오행 변동성 증가."


def _context(question: str, annual: str, useful: str, avoid: str, ss_dominant: list[str], clash: str) -> str:
    q = question or ""
    clash_note = f" {clash}" if clash else ""
    dominant_note = ""
    if ss_dominant:
        meanings = "/".join(SHISHEN_MEANING.get(ss, ss) for ss in ss_dominant[:2])
        dominant_note = f" 원국 지배 십신({'/'.join(ss_dominant[:2])})은 {meanings}에 에너지가 집중된 구조다."

    if any(w in q for w in ["돈", "재물", "투자", "주식", "매매"]):
        return (
            f"재물 초점: {annual}{dominant_note}\n"
            f"{avoid} 과잉이면 충동 진입·물타기 위험이 커진다.{clash_note}\n"
            f"실행: 새 진입 전 최대손실금액과 포지션 크기를 먼저 확정하고, {useful} 기운을 살리는 방향(분할·계획·검증)으로 접근한다."
        )
    if any(w in q for w in ["연애", "관계", "결혼", "상대"]):
        return (
            f"관계 초점: 세운의 {avoid} 기운이 감정 반응과 책임 분담 패턴을 흔든다.{dominant_note}\n"
            f"{useful} 방식으로 말보다 반복 행동을 본다.{clash_note}\n"
            f"실행: 이번 주 상대에게 확인할 행동 기준 1개를 문장으로 먼저 쓴다."
        )
    if any(w in q for w in ["직장", "일", "시험", "공부", "이직", "업무"]):
        return (
            f"직장 초점: {annual}{dominant_note}\n"
            f"{avoid} 과잉 판단은 줄이고, {useful} 기운(구체적 산출물·절차)에 집중한다.{clash_note}\n"
            f"실행: 오늘 완료할 문서·성과물 1개를 먼저 끝내고 나머지를 시작한다."
        )
    if any(w in q for w in ["오늘", "오늘의", "일진", "운세"]):
        return (
            f"오늘 초점: 큰 결정보다 {useful} 보완 행동이 유리하다.{dominant_note}\n"
            f"{avoid} 쪽으로 과열되면 말·소비·감정반응이 커진다.{clash_note}\n"
            f"실행: 약속·매매·대화는 10분 지연 후 결정한다."
        )
    return (
        f"종합 초점: {annual}{dominant_note}\n"
        f"원국의 약한 {useful}을 보완하면 안정된다. 기신 {avoid}이 강한 영역에서 과도한 확신을 경계한다.{clash_note}\n"
        f"실행: 오늘 추진할 일 1개와 내려놓을 일 1개를 동시에 정한다."
    )


def zodiac(month: int, day: int) -> str:
    md = month * 100 + day
    current = "염소자리"
    for cutoff, sign in ZODIAC:
        if md < cutoff:
            return current
        current = sign
    return current


def _zodiac_line(m: int, d: int, question: str) -> str:
    sign = zodiac(m, d)
    element, mode, theme = SIGN_META.get(sign, ("", "", ""))
    advice = SIGN_DEFAULT_ADVICE.get(sign, "") if not question.strip() else ""
    line = f"서양 별자리: {sign}({element}/{mode}) — {theme}"
    if advice:
        line += f"\n별자리 보조 조언: {advice}"
    return line


def reading(name: str, profile, question: str = "") -> str:
    y, m, d, hour = profile_parts(profile)
    c = chart_from_ymdh(y, m, d, hour)
    bal = balance(c)
    dm = c["day"][0]
    day_el = ELEMENT[dm]
    strength = _strength(day_el, bal)
    useful, avoid = _useful(day_el, strength)
    ss_count = _shishen_profile(c, day_el)
    ss_dominant = _dominant_shishen(ss_count)
    clash = _clash_analysis(c)
    today_line = ""
    if any(w in question for w in ["오늘", "오늘의", "일진", "운세"]):
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        tc = chart_from_ymdh(now.year, now.month, now.day, now.hour)
        today_line = f"오늘 만세력: {tc['year']}년 {tc['month']}월 {tc['day']}일 {tc['hour']}시\n"
    btxt = " ".join(f"{k}{v}" for k, v in bal.items())
    ss_line = " ".join(f"{k}{v}" for k, v in sorted(ss_count.items(), key=lambda x: -x[1]) if v > 0)
    annual = _annual(dm)
    body = _context(question, annual, useful, avoid, ss_dominant, clash)
    clash_line = f"충: {clash}\n" if clash else ""
    calendar_line = "음력→양력 변환 반영\n" if getattr(profile, "calendar", "solar") == "lunar" else ""
    sign_line = _zodiac_line(m, d, question)
    result = (
        f"사주 리딩 [{c['day']}] — {DAY_STEM_TRAITS[dm]}\n"
        f"━━━━━━━━\n"
        f"{calendar_line}"
        f"사주팔자: {c['year']} {c['month']} {c['day']} {c['hour']}\n"
        f"{today_line}"
        f"일간: {dm}({day_el}) | 오행: {btxt} | {strength}\n"
        f"십신 분포: {ss_line}\n"
        f"용신: {useful} / 기신: {avoid}\n"
        f"{clash_line}"
        f"{sign_line}\n"
        f"{annual}\n"
        f"─────\n"
        f"{body}"
    )
    return result[:2000]
