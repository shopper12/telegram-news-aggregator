from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass

BANKS: dict[str, list[tuple[str, str]]] = {
    "ko": [
        ("학교", "학생들이 수업을 받는 곳"), ("학생", "학교에서 배우는 사람"), ("학원", "학교 밖에서 배우는 교육 시설"), ("학기", "학교의 수업 기간 단위"),
        ("교실", "수업을 하는 방"), ("교사", "학생을 가르치는 사람"), ("과학", "자연 현상을 연구하는 학문"), ("과일", "나무나 풀에서 열리는 먹을거리"),
        ("사과", "빨갛거나 초록색인 대표적인 과일"), ("과자", "간식으로 먹는 바삭한 음식"), ("실내", "건물의 안쪽"), ("내일", "오늘 다음 날"),
        ("일기", "하루의 일을 기록한 글"), ("기차", "철길 위를 달리는 교통수단"), ("전기", "전하의 이동으로 생기는 에너지"), ("기분", "마음에 느껴지는 상태"),
        ("분수", "전체를 몇 부분으로 나눈 수"), ("수학", "수와 도형의 관계를 다루는 학문"), ("수업", "배우고 가르치는 활동"), ("업무", "직장에서 맡아 하는 일"),
        ("시장", "물건을 사고파는 곳"), ("장터", "여러 상인이 모여 물건을 파는 곳"), ("장미", "가시가 있고 향기가 나는 꽃"), ("미술", "그림과 조형을 다루는 예술"),
        ("술잔", "술을 따라 마시는 작은 잔"), ("잔디", "운동장이나 정원에 까는 풀"), ("바다", "육지보다 넓은 소금물의 공간"), ("다리", "강이나 길을 건너도록 만든 시설"),
        ("리본", "선물 포장에 자주 쓰는 장식 끈"), ("본문", "문서에서 제목 등을 제외한 중심 글"), ("문화", "한 사회가 공유하는 생활 방식과 가치"), ("화분", "식물을 심어 기르는 그릇"),
        ("분필", "칠판에 글씨를 쓰는 도구"), ("필통", "연필과 펜을 넣는 물건"), ("통장", "은행 거래 내역을 기록하는 장부"), ("장갑", "손을 보호하거나 따뜻하게 하는 물건"),
        ("옷장", "옷을 넣어 보관하는 가구"), ("자동차", "도로를 달리는 대표적인 교통수단"), ("차표", "기차나 버스 등을 탈 때 쓰는 표"), ("표정", "얼굴에 드러나는 감정의 모습"),
        ("정답", "문제에 맞는 올바른 답"), ("답장", "받은 편지나 메시지에 보내는 회신"), ("약속", "미래의 일을 서로 정해 지키기로 함"), ("속도", "물체가 움직이는 빠르기"),
        ("도시", "인구와 시설이 밀집한 지역"), ("시간", "사건의 흐름을 나타내는 기준"), ("간식", "끼니 사이에 가볍게 먹는 음식"), ("식물", "뿌리와 잎을 가지며 자라는 생물"),
        ("물감", "그림을 그릴 때 색을 내는 재료"), ("감자", "땅속줄기를 먹는 대표적인 작물"), ("자전거", "두 바퀴를 페달로 굴리는 탈것"), ("거울", "빛을 반사해 모습을 비추는 물건"),
    ],
    "en": [
        ("APPLE", "사과"), ("PLANET", "행성"), ("PAPER", "종이"), ("LEARN", "배우다"), ("WATER", "물"), ("TRAIN", "기차"), ("TABLE", "탁자"), ("LIGHT", "빛"),
        ("HOUSE", "집"), ("MUSIC", "음악"), ("SMILE", "미소"), ("GREEN", "초록색"), ("BREAD", "빵"), ("RIVER", "강"), ("CLOUD", "구름"), ("STONE", "돌"),
        ("HEART", "심장 또는 마음"), ("DREAM", "꿈"), ("MONEY", "돈"), ("CHAIR", "의자"), ("BEACH", "해변"), ("PHONE", "전화기"), ("WORLD", "세계"), ("NIGHT", "밤"),
        ("HAPPY", "행복한"), ("EARTH", "지구"), ("CLOCK", "시계"), ("SUGAR", "설탕"), ("BRUSH", "솔 또는 붓"), ("GLASS", "유리 또는 잔"), ("PLANT", "식물"), ("QUEEN", "여왕"),
        ("BRAIN", "뇌"), ("STORY", "이야기"), ("SOUND", "소리"), ("FRUIT", "과일"), ("MOUSE", "쥐 또는 컴퓨터 입력장치"), ("FIELD", "들판 또는 분야"), ("BOARD", "판 또는 게시판"), ("SCHOOL", "학교"),
        ("FRIEND", "친구"), ("ORANGE", "오렌지"), ("MARKET", "시장"), ("FAMILY", "가족"), ("GARDEN", "정원"), ("WINDOW", "창문"), ("SUMMER", "여름"), ("WINTER", "겨울"),
        ("BUTTON", "단추 또는 버튼"), ("BRIDGE", "다리"), ("ENERGY", "에너지"), ("COFFEE", "커피"), ("CAMERA", "카메라"), ("LETTER", "편지 또는 글자"), ("PEOPLE", "사람들"), ("TRAVEL", "여행하다"),
    ],
}

@dataclass
class Cell:
    char: str
    dirs: set[str]


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


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


def generate_puzzle(language: str, publish_date: str, rows: int = 11, cols: int = 11, target_entries: int = 8) -> tuple[dict, dict]:
    if language not in BANKS:
        raise ValueError(f"unsupported language: {language}")
    best_grid: dict[tuple[int, int], Cell] = {}
    best_entries: list[dict] = []
    for attempt in range(96):
        grid, entries = _attempt(language, publish_date, attempt, rows, cols, target_entries)
        answers = [entry["answer"] for entry in entries]
        leaks = any(answer in other["clue"] for i, answer in enumerate(answers) for j, other in enumerate(entries) if i != j)
        if leaks:
            continue
        if len(entries) > len(best_entries):
            best_grid, best_entries = grid, entries
        if len(best_entries) >= target_entries:
            break
    if len(best_entries) < 5:
        raise RuntimeError(f"could only place {len(best_entries)} entries for {language} {publish_date}")
    starts = sorted({(e["row"], e["col"]) for e in best_entries})
    number_by_start = {coord: i + 1 for i, coord in enumerate(starts)}
    public_entries, answers = [], {}
    for i, entry in enumerate(best_entries):
        clue_id = f"c{i+1}"
        public_entries.append({"id": clue_id, "number": number_by_start[(entry["row"], entry["col"])], "clue": entry["clue"], "direction": entry["direction"], "row": entry["row"], "col": entry["col"], "length": len(entry["answer"])})
        answers[clue_id] = entry["answer"]
    cells = [{"row": r, "col": c, "number": number_by_start.get((r, c))} for r, c in sorted(best_grid)]
    public = {"language": language, "publishDate": publish_date, "rows": rows, "cols": cols, "cells": cells, "entries": public_entries}
    return public, {"answers": answers}


def normalize_answer(value: object, language: str) -> str:
    text = unicodedata.normalize("NFKC", "".join(str(value or "").split()))
    return text.upper() if language == "en" else text
