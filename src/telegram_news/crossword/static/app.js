const state = {
  language: new URLSearchParams(location.search).get('language') === 'en' ? 'en' : 'ko',
  token: localStorage.getItem('crossword.session') || '',
  puzzle: null,
  active: null,
  hintRequestId: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const cellKey = (row, col) => `${row}-${col}`;

async function api(path, options = {}) {
  const headers = {
    'content-type': 'application/json',
    ...(state.token ? { authorization: `Bearer ${state.token}` } : {}),
  };
  const response = await fetch(path, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || 'REQUEST_FAILED');
  return data;
}

async function ensureSession() {
  const query = new URLSearchParams(location.search);
  if (query.get('s')) {
    state.token = query.get('s');
    localStorage.setItem('crossword.session', state.token);
    query.delete('s');
    history.replaceState(null, '', location.pathname + (query.toString() ? `?${query}` : ''));
  }
  if (!state.token) {
    const created = await api('/api/crossword/session/guest', { method: 'POST' });
    state.token = created.token;
    localStorage.setItem('crossword.session', state.token);
  }
}

function entryCells(entry) {
  return Array.from({ length: entry.length }, (_, index) => [
    entry.row + (entry.direction === 'down' ? index : 0),
    entry.col + (entry.direction === 'across' ? index : 0),
  ]);
}

function inputForCell(row, col) {
  return document.querySelector(`[data-cell='${cellKey(row, col)}'] input`);
}

function activeEntry() {
  return state.puzzle?.entries.find((entry) => entry.id === state.active) || null;
}

function entriesForCell(row, col) {
  if (!state.puzzle) return [];
  return state.puzzle.entries.filter((entry) =>
    entryCells(entry).some(([entryRow, entryCol]) => entryRow === row && entryCol === col),
  );
}

function selectEntry(id, { focus = true, preferredCell = null } = {}) {
  state.active = id;
  $$('.clue,.cell').forEach((element) => element.classList.remove('active'));
  const entry = activeEntry();
  if (!entry) return;
  document.querySelector(`[data-clue='${id}']`)?.classList.add('active');
  const cells = entryCells(entry);
  cells.forEach(([row, col]) =>
    document.querySelector(`[data-cell='${cellKey(row, col)}']`)?.classList.add('active'),
  );
  if (!focus) return;
  const target = preferredCell && cells.some(([r, c]) => r === preferredCell[0] && c === preferredCell[1])
    ? preferredCell
    : cells[0];
  inputForCell(target[0], target[1])?.focus();
}

function selectEntryForCell(row, col, input) {
  const candidates = entriesForCell(row, col);
  if (!candidates.length) return;
  const current = candidates.find((entry) => entry.id === state.active);
  const chosen = current || candidates[0];
  selectEntry(chosen.id, { focus: false });
  input?.focus();
}

function normalizeKoreanCell(value) {
  const normalized = String(value || '').normalize('NFKC').normalize('NFC');
  const syllables = [...normalized].filter((char) => /[가-힣]/.test(char));
  return syllables.length ? syllables[syllables.length - 1] : '';
}

function normalizeEnglishCell(value) {
  const letters = String(value || '').toUpperCase().replace(/[^A-Z]/g, '');
  return letters.slice(-1);
}

function cellIndexInActive(input) {
  const entry = activeEntry();
  if (!entry) return { entry: null, cells: [], index: -1 };
  const cells = entryCells(entry);
  const index = cells.findIndex(([row, col]) => cellKey(row, col) === input.parentElement.dataset.cell);
  return { entry, cells, index };
}

function moveInActive(input, delta) {
  const { cells, index } = cellIndexInActive(input);
  const targetIndex = index + delta;
  if (index < 0 || targetIndex < 0 || targetIndex >= cells.length) return;
  const [row, col] = cells[targetIndex];
  inputForCell(row, col)?.focus();
}

function commitCell(input) {
  if (input.dataset.composing === '1') return;
  const nextValue = state.language === 'en'
    ? normalizeEnglishCell(input.value)
    : normalizeKoreanCell(input.value);
  input.value = nextValue;
  if (nextValue) moveInActive(input, 1);
}

function bindCellInput(input) {
  const [row, col] = input.parentElement.dataset.cell.split('-').map(Number);
  input.autocomplete = 'off';
  input.autocapitalize = 'characters';
  input.spellcheck = false;
  input.setAttribute('aria-label', `${row + 1}행 ${col + 1}열`);
  if (state.language === 'en') input.maxLength = 1;
  else input.removeAttribute('maxlength');

  input.addEventListener('focus', () => selectEntryForCell(row, col, input));
  input.addEventListener('click', () => selectEntryForCell(row, col, input));
  input.addEventListener('compositionstart', () => {
    input.dataset.composing = '1';
  });
  input.addEventListener('compositionend', () => {
    input.dataset.composing = '0';
    commitCell(input);
  });
  input.addEventListener('input', () => {
    if (input.dataset.composing === '1') return;
    commitCell(input);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Backspace' && !input.value) {
      event.preventDefault();
      moveInActive(input, -1);
      return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveInActive(input, -1);
      return;
    }
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      moveInActive(input, 1);
    }
  });
}

function renderPuzzle() {
  const puzzle = state.puzzle;
  $('#grid').style.gridTemplateColumns = `repeat(${puzzle.cols},1fr)`;
  const valid = new Map(puzzle.cells.map((cell) => [cellKey(cell.row, cell.col), cell]));
  let html = '';
  for (let row = 0; row < puzzle.rows; row += 1) {
    for (let col = 0; col < puzzle.cols; col += 1) {
      const cell = valid.get(cellKey(row, col));
      html += cell
        ? `<div class="cell" data-cell="${cellKey(row, col)}">${cell.number ? `<span class="num">${cell.number}</span>` : ''}<input inputmode="text" autocomplete="off"></div>`
        : '<div class="cell block"></div>';
    }
  }
  $('#grid').innerHTML = html;
  $('#clues').innerHTML = puzzle.entries
    .map((entry) => `<div class="clue" data-clue="${entry.id}"><strong>${entry.number}${entry.direction === 'across' ? '→' : '↓'}</strong>${entry.clue} <small>(${entry.length})</small></div>`)
    .join('');
  $$('.clue').forEach((element) => {
    element.onclick = () => selectEntry(element.dataset.clue);
  });
  $$('.cell input').forEach(bindCellInput);
  selectEntry(puzzle.entries[0]?.id);
}

function answers() {
  const result = {};
  for (const entry of state.puzzle.entries) {
    result[entry.id] = entryCells(entry)
      .map(([row, col]) => inputForCell(row, col)?.value || '')
      .join('');
  }
  return result;
}

async function loadRank() {
  try {
    const data = await api(`/api/crossword/leaderboard?language=${state.language}`);
    $('#leaderboard').innerHTML = data.players.length
      ? data.players
          .map((player) => `<div class="rank ${player.is_me ? 'me' : ''}"><span>${player.rank ?? '-'}</span><span>${player.nickname}${player.is_me ? ' (나)' : ''}</span><span>${player.score ?? '미완료'}</span></div>`)
          .join('')
      : '친구를 초대해 랭킹을 시작하세요.';
  } catch {
    $('#leaderboard').textContent = '랭킹을 불러오지 못했습니다.';
  }
}

async function load() {
  await ensureSession();
  state.puzzle = await api(`/api/crossword/today?language=${state.language}`);
  $('#dateText').textContent = `${state.puzzle.publishDate} · 고등학교+ · 간접 단서 · 매일 새 문제`;
  renderPuzzle();
  await api(`/api/crossword/plays/${encodeURIComponent(state.puzzle.puzzle_id)}/start`, { method: 'POST' });
  await loadRank();

  const query = new URLSearchParams(location.search);
  if (query.get('invite')) {
    try {
      await api(`/api/crossword/friends/invite/${encodeURIComponent(query.get('invite'))}/accept`, { method: 'POST' });
      $('#status').textContent = '친구 등록 완료!';
      query.delete('invite');
      history.replaceState(null, '', location.pathname + (query.toString() ? `?${query}` : ''));
      await loadRank();
    } catch (error) {
      $('#status').textContent = error.message;
    }
  }
  if (query.get('hint')) await helperMode(query.get('hint'));
}

async function share(title, url) {
  try {
    const config = await api('/api/crossword/config');
    if (config.kakaoJavaScriptKey) {
      if (!window.Kakao) {
        await new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = `https://t1.kakaocdn.net/kakao_js_sdk/${config.sdkVersion}/kakao.min.js`;
          script.onload = resolve;
          script.onerror = reject;
          document.head.appendChild(script);
        });
      }
      if (!Kakao.isInitialized()) Kakao.init(config.kakaoJavaScriptKey);
      Kakao.Share.sendDefault({
        objectType: 'text',
        text: title,
        link: { mobileWebUrl: url, webUrl: url },
        buttonTitle: '열기',
      });
      return;
    }
  } catch {}
  if (navigator.share) {
    await navigator.share({ title, text: title, url });
    return;
  }
  await navigator.clipboard.writeText(url);
  alert('공유 링크를 복사했습니다.');
}

$('#submitBtn').onclick = async () => {
  try {
    const result = await api(`/api/crossword/plays/${encodeURIComponent(state.puzzle.puzzle_id)}/submit`, {
      method: 'POST',
      body: JSON.stringify({ answers: answers() }),
    });
    if (result.correct) {
      $('#status').textContent = `정답! ${result.score}점 · ${Math.round(result.elapsedMs / 1000)}초`;
      await loadRank();
    } else {
      $('#status').textContent = `아직 ${result.wrongClueIds.length}개가 틀렸습니다.`;
    }
  } catch (error) {
    $('#status').textContent = error.message;
  }
};

$('#hintBtn').onclick = async () => {
  if (!state.active) return alert('힌트를 받을 문제를 먼저 선택하세요.');
  const result = await api('/api/crossword/hints', {
    method: 'POST',
    body: JSON.stringify({ puzzleId: state.puzzle.puzzle_id, clueId: state.active }),
  });
  state.hintRequestId = result.requestId;
  await share('💡 친구가 크로스워드 힌트를 부탁했어요. 정답이나 글자를 직접 말하지 말고 연상 단서만 주세요!', result.shareUrl);
  pollHint();
};

async function pollHint() {
  for (let index = 0; state.hintRequestId && index < 30; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const result = await api(`/api/crossword/hints/${state.hintRequestId}`);
    if (result.status === 'answered') {
      alert(`친구 힌트: ${result.hint_text}`);
      state.hintRequestId = null;
    }
  }
}

$('#friendBtn').onclick = async () => {
  const result = await api('/api/crossword/friends/invite', { method: 'POST' });
  await share('🧩 오늘의 크로스워드 같이 풀고 친구 랭킹에서 겨뤄요!', result.shareUrl);
};

$('#nicknameBtn').onclick = async () => {
  const nickname = prompt('랭킹에 표시할 닉네임 (2~20자)');
  if (nickname) {
    await api('/api/crossword/me/nickname', {
      method: 'POST',
      body: JSON.stringify({ nickname }),
    });
    await loadRank();
  }
};

$$('[data-lang]').forEach((button) => {
  button.onclick = async () => {
    state.language = button.dataset.lang;
    $$('[data-lang]').forEach((item) => item.classList.toggle('active', item === button));
    await load();
  };
});

async function helperMode(token) {
  $('#game').classList.add('hidden');
  $('#helper').classList.remove('hidden');
  try {
    const result = await api(`/api/crossword/hints/help/${encodeURIComponent(token)}`);
    $('#helper').innerHTML = `<div class="helperBox"><h2>친구에게 힌트 보내기</h2><p><strong>${result.clue.number}${result.clue.direction === 'across' ? '→' : '↓'}</strong> ${result.clue.clue} (${result.clue.length})</p><p>정답·초성·첫 글자·끝 글자·철자를 직접 알려주는 힌트는 보낼 수 없습니다. 의미, 상황, 연상, 예문처럼 한 단계 돌아가는 단서를 적어주세요.</p><textarea id="hintText" maxlength="240"></textarea><button id="sendHint">힌트 보내기</button><p id="helperStatus"></p></div>`;
    $('#sendHint').onclick = async () => {
      try {
        await api(`/api/crossword/hints/help/${encodeURIComponent(token)}`, {
          method: 'POST',
          body: JSON.stringify({ hintText: $('#hintText').value }),
        });
        $('#helperStatus').textContent = '힌트를 보냈습니다. 내용은 요청한 친구에게만 공개됩니다.';
      } catch (error) {
        $('#helperStatus').textContent = error.message === 'DIRECT_HINT_NOT_ALLOWED'
          ? '정답이나 글자를 직접 알려주는 힌트는 보낼 수 없습니다. 의미나 상황으로 다시 적어주세요.'
          : error.message;
      }
    };
  } catch {
    $('#helper').innerHTML = '<div class="helperBox"><h2>힌트 링크가 만료되었거나 사용할 수 없습니다.</h2></div>';
  }
}

load().catch((error) => {
  $('#status').textContent = `게임 로드 실패: ${error.message}`;
});
