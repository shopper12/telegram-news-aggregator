from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
MONTH_BRANCHES = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
HOUR_BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
ELEMENT = {"甲":"목","乙":"목","寅":"목","卯":"목","丙":"화","丁":"화","巳":"화","午":"화","戊":"토","己":"토","辰":"토","戌":"토","丑":"토","未":"토","庚":"금","辛":"금","申":"금","酉":"금","壬":"수","癸":"수","亥":"수","子":"수"}
DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]
ZODIAC = [(120,"염소자리"),(219,"물병자리"),(321,"물고기자리"),(420,"양자리"),(521,"황소자리"),(621,"쌍둥이자리"),(723,"게자리"),(823,"사자자리"),(923,"처녀자리"),(1023,"천칭자리"),(1122,"전갈자리"),(1222,"사수자리"),(1232,"염소자리")]


def days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def solar_year(y: int, m: int, d: int) -> int:
    return y - 1 if (m, d) < (2, 4) else y


def solar_month_offset(m: int, d: int) -> int:
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


def chart_from_ymdh(y: int, m: int, d: int, hour: int = 12) -> dict[str, str]:
    sy = solar_year(y, m, d)
    year_idx = (sy - 4) % 60
    year_stem_idx = year_idx % 10
    moff = solar_month_offset(m, d)
    first_month_stem = ((year_stem_idx % 5) * 2 + 2) % 10
    month_stem_idx = (first_month_stem + moff) % 10
    day_idx = (days_from_civil(y, m, d) + 17) % 60
    day_stem_idx = day_idx % 10
    hour_branch_idx = 0 if hour == 23 else ((hour + 1) // 2) % 12
    hour_stem_idx = ((day_stem_idx % 5) * 2 + hour_branch_idx) % 10
    return {"year": STEMS[year_idx % 10] + BRANCHES[year_idx % 12], "month": STEMS[month_stem_idx] + MONTH_BRANCHES[moff], "day": STEMS[day_idx % 10] + BRANCHES[day_idx % 12], "hour": STEMS[hour_stem_idx] + HOUR_BRANCHES[hour_branch_idx]}


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


def reading(name: str, profile, question: str = "") -> str:
    y, m, d, hour = profile_parts(profile)
    c = chart_from_ymdh(y, m, d, hour)
    b = balance(c)
    strong = max(b, key=b.get)
    weak = min(b, key=b.get)
    today_line = ""
    if any(w in question for w in ["오늘", "오늘의", "일진", "운세"]):
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        tc = chart_from_ymdh(now.year, now.month, now.day, now.hour)
        today_line = f"오늘 만세력: {now:%Y-%m-%d} {tc['year']}년 {tc['month']}월 {tc['day']}일 {tc['hour']}시\n"
    btxt = " ".join(f"{k}{v}" for k, v in b.items())
    dm = c["day"][0]
    focus = question or "종합"
    if any(w in focus for w in ["돈", "재물", "주식", "매매"]):
        action = f"실행 조언: {strong} 과잉 결정을 줄이고, 금액·시간·철회 기준을 먼저 정한다."
    elif any(w in focus for w in ["연애", "관계", "결혼"]):
        action = f"실행 조언: 감정 확인보다 반복 행동을 보고, {weak} 보완처럼 생활 리듬과 책임 분담을 확인한다."
    elif any(w in focus for w in ["일", "직장", "공부", "시험", "업무"]):
        action = f"실행 조언: 산출물 1개를 먼저 만들고, 막힌 일은 30분 단위로 쪼갠다."
    else:
        action = f"실행 조언: 부족한 {weak}를 보완하는 행동 하나만 선택하고, 할 일보다 버릴 일을 먼저 정한다."
    return f"{name} 사주\n입력: {profile.birth_date} {profile.birth_time or '시간미상'} {profile.gender or ''} {'음력' if profile.calendar == 'lunar' else '양력'}\n명식: {c['year']}년 {c['month']}월 {c['day']}일 {c['hour']}시\n{today_line}일간: {dm}({ELEMENT.get(dm, '')})\n오행분포: {btxt}\n구조판단: {strong} 기운이 가장 강하고 {weak} 기운이 약함. 질문 초점: {focus}\n{action}"


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
    day_name = DAY_NAMES[(days_from_civil(y, m, d) + 4) % 7]
    return f"{name} 점성술\n입력: {profile.birth_date} {profile.birth_time or '시간미상'} {profile.gender or ''}\n태양궁: {sign}\n출생요일: {day_name}요일\n시간대 힌트: {hour:02d}시 출생\n질문 초점: {question or '종합'}\n실행 조언: 별자리 성향을 기준으로 과한 반응을 줄이고, 오늘의 선택 기준 1개만 고정."
