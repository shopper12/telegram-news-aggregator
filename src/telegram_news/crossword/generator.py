from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from dataclasses import dataclass


# The daily puzzle is aimed at Korean high-school students and adults rather than
# elementary vocabulary practice. Clues are intentionally contextual/indirect:
# they should be fair after crossings, but should not simply translate or repeat
# the answer.
BANKS: dict[str, list[tuple[str, str]]] = {
    "ko": [
        ("맥락", "같은 문장도 앞뒤 상황이 달라지면 뜻이 바뀔 때 함께 살펴야 하는 조건"),
        ("역설", "겉으로는 모순되어 보이는데 오히려 그 충돌 때문에 진실이 선명해지는 표현 장치"),
        ("반어", "칭찬처럼 들리는 말이 실제로는 비판이 되는 식의 겉뜻과 속뜻의 어긋남"),
        ("함축", "짧은 표현 안에 표면보다 더 많은 의미를 눌러 담는 성질"),
        ("개연성", "소설 속 사건을 읽으며 독자가 ‘그럴 법하다’고 받아들이게 되는 정도"),
        ("귀납", "여러 구체적 사례를 모아 더 넓은 일반 판단으로 올라가는 추론 방식"),
        ("연역", "일반 원리에서 출발해 개별 사례에 적용되는 결론을 끌어내는 추론 방식"),
        ("유추", "서로 다른 두 대상의 닮은 관계를 바탕으로 새로운 판단을 끌어내는 사고"),
        ("논증", "주장을 그냥 제시하지 않고 이유와 자료를 연결해 타당성을 세우는 과정"),
        ("전제", "결론을 끌어내기 전에 이미 참이라고 받아들이고 출발하는 조건"),
        ("반례", "‘항상 그렇다’는 주장을 단 하나의 어긋난 사례로 무너뜨릴 때 쓰는 사례"),
        ("명제", "참과 거짓을 판별할 수 있도록 하나의 판단을 문장으로 나타낸 것"),
        ("인과", "앞선 사건이 뒤의 결과를 낳았다고 설명할 때 묶이는 두 사건의 관계"),
        ("관점", "같은 사건을 보더라도 무엇을 중심에 놓느냐에 따라 달라지는 바라보는 자리"),
        ("시점", "이야기 속 정보를 누구의 눈과 거리에서 전달할지 결정하는 서술의 자리"),
        ("화자", "시 속에서 실제 작가와 구별해 말하고 있다고 설정되는 목소리의 주체"),
        ("서사", "인물·사건·시간의 변화를 엮어 하나의 이야기 흐름으로 만드는 방식"),
        ("상징", "구체적인 사물 하나가 그 자체를 넘어 더 넓은 관념까지 떠올리게 하는 장치"),
        ("심상", "글을 읽을 때 실제 감각 자극 없이도 머릿속에 떠오르는 감각적 모습"),
        ("운율", "반복되는 소리와 호흡이 시를 읽을 때 만들어 내는 리듬감"),
        ("풍자", "대상을 정면으로 꾸짖기보다 웃음과 비틀기를 통해 모순을 드러내는 방식"),
        ("복선", "뒤에 일어날 사건을 독자가 나중에 되짚어 알아차리도록 미리 심어 둔 단서"),
        ("갈등", "인물의 욕망과 다른 힘이 충돌하면서 사건을 앞으로 밀어가는 긴장"),
        ("대조", "두 대상을 나란히 놓고 차이를 두드러지게 만들어 특징을 선명하게 하는 방식"),
        ("전환", "글의 방향·정서·논지가 앞부분과 다른 쪽으로 꺾이는 지점"),
        ("논리", "주장과 근거 사이가 모순 없이 이어져 결론까지 납득 가능하게 만드는 질서"),
        ("쟁점", "찬반이나 여러 입장이 맞서면서 토론에서 실제로 판단해야 할 핵심 문제"),
        ("요지", "세부 사례를 덜어 내고 글쓴이가 결국 전달하려는 중심 내용을 압축한 것"),
        ("주제", "작품의 여러 요소를 꿰어 독자가 끝내 생각하게 되는 중심적인 의미"),
        ("태도", "대상을 바라보는 화자나 글쓴이의 거리감·평가·감정이 드러난 자세"),
        ("다의성", "하나의 표현이 문맥에 따라 둘 이상의 의미로 읽힐 수 있는 성질"),
        ("객관성", "개인의 호오보다 확인 가능한 사실과 공통 기준에 기대려는 성질"),
        ("주관성", "판단에 개인의 경험·감정·가치가 강하게 개입하는 성질"),
        ("보편성", "특정 개인이나 시대에만 머물지 않고 넓은 대상에 두루 적용되는 성질"),
        ("특수성", "일반 규칙만으로 환원되지 않는 개별 대상만의 고유한 성질"),
        ("필연성", "다른 가능성을 거의 허용하지 않고 그렇게 될 수밖에 없다고 보는 성질"),
        ("우연성", "미리 정해진 필수 관계 없이 여러 가능성 가운데 하나가 실현되는 성질"),
        ("상대성", "판단 기준이 하나로 고정되지 않고 조건이나 관계에 따라 달라지는 성질"),
        ("정체성", "시간이 지나도 ‘나는 누구인가’를 이어 주는 자기 인식과 소속의 틀"),
        ("다원성", "하나의 기준만 강요하지 않고 서로 다른 가치와 관점의 공존을 인정하는 성질"),
        ("상호성", "한쪽만 작용하는 것이 아니라 서로 주고받는 관계 속에서 성립하는 성질"),
        ("역동성", "고정된 상태보다 충돌과 변화가 계속 일어나며 움직이는 성질"),
        ("담론", "어떤 시대와 사회가 특정 주제를 말하고 이해하도록 만드는 말과 관념의 체계"),
        ("통념", "충분히 검토하지 않아도 사회 구성원 다수가 당연하다고 받아들이는 생각"),
        ("관습", "오랫동안 반복되어 구성원들이 자연스럽게 따르게 된 사회적 행동 방식"),
        ("직관", "긴 추론 과정을 의식적으로 거치지 않고 곧바로 핵심을 포착하는 판단"),
        ("통찰", "겉으로 드러난 현상 너머의 구조나 원인을 깊게 꿰뚫어 보는 이해"),
        ("성찰", "자신의 행동과 생각을 한 걸음 떨어져 되돌아보고 의미를 따져 보는 과정"),
        ("소외", "사람이 자신이 만든 관계·노동·사회로부터 오히려 낯설어지고 멀어지는 상태"),
        ("연대", "서로 다른 사람들이 공동의 문제와 책임을 함께 나누기 위해 맺는 연결"),
        ("공감", "타인의 감정을 내 감정과 동일시하지 않으면서도 그 입장에서 이해하려는 능력"),
        ("윤리", "무엇이 옳은 행동인지 개인의 이익을 넘어 숙고하게 하는 가치의 기준"),
        ("규범", "구성원에게 어떤 행동이 바람직하거나 허용되는지를 제시하는 사회적 기준"),
        ("책임", "자신의 선택이 낳은 결과를 남에게 떠넘기지 않고 감당해야 하는 의무"),
        ("권리", "개인이나 집단이 정당하게 요구하고 누릴 수 있다고 사회가 인정하는 자격"),
        ("실증", "주장을 관념에만 두지 않고 관찰·자료·경험으로 확인하려는 접근"),
        ("가설", "아직 확정되지 않았지만 관찰 결과를 설명하기 위해 임시로 세운 설명"),
        ("검증", "주장이나 예측이 실제 자료와 맞는지 반례 가능성까지 두고 확인하는 절차"),
        ("추론", "이미 아는 정보들을 연결해 직접 주어지지 않은 새로운 판단에 이르는 사고"),
        ("관념", "구체적인 사물 하나보다 그것을 머릿속에서 일반화해 만든 생각의 내용"),
        ("매개", "서로 직접 이어지지 않은 두 대상 사이에서 관계가 형성되도록 잇는 역할"),
        ("체계", "여러 요소가 제각각 놓이지 않고 일정한 원리와 관계에 따라 조직된 전체"),
        ("구조", "겉모습보다 요소들이 서로 어떤 위치와 관계를 이루는지 보여 주는 짜임"),
        ("패러다임", "한 시대의 사람들이 문제를 보고 설명하는 방식 자체를 규정하는 큰 사고 틀"),
    ],
    "en": [
        ("AMBIGUOUS", "A sentence with two equally defensible interpretations could be described this way."),
        ("INEVITABLE", "What an outcome may seem once every realistic alternative has been ruled out."),
        ("RESILIENT", "Able to recover after repeated setbacks without simply returning unchanged."),
        ("COHERENT", "What an argument is when each part connects logically to the next."),
        ("EMPIRICAL", "Grounded in observation or experiment rather than speculation alone."),
        ("INFERENCE", "A conclusion reached from evidence that does not state it outright."),
        ("PREMISE", "A claim accepted at the start so that an argument can build from it."),
        ("PARADOX", "An apparent contradiction that can reveal a deeper truth on closer examination."),
        ("ANALOGY", "A comparison used to illuminate a relationship in a less familiar case."),
        ("CONCISE", "Brief without losing the information needed to make the point complete."),
        ("IMPLICIT", "Present in the meaning even though it is never stated word for word."),
        ("EXPLICIT", "Stated so directly that the reader is not expected to infer it."),
        ("CRITICAL", "In academic reading, involving judgment and evaluation rather than mere acceptance."),
        ("ETHICAL", "Concerned with what ought to be done, not merely with what can be done."),
        ("DIVERSE", "Containing meaningfully different kinds rather than many copies of the same kind."),
        ("PLAUSIBLE", "Believable enough to deserve consideration, even before it has been proved."),
        ("RELEVANT", "Directly connected to the question being decided rather than merely interesting."),
        ("VALIDATE", "To test whether a claim or method holds up against independent evidence."),
        ("MITIGATE", "To reduce the severity of a problem without necessarily eliminating its cause."),
        ("CONSTRAIN", "To limit the range of actions or outcomes that remain possible."),
        ("PERSIST", "To continue despite resistance, delay, or repeated failure."),
        ("ADAPTIVE", "Able to change behavior when the environment changes instead of following one fixed rule."),
        ("DYNAMIC", "Characterized by continuing interaction and change rather than a fixed state."),
        ("RATIONAL", "Based on reasons that can be examined rather than on impulse alone."),
        ("NUANCED", "Showing fine distinctions instead of forcing a complex issue into two simple sides."),
        ("EVIDENCE", "What should make a claim more credible when it can be checked independently."),
        ("CONTEXT", "The surrounding circumstances that can change how the same words should be understood."),
        ("CONFLICT", "A clash of goals or forces that often supplies the engine of a narrative."),
        ("SYMBOLIC", "Representing an idea beyond the object or action that appears on the surface."),
        ("NARRATIVE", "An organized account in which events are shaped into a meaningful sequence."),
        ("PERSPECTIVE", "The position from which an event is interpreted rather than the event itself."),
        ("PRECISE", "Exact enough to rule out interpretations the writer did not intend."),
        ("INTEGRITY", "Consistency between stated principles and conduct, especially when no reward is guaranteed."),
        ("CONSENSUS", "Broad agreement reached without requiring every participant to think identically."),
        ("VOLATILE", "Likely to change sharply and unpredictably over a short period."),
        ("SCARCE", "Limited enough that choosing one use means giving up another."),
        ("SUSTAIN", "To keep something operating or continuing over a meaningful period."),
        ("INNOVATE", "To introduce a genuinely useful new method rather than merely rename an old one."),
        ("SYNTHESIS", "A new whole created by combining ideas rather than listing them side by side."),
        ("ABSTRACT", "Concerned with general ideas rather than a particular physical example."),
        ("CONCRETE", "Specific and observable rather than expressed only as a general idea."),
        ("HYPOTHESIS", "A testable explanation proposed before the evidence is sufficient for a conclusion."),
        ("CORRELATE", "To vary in a related way without by itself proving that one thing causes the other."),
        ("CAUSAL", "Describing a relationship in which one factor actually produces a change in another."),
        ("INCLUSIVE", "Designed so that participation is not restricted to a narrow or favored group."),
        ("INTRINSIC", "Belonging to the nature of something itself rather than supplied from outside."),
        ("EXTRINSIC", "Coming from an outside reward, pressure, or condition rather than from the activity itself."),
        ("EQUITABLE", "Fair by accounting for different circumstances rather than treating every case identically."),
        ("TOLERANCE", "The capacity to accept difference or withstand variation without immediate failure."),
        ("SCRUTINY", "Close examination intended to uncover weaknesses that a quick look might miss."),
        ("SUBTLE", "Present but easy to miss because it is expressed through fine distinctions."),
        ("ROBUST", "Strong enough to remain reliable when conditions or assumptions change somewhat."),
        ("FRAGILE", "Likely to fail when exposed to even modest stress or unexpected change."),
        ("CREDIBLE", "Worthy of belief because the source and supporting evidence can withstand checking."),
        ("ASSERTIVE", "Confident and direct without necessarily becoming aggressive."),
        ("PROVISION", "A condition or arrangement included in advance for a possible need or event."),
        ("RECONCILE", "To make two apparently conflicting accounts or demands fit together consistently."),
        ("PREDICT", "To state what is likely to happen before the outcome is known."),
        ("DISTORT", "To alter the shape or meaning of information so that it no longer represents the original fairly."),
        ("EMPATHY", "Understanding another person’s feelings from their position without assuming they are your own."),
        ("DILEMMA", "A choice in which each available option carries a serious cost."),
        ("REFORM", "Change intended to improve an existing institution rather than replace it entirely."),
        ("TRANSIENT", "Lasting only for a relatively short time before passing away."),
        ("ENDURING", "Continuing to matter or survive well beyond the circumstances that first produced it."),
        ("ORIENT", "To position or direct something so that it is aligned with a chosen reference."),
        ("DELIBERATE", "Done after conscious consideration rather than by accident or impulse."),
    ],
}


@dataclass
class Cell:
    char: str
    dirs: set[str]


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def normalize_answer(value: object, language: str) -> str:
    text = "".join(str(value or "").split())
    text = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", text))
    return text.upper() if language == "en" else text


def _normalized_clue(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _clue_is_acceptable(answer: str, clue: str, language: str) -> bool:
    normalized_answer = _normalized_clue(answer)
    normalized_clue = _normalized_clue(clue)
    minimum = 18 if language == "ko" else 34
    if len(str(clue).strip()) < minimum:
        return False
    if normalized_answer and normalized_answer in normalized_clue:
        return False
    if language == "en" and re.search(r"[가-힣]", clue):
        return False
    return True


def _validate_bank(language: str) -> None:
    seen: set[str] = set()
    for answer, clue in BANKS[language]:
        normalized = normalize_answer(answer, language)
        if len(normalized) < 3 or len(normalized) > 11:
            raise ValueError(f"invalid crossword answer length: {language}:{answer}")
        if normalized in seen:
            raise ValueError(f"duplicate crossword answer: {language}:{answer}")
        if not _clue_is_acceptable(answer, clue, language):
            raise ValueError(f"direct/weak crossword clue: {language}:{answer}")
        seen.add(normalized)


for _language in BANKS:
    _validate_bank(_language)


def _can_place(grid: dict[tuple[int, int], Cell], word: str, row: int, col: int, direction: str, rows: int, cols: int) -> bool:
    dr, dc = (1, 0) if direction == "down" else (0, 1)
    end_row, end_col = row + dr * (len(word) - 1), col + dc * (len(word) - 1)
    if row < 0 or col < 0 or end_row >= rows or end_col >= cols:
        return False
    if grid.get((row - dr, col - dc)) or grid.get((end_row + dr, end_col + dc)):
        return False
    crossings = 0
    for i, char in enumerate(word):
        r, c = row + dr * i, col + dc * i
        existing = grid.get((r, c))
        if existing:
            if existing.char != char or direction in existing.dirs:
                return False
            crossings += 1
        else:
            neighbors = ((r - 1, c), (r + 1, c)) if direction == "across" else ((r, c - 1), (r, c + 1))
            if any(neighbor in grid for neighbor in neighbors):
                return False
    return crossings > 0


def _place(grid: dict[tuple[int, int], Cell], word: str, row: int, col: int, direction: str) -> None:
    dr, dc = (1, 0) if direction == "down" else (0, 1)
    for i, char in enumerate(word):
        coord = (row + dr * i, col + dc * i)
        if coord in grid:
            grid[coord].dirs.add(direction)
        else:
            grid[coord] = Cell(char=char, dirs={direction})


def _attempt(language: str, publish_date: str, attempt: int, rows: int, cols: int, target_entries: int):
    rng = random.Random(_seed(f"{publish_date}:{language}:{attempt}"))
    candidates = list(BANKS[language])
    rng.shuffle(candidates)
    grid: dict[tuple[int, int], Cell] = {}
    entries: list[dict] = []
    first_idx = max(range(len(candidates)), key=lambda i: len(candidates[i][0]))
    first_answer, first_clue = candidates.pop(first_idx)
    first_row = rows // 2
    first_col = max(0, (cols - len(first_answer)) // 2)
    _place(grid, first_answer, first_row, first_col, "across")
    entries.append({"answer": first_answer, "clue": first_clue, "row": first_row, "col": first_col, "direction": "across"})
    progressed = True
    while progressed and len(entries) < target_entries:
        progressed = False
        idx = 0
        while idx < len(candidates) and len(entries) < target_entries:
            answer, clue = candidates[idx]
            placements: list[tuple[int, int, str]] = []
            for i, char in enumerate(answer):
                for (r, c), cell in list(grid.items()):
                    if cell.char != char:
                        continue
                    directions: list[str] = []
                    if "across" in cell.dirs and "down" not in cell.dirs:
                        directions.append("down")
                    if "down" in cell.dirs and "across" not in cell.dirs:
                        directions.append("across")
                    for direction in directions:
                        start_row = r - i if direction == "down" else r
                        start_col = c - i if direction == "across" else c
                        if _can_place(grid, answer, start_row, start_col, direction, rows, cols):
                            placements.append((start_row, start_col, direction))
            if not placements:
                idx += 1
                continue
            start_row, start_col, direction = rng.choice(placements)
            _place(grid, answer, start_row, start_col, direction)
            entries.append({"answer": answer, "clue": clue, "row": start_row, "col": start_col, "direction": direction})
            candidates.pop(idx)
            progressed = True
    return grid, entries


def _quality_score(grid: dict[tuple[int, int], Cell], entries: list[dict]) -> tuple[int, int]:
    crossing_cells = sum(1 for cell in grid.values() if len(cell.dirs) > 1)
    return len(entries), crossing_cells


def generate_puzzle(language: str, publish_date: str, rows: int = 13, cols: int = 13, target_entries: int = 9) -> tuple[dict, dict]:
    if language not in BANKS:
        raise ValueError(f"unsupported language: {language}")
    best_grid: dict[tuple[int, int], Cell] = {}
    best_entries: list[dict] = []
    best_quality = (0, 0)
    for attempt in range(160):
        grid, entries = _attempt(language, publish_date, attempt, rows, cols, target_entries)
        own_clues_ok = all(_clue_is_acceptable(entry["answer"], entry["clue"], language) for entry in entries)
        if not own_clues_ok:
            continue
        answers = [normalize_answer(entry["answer"], language) for entry in entries]
        leaks = any(
            _normalized_clue(answer) in _normalized_clue(other["clue"])
            for i, answer in enumerate(answers)
            for j, other in enumerate(entries)
            if i != j
        )
        if leaks:
            continue
        quality = _quality_score(grid, entries)
        if quality > best_quality:
            best_grid, best_entries, best_quality = grid, entries, quality
        if len(best_entries) >= target_entries and best_quality[1] >= max(3, target_entries // 2):
            break
    if len(best_entries) < 5:
        raise RuntimeError(f"could only place {len(best_entries)} entries for {language} {publish_date}")
    starts = sorted({(e["row"], e["col"]) for e in best_entries})
    number_by_start = {coord: i + 1 for i, coord in enumerate(starts)}
    public_entries, answers = [], {}
    for i, entry in enumerate(best_entries):
        clue_id = f"c{i + 1}"
        public_entries.append(
            {
                "id": clue_id,
                "number": number_by_start[(entry["row"], entry["col"])],
                "clue": entry["clue"],
                "direction": entry["direction"],
                "row": entry["row"],
                "col": entry["col"],
                "length": len(entry["answer"]),
            }
        )
        answers[clue_id] = entry["answer"]
    cells = [{"row": r, "col": c, "number": number_by_start.get((r, c))} for r, c in sorted(best_grid)]
    public = {
        "language": language,
        "publishDate": publish_date,
        "difficulty": "high-school-plus",
        "clueStyle": "contextual-indirect",
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "entries": public_entries,
    }
    return public, {"answers": answers}
