/* LiteDiff – felület */
'use strict';

window.addEventListener('error', e => showError(e.message));
window.addEventListener('unhandledrejection', e => showError(String((e.reason && e.reason.message) || e.reason)));
function showError(msg) {
  const b = document.getElementById('banner');
  if (!b) return;
  b.textContent = 'Hiba: ' + msg;
  b.classList.remove('hidden');
}

const BADGE = { new: '➕', modified: '✏️', deleted: '🗑️', renamed: '➡️', conflict: '⚠️' };
const BADGE_TITLE = { new: 'Új', modified: 'Módosított', deleted: 'Törölt', renamed: 'Átnevezett', conflict: 'Ütközés' };

const state = {
  repos: [],                 // [{path, name, branch, head, changes, error, compare}]
  collapsed: new Set(JSON.parse(localStorage.getItem('collapsed') || '[]')),
  selected: null,            // {kind: 'work'|'commit', repo, path, sha}
  current: null,             // {mode: 'work'|'commit', repo, change, bom, savedVersion, readOnly, baseRev, sha}
  sideBySide: localStorage.getItem('sideBySide') !== '0',
  revealPending: false,
  activeRepo: localStorage.getItem('activeRepo') || null,
  history: null,             // {repo, branch, base, base_source, commits, more}
  expanded: new Set(),       // kinyitott commitok (sha) az aktív repóban
  commitFiles: new Map(),    // sha -> fájlok (a commit tartalma nem változik, ezért nem kell üríteni)
  historyCache: new Map(),   // repó -> utoljára betöltött history (repóváltáskor azonnal ezt mutatjuk)
  expandedByRepo: new Map(), // repó -> kinyitott commitok
  historySeq: new Map(),     // repó -> a legutóbbi frissítési kérés sorszáma (a régebbi válasz eldobható)
  refreshing: new Set(),     // repók, amelyeknél épp háttérfrissítés fut
  loadingMore: false,
};

let api;
let monacoRef;
let diffEditor;
const $ = id => document.getElementById(id);

// ---------- indulás ----------
window.addEventListener('pywebviewready', start);

async function start() {
  api = window.pywebview.api;
  const width = parseInt(localStorage.getItem('sidebarWidth') || '0', 10);
  if (width) $('sidebar').style.width = width + 'px';
  const commitsHeight = parseInt(localStorage.getItem('commitsHeight') || '0', 10);
  if (commitsHeight) $('commits-section').style.height = commitsHeight + 'px';
  setupSplitter();
  setupHSplitter();
  setupButtons();
  await loadMonaco();
  state.repos = await api.repos();
  if (!state.repos.some(r => r.path === state.activeRepo)) state.activeRepo = state.repos.length ? state.repos[0].path : null;
  if (state.activeRepo) state.expandedByRepo.set(state.activeRepo, state.expanded);
  renderTree();
  loadHistory(true);
  setTimeout(prefetchHistories, 2000);
}

// indulás után a háttérben, egymás után, kis szünetekkel lekérjük a többi repó commitlistáját is,
// hogy repóváltáskor már ne kelljen várni
async function prefetchHistories() {
  for (const repo of state.repos.slice()) {
    if (repo.error || repo.path === state.activeRepo) continue;
    if (state.historyCache.has(repo.path) || state.historySeq.has(repo.path)) continue;
    const seq = 1;
    state.historySeq.set(repo.path, seq);
    let res;
    try { res = await api.history(repo.path, 0, 50); } catch (e) { res = { ok: false }; }
    if (state.historySeq.get(repo.path) === seq) {   // közben nem kérte le más (pl. repóváltás)
      if (res.ok) {
        state.historyCache.set(repo.path, res);
        if (state.activeRepo === repo.path) { state.history = res; renderCommits(); }
      } else {
        state.historySeq.delete(repo.path);
      }
    }
    await new Promise(r => setTimeout(r, 400));
  }
}

function loadMonaco() {
  return new Promise(resolve => {
    require.config({ paths: { vs: 'vendor/vs' } });
    require(['vs/editor/editor.main'], () => {
      monacoRef = window.monaco;
      diffEditor = monacoRef.editor.createDiffEditor($('editor'), {
        theme: 'vs-dark',
        automaticLayout: true,
        renderSideBySide: state.sideBySide,
        originalEditable: false,
        readOnly: false,
        ignoreTrimWhitespace: false,
        renderOverviewRuler: true,
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        useInlineViewWhenSpaceIsLimited: false,
        hideUnchangedRegions: { enabled: false },
      });
      diffEditor.onDidUpdateDiff(() => {
        if (state.revealPending) {
          state.revealPending = false;
          goToChange(1, true);
        }
      });
      const modifiedEditor = diffEditor.getModifiedEditor();
      modifiedEditor.addCommand(monacoRef.KeyMod.CtrlCmd | monacoRef.KeyCode.KeyS, saveCurrent);
      modifiedEditor.addCommand(monacoRef.KeyCode.F7, () => goToChange(1));
      modifiedEditor.addCommand(monacoRef.KeyMod.Shift | monacoRef.KeyCode.F7, () => goToChange(-1));
      $('editor').classList.add('hidden');
      resolve();
    });
  });
}

// ---------- gombok, billentyűk, elválasztók ----------
function setupButtons() {
  $('btn-add').onclick = async () => {
    const added = await api.add_repo_dialog();
    if (added.length) {
      state.repos = await api.repos();
      if (!state.activeRepo) setActiveRepo(added[0]);
      renderTree();
    }
  };
  $('btn-collapse').onclick = () => {
    const keys = allFolderKeys();
    const anyOpen = keys.some(k => !state.collapsed.has(k));
    state.collapsed = anyOpen ? new Set(keys) : new Set();
    persistCollapsed();
    renderTree();
  };
  $('btn-next').onclick = () => goToChange(1);
  $('btn-prev').onclick = () => goToChange(-1);
  $('btn-save').onclick = saveCurrent;
  $('btn-inline').onclick = () => {
    state.sideBySide = !state.sideBySide;
    localStorage.setItem('sideBySide', state.sideBySide ? '1' : '0');
    diffEditor && diffEditor.updateOptions({ renderSideBySide: state.sideBySide });
  };
  $('commits-base').onclick = e => { e.stopPropagation(); pickBase(state.activeRepo, $('commits-base')); };
  $('btn-refresh-commits').onclick = e => { e.stopPropagation(); loadHistory(false); };
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveCurrent(); }
    else if (e.key === 'F7') { e.preventDefault(); goToChange(e.shiftKey ? -1 : 1); }
  });
  $('tree').addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); stepFile('#tree', e.key === 'ArrowDown' ? 1 : -1); }
  });
  $('commits').addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); stepFile('#commits', e.key === 'ArrowDown' ? 1 : -1); }
  });
}

function setupSplitter() {
  const splitter = $('splitter');
  splitter.addEventListener('mousedown', e => {
    e.preventDefault();
    splitter.classList.add('drag');
    const move = ev => { $('sidebar').style.width = Math.max(180, Math.min(ev.clientX, window.innerWidth - 300)) + 'px'; };
    const up = () => {
      splitter.classList.remove('drag');
      localStorage.setItem('sidebarWidth', parseInt($('sidebar').style.width, 10));
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
}

function setupHSplitter() {
  const splitter = $('hsplitter');
  splitter.addEventListener('mousedown', e => {
    e.preventDefault();
    splitter.classList.add('drag');
    const sidebar = $('sidebar').getBoundingClientRect();
    const move = ev => {
      const h = Math.max(60, Math.min(sidebar.bottom - ev.clientY, sidebar.height - 100));
      $('commits-section').style.height = h + 'px';
    };
    const up = () => {
      splitter.classList.remove('drag');
      localStorage.setItem('commitsHeight', parseInt($('commits-section').style.height, 10));
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
}

// ---------- segédek ----------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function findRepo(path) {
  return state.repos.find(r => r.path === path);
}

function relTime(ts) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return 'most';
  if (diff < 3600) return Math.floor(diff / 60) + ' perce';
  if (diff < 86400) return Math.floor(diff / 3600) + ' órája';
  if (diff < 86400 * 30) return Math.floor(diff / 86400) + ' napja';
  return new Date(ts * 1000).toLocaleDateString('hu-HU');
}

function shortRef(name) {
  return (name || '').replace(/^refs\/(heads|remotes)\//, '');
}

// ---------- fa (változások) ----------
function buildTree(changes) {
  const root = { dirs: new Map(), files: [] };
  for (const ch of changes) {
    const parts = ch.path.split('/');
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    }
    node.files.push({ name: parts[parts.length - 1], change: ch });
  }
  return root;
}

function allFolderKeys() {
  const keys = [];
  for (const repo of state.repos) {
    keys.push('repo:' + repo.path);
    const walk = (node, prefix) => {
      for (const [name, child] of node.dirs) {
        const p = prefix ? prefix + '/' + name : name;
        keys.push('dir:' + repo.path + '|' + p);
        walk(child, p);
      }
    };
    walk(buildTree(repo.changes), '');
  }
  return keys;
}

function persistCollapsed() {
  localStorage.setItem('collapsed', JSON.stringify([...state.collapsed]));
}

function toggle(key) {
  if (state.collapsed.has(key)) state.collapsed.delete(key); else state.collapsed.add(key);
  persistCollapsed();
  renderTree();
}

function renderTree() {
  const tree = $('tree');
  const scroll = tree.scrollTop;
  tree.innerHTML = '';
  $('empty-hint').classList.toggle('hidden', state.repos.length > 0);
  for (const repo of state.repos) {
    const repoKey = 'repo:' + repo.path;
    const open = !state.collapsed.has(repoKey);
    const row = el('div', 'row repo' + (repo.path === state.activeRepo ? ' active-repo' : ''));
    row.title = repo.path;

    const branchBtn = el('span', 'branch', '⎇ ' + repo.branch);
    branchBtn.title = 'Ág váltása';
    branchBtn.onclick = e => { e.stopPropagation(); showBranchMenu(repo, branchBtn); };

    const cmp = repo.compare;
    const compareBtn = el('span', 'compare' + (cmp ? ' on' : ''), cmp ? '⇄ ' + shortRef(cmp.base || '?') : '⇄');
    compareBtn.title = cmp
      ? `Pull request nézet: összevetés a(z) ${shortRef(cmp.base || '?')} ággal (${cmp.source}). Kattints a beállításhoz.`
      : 'Pull request nézet bekapcsolása (összevetés az alapággal)';
    compareBtn.onclick = e => { e.stopPropagation(); compareClick(e, repo, compareBtn); };

    row.append(el('span', 'twisty', open ? '▼' : '▶'), el('span', 'label', repo.name), branchBtn, compareBtn);
    const actions = el('span', 'repo-actions');
    const remove = el('button', '', '✕');
    remove.title = 'Eltávolítás a listából (a mappához nem nyúl)';
    remove.onclick = async e => {
      e.stopPropagation();
      if (!confirm(`Eltávolítod a(z) ${repo.name} repót a listából?`)) return;
      await api.remove_repo(repo.path);
      state.repos = state.repos.filter(r => r.path !== repo.path);
      if (state.activeRepo === repo.path) setActiveRepo(state.repos.length ? state.repos[0].path : null);
      renderTree();
    };
    actions.append(remove);
    row.append(actions, el('span', 'count', String(repo.changes.length)));
    setupRepoDrag(row, repo);
    row.onclick = () => { toggle(repoKey); activateRepoFromRow(repo.path); };
    row.oncontextmenu = e => { e.preventDefault(); repoMenu(e, repo); };
    tree.append(row);
    if (!open) continue;
    if (repo.error) { tree.append(el('div', 'error', repo.error)); continue; }
    if (cmp) {
      const info = cmp.error
        ? el('div', 'error', cmp.error)
        : el('div', 'compare-info', `Pull request nézet: ${shortRef(cmp.base)} óta (közös ős: ${(cmp.merge_base || '').slice(0, 8)})`);
      tree.append(info);
    }
    if (!repo.changes.length) { tree.append(el('div', 'clean', 'Nincs változás.')); continue; }
    renderNode(tree, repo, buildTree(repo.changes), '', 1);
  }
  tree.scrollTop = scroll;
}

function renderNode(container, repo, node, prefix, depth) {
  const dirs = [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { sensitivity: 'base' }));
  for (let [name, child] of dirs) {
    // egygyermekes mappák összevonása, mint a VS Code-ban
    let path = prefix ? prefix + '/' + name : name;
    while (child.files.length === 0 && child.dirs.size === 1) {
      const [nextName, nextChild] = child.dirs.entries().next().value;
      name += '/' + nextName;
      path += '/' + nextName;
      child = nextChild;
    }
    const key = 'dir:' + repo.path + '|' + path;
    const open = !state.collapsed.has(key);
    const row = el('div', 'row folder');
    row.style.paddingLeft = (depth * 12) + 'px';
    row.append(el('span', 'twisty', open ? '▼' : '▶'), el('span', 'label', name));
    row.onclick = () => toggle(key);
    container.append(row);
    if (open) renderNode(container, repo, child, path, depth + 1);
  }
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  for (const file of files) {
    const ch = file.change;
    const row = fileRow(file.name, ch, depth * 12 + 16);
    row.dataset.key = 'work|' + repo.path + '|' + ch.path;
    if (isSelected('work', repo.path, ch.path)) row.classList.add('selected');
    row.onclick = () => { $('tree').focus(); openFile(repo.path, ch); };
    row.oncontextmenu = e => { e.preventDefault(); fileMenu(e, repo, ch); };
    container.append(row);
  }
}

function fileRow(name, ch, indent) {
  const row = el('div', 'row file status-' + ch.status);
  row.style.paddingLeft = indent + 'px';
  row.title = ch.orig_path ? `${ch.orig_path} → ${ch.path}` : ch.path;
  row.append(el('span', 'label', name));
  if (ch.orig_path) row.append(el('span', 'dir', '← ' + ch.orig_path.split('/').pop()));
  const badge = el('span', 'badge', BADGE[ch.status] || '•');
  badge.title = BADGE_TITLE[ch.status] || ch.status;
  row.append(badge);
  return row;
}

function isSelected(kind, repo, path, sha) {
  const s = state.selected;
  return !!s && s.kind === kind && s.repo === repo && s.path === path && (kind !== 'commit' || s.sha === sha);
}

function markSelected() {
  document.querySelectorAll('#sidebar .row.selected').forEach(r => r.classList.remove('selected'));
  const s = state.selected;
  if (!s) return;
  const key = s.kind === 'commit' ? `commit|${s.repo}|${s.sha}|${s.path}` : `work|${s.repo}|${s.path}`;
  const row = [...document.querySelectorAll('#sidebar .row.file')].find(r => r.dataset.key === key);
  if (row) row.classList.add('selected');
}

function stepFile(scope, delta) {
  const rows = [...document.querySelectorAll(scope + ' .row.file')];
  if (!rows.length) return;
  let i = rows.findIndex(r => r.classList.contains('selected'));
  i = i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + delta));
  rows[i].click();
  rows[i].scrollIntoView({ block: 'nearest' });
}

// ---------- aktív repó és commitok ----------
function activateRepoFromRow(path) {
  if (state.activeRepo === path) return;
  if (!closeFile()) return;
  setActiveRepo(path);
}

function setActiveRepo(path) {
  if (state.activeRepo === path && state.history && state.history.repo === path) return;
  state.activeRepo = path;
  if (path) localStorage.setItem('activeRepo', path); else localStorage.removeItem('activeRepo');
  // amit már ismerünk, azt azonnal mutatjuk, a frissítés a háttérben fut
  state.history = path ? state.historyCache.get(path) || null : null;
  if (path && !state.expandedByRepo.has(path)) state.expandedByRepo.set(path, new Set());
  state.expanded = path ? state.expandedByRepo.get(path) : new Set();
  document.querySelectorAll('#tree .row.repo').forEach(r => r.classList.toggle('active-repo', r.title === path));
  renderCommits();
  // a figyelő minden commitot, ágváltást és push/fetch-et jelez, így a meglévő lista mindig friss
  const cached = path ? state.historyCache.get(path) : null;
  if (!cached || cached.error || cached.stale) loadHistory(true);
}

async function loadHistory(reset, { more = false } = {}) {
  const repoPath = state.activeRepo;
  if (!repoPath) { state.history = null; renderCommits(); return; }
  const known = state.history && state.history.repo === repoPath && !state.history.error ? state.history : null;
  if (more) {
    if (state.loadingMore || !known) return;
    state.loadingMore = true;
    const res = await api.history(repoPath, known.commits.length, 50);
    state.loadingMore = false;
    if (!res.ok || state.historyCache.get(repoPath) !== known) return;
    known.commits.push(...res.commits);
    known.more = res.more;
    if (state.activeRepo === repoPath) renderCommits();
    return;
  }
  // frissítés: a már betöltött darabszámot kérjük le újra; a régi lista addig látszik
  const seq = (state.historySeq.get(repoPath) || 0) + 1;
  state.historySeq.set(repoPath, seq);
  state.refreshing.add(repoPath);
  updateRefreshButton();
  const limit = Math.max(50, known ? known.commits.length : 0);
  let res;
  try {
    res = await api.history(repoPath, 0, limit);
  } finally {
    if (state.historySeq.get(repoPath) === seq) state.refreshing.delete(repoPath);
    updateRefreshButton();
  }
  if (state.historySeq.get(repoPath) !== seq) return;   // közben újabb frissítés indult
  const fresh = res.ok ? res : { repo: repoPath, error: res.error, commits: [] };
  state.historyCache.set(repoPath, fresh);
  if (state.activeRepo !== repoPath) return;
  state.history = fresh;
  renderCommits();
}

function updateRefreshButton() {
  const btn = $('btn-refresh-commits');
  const busy = !!state.activeRepo && state.refreshing.has(state.activeRepo);
  btn.classList.toggle('spin', busy);
  btn.disabled = !state.activeRepo;
  btn.title = busy ? 'Commitok frissítése folyamatban' : 'Commitok frissítése';
}

function renderCommits() {
  const box = $('commits');
  const scroll = box.scrollTop;
  box.innerHTML = '';
  const repo = findRepo(state.activeRepo);
  const h = state.history;
  $('commits-repo').textContent = repo ? `${repo.name} ⎇ ${repo.branch}` : '';
  // az alapág csak ikon; a neve a súgóban látszik
  const baseBtn = $('commits-base');
  baseBtn.classList.toggle('hidden', !(h && h.repo === state.activeRepo && !h.error));
  baseBtn.title = h && h.base ? `Alapág: ${shortRef(h.base)} (${h.base_source})\nKattints a módosításhoz.` : 'Alapág – kattints a beállításhoz';
  updateRefreshButton();
  if (!repo) { box.append(el('div', 'clean', 'Kattints egy repóra.')); return; }
  if (!h) { box.append(el('div', 'clean', 'Betöltés')); return; }
  if (h.error) { box.append(el('div', 'error', h.error)); return; }
  for (const c of h.commits) {
    const open = state.expanded.has(c.sha);
    const row = el('div', 'row commit ' + (c.own ? 'own' : 'base'));
    row.title = `${c.short}  ${c.author}  ${new Date(c.time * 1000).toLocaleString('hu-HU')}\n\n${c.subject}${c.body ? '\n\n' + c.body : ''}`;
    row.append(el('span', 'twisty', open ? '▼' : '▶'));
    const mark = el('span', 'cmark', c.merge ? '⑂' : '●');
    row.append(mark, el('span', 'label', c.subject));
    if (c.unpushed) { const u = el('span', 'unpushed', '⬆'); u.title = 'Még nincs pusholva'; row.append(u); }
    row.append(el('span', 'meta', `${c.author.split(' ')[0]} · ${relTime(c.time)}`));
    row.onclick = () => toggleCommit(c);
    box.append(row);
    if (open) renderCommitFiles(box, c);
  }
  if (h.more) {
    const more = el('div', 'row more', 'Továbbiak betöltése');
    more.onclick = () => loadHistory(false, { more: true });
    box.append(more);
  }
  if (!h.commits.length) box.append(el('div', 'clean', 'Nincs commit.'));
  box.scrollTop = scroll;
}

async function toggleCommit(c) {
  if (state.expanded.has(c.sha)) { state.expanded.delete(c.sha); renderCommits(); return; }
  state.expanded.add(c.sha);
  if (!state.commitFiles.has(c.sha)) {
    renderCommits();
    const res = await api.commit_files(state.activeRepo, c.sha, c.parents[0] || null);
    state.commitFiles.set(c.sha, res.ok ? res.files : { error: res.error });
  }
  renderCommits();
}

function renderCommitFiles(box, c) {
  const files = state.commitFiles.get(c.sha);
  if (!files) { box.append(el('div', 'clean indent', 'Betöltés')); return; }
  if (files.error) { box.append(el('div', 'error', files.error)); return; }
  if (!files.length) { box.append(el('div', 'clean indent', 'Nincs fájlváltozás.')); return; }
  const repoPath = state.activeRepo;
  for (const f of files) {
    const parts = f.path.split('/');
    const name = parts.pop();
    const row = fileRow(name, f, 34);
    if (parts.length) row.insertBefore(el('span', 'dir', parts.join('/')), row.querySelector('.badge'));
    row.dataset.key = `commit|${repoPath}|${c.sha}|${f.path}`;
    if (isSelected('commit', repoPath, f.path, c.sha)) row.classList.add('selected');
    row.onclick = () => { $('commits').focus(); openCommitFile(repoPath, c, f); };
    box.append(row);
  }
}

// ---------- fájl megnyitása ----------
function languageFor(path) {
  const lower = path.toLowerCase();
  const name = lower.split('/').pop();
  for (const lang of monacoRef.languages.getLanguages()) {
    if ((lang.filenames || []).some(f => f.toLowerCase() === name)) return lang.id;
  }
  for (const lang of monacoRef.languages.getLanguages()) {
    if ((lang.extensions || []).some(ext => lower.endsWith(ext.toLowerCase()))) return lang.id;
  }
  return 'plaintext';
}

function isDirty() {
  const cur = state.current;
  if (!cur || cur.readOnly) return false;
  const model = diffEditor.getModel();
  return !!model && model.modified.getAlternativeVersionId() !== cur.savedVersion;
}

function closeFile() {
  if (isDirty() && !confirm('A fájlban mentetlen módosítás van. Eldobod?')) return false;
  const old = diffEditor && diffEditor.getModel();
  if (diffEditor) diffEditor.setModel(null);
  if (old) { old.original.dispose(); old.modified.dispose(); }
  state.current = null;
  state.selected = null;
  markSelected();
  api.close_file();
  $('editor').classList.add('hidden');
  $('placeholder').classList.remove('hidden');
  $('placeholder').textContent = 'Válassz egy fájlt a bal oldali listából.';
  $('banner').classList.add('hidden');
  updateTitle();
  return true;
}

function showModels(data, path, readOnly, silent) {
  const viewState = silent ? diffEditor.saveViewState() : null;
  const old = diffEditor.getModel();
  $('banner').classList.add('hidden');
  if (data.binary) {
    diffEditor.setModel(null);
    if (old) { old.original.dispose(); old.modified.dispose(); }
    $('editor').classList.add('hidden');
    $('placeholder').classList.remove('hidden');
    $('placeholder').textContent = 'Bináris fájl, nem jeleníthető meg.';
    return null;
  }
  const lang = languageFor(path);
  const original = monacoRef.editor.createModel(data.original, lang);
  const modified = monacoRef.editor.createModel(data.modified, lang);
  diffEditor.setModel({ original, modified });
  diffEditor.getModifiedEditor().updateOptions({ readOnly });
  if (old) { old.original.dispose(); old.modified.dispose(); }
  modified.onDidChangeContent(updateTitle);
  $('placeholder').classList.add('hidden');
  $('editor').classList.remove('hidden');
  if (viewState) diffEditor.restoreViewState(viewState);
  else state.revealPending = true;
  return modified;
}

async function openFile(repoPath, change, { silent = false } = {}) {
  const cur = state.current;
  const same = cur && cur.mode === 'work' && cur.repo === repoPath && cur.change.path === change.path;
  if (!silent && same) return;
  if (!silent && isDirty() && !confirm('A fájlban mentetlen módosítás van. Eldobod?')) return;
  if (!silent && state.activeRepo !== repoPath) setActiveRepo(repoPath);
  state.selected = { kind: 'work', repo: repoPath, path: change.path };
  markSelected();
  const repo = findRepo(repoPath);
  const baseRev = repo && repo.compare && repo.compare.merge_base ? repo.compare.merge_base : null;
  const data = await api.open_file(repoPath, change.path, change.orig_path, change.status, baseRev);
  const readOnly = change.status === 'deleted' || !data.exists;
  const modified = showModels(data, change.path, readOnly, silent);
  state.current = {
    mode: 'work', repo: repoPath, change, bom: data.bom, baseRev,
    savedVersion: modified ? modified.getAlternativeVersionId() : 0, readOnly: readOnly || !modified,
  };
  updateTitle();
}

async function openCommitFile(repoPath, commit, file) {
  const cur = state.current;
  if (cur && cur.mode === 'commit' && cur.sha === commit.sha && cur.change.path === file.path) return;
  if (isDirty() && !confirm('A fájlban mentetlen módosítás van. Eldobod?')) return;
  state.selected = { kind: 'commit', repo: repoPath, path: file.path, sha: commit.sha };
  markSelected();
  const data = await api.open_commit_file(repoPath, commit.sha, commit.parents[0] || null, file.path, file.orig_path, file.status);
  showModels(data, file.path, true, false);
  state.current = { mode: 'commit', repo: repoPath, change: file, sha: commit.sha, short: commit.short, bom: false, savedVersion: 0, readOnly: true };
  updateTitle();
}

function updateTitle() {
  const cur = state.current;
  const title = $('file-title');
  title.innerHTML = '';
  if (!cur) { title.textContent = 'Nincs megnyitott fájl'; $('btn-save').disabled = true; return; }
  const dirty = isDirty();
  const parts = cur.change.path.split('/');
  if (dirty) title.append(el('span', 'dirty', '● '));
  title.append(document.createTextNode(parts.pop()));
  title.append(el('span', 'path', parts.join('/')));
  if (cur.mode === 'commit') title.append(el('span', 'path', `(commit ${cur.short}, csak olvasható)`));
  else if (cur.baseRev) title.append(el('span', 'path', `(pull request nézet${cur.readOnly ? ', csak olvasható' : ''})`));
  else if (cur.readOnly) title.append(el('span', 'path', '(csak olvasható)'));
  $('btn-save').disabled = !dirty;
}

function goToChange(delta, first = false) {
  if (!diffEditor || !diffEditor.getModel()) return;
  const changes = diffEditor.getLineChanges() || [];
  if (!changes.length) return;
  const editor = diffEditor.getModifiedEditor();
  const line = editor.getPosition() ? editor.getPosition().lineNumber : 1;
  const starts = changes.map(c => Math.max(1, c.modifiedStartLineNumber || c.modifiedEndLineNumber || 1));
  let target;
  if (first) target = starts[0];
  else if (delta > 0) target = starts.find(s => s > line) ?? starts[0];
  else target = [...starts].reverse().find(s => s < line) ?? starts[starts.length - 1];
  editor.setPosition({ lineNumber: target, column: 1 });
  editor.revealLineInCenter(target);
  if (!first) editor.focus();
}

async function saveCurrent() {
  const cur = state.current;
  if (!cur || cur.readOnly || !isDirty()) return;
  const model = diffEditor.getModel().modified;
  const version = model.getAlternativeVersionId();
  const res = await api.save_file(cur.repo, cur.change.path, model.getValue(), cur.bom);
  if (!res.ok) { alert('Mentési hiba: ' + res.error); return; }
  cur.savedVersion = version;
  $('banner').classList.add('hidden');
  updateTitle();
}

function showBanner(text, buttons) {
  const banner = $('banner');
  banner.innerHTML = '';
  banner.append(el('span', '', text));
  for (const [label, fn] of buttons) {
    const b = el('button', '', label);
    b.onclick = () => { banner.classList.add('hidden'); fn(); };
    banner.append(b);
  }
  banner.classList.remove('hidden');
}

async function reloadModified() {
  const cur = state.current;
  if (!cur || cur.mode !== 'work') return;
  const data = await api.reload_file(cur.repo, cur.change.path);
  const model = diffEditor.getModel();
  if (!model || data.binary) return;
  if (model.modified.getValue() === data.text) { cur.savedVersion = model.modified.getAlternativeVersionId(); updateTitle(); return; }
  const viewState = diffEditor.saveViewState();
  model.modified.setValue(data.text);
  cur.bom = data.bom;
  cur.savedVersion = model.modified.getAlternativeVersionId();
  diffEditor.restoreViewState(viewState);
  updateTitle();
}

// ---------- repók sorrendje húzással ----------
let dragRepo = null;

function setupRepoDrag(row, repo) {
  row.draggable = true;
  row.addEventListener('dragstart', e => {
    dragRepo = repo.path;
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', repo.path);
  });
  row.addEventListener('dragend', () => {
    dragRepo = null;
    document.querySelectorAll('.row.repo').forEach(r => r.classList.remove('dragging', 'drop-before', 'drop-after'));
  });
  row.addEventListener('dragover', e => {
    if (!dragRepo || dragRepo === repo.path) return;
    e.preventDefault();
    const r = row.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    row.classList.toggle('drop-before', before);
    row.classList.toggle('drop-after', !before);
  });
  row.addEventListener('dragleave', () => row.classList.remove('drop-before', 'drop-after'));
  row.addEventListener('drop', async e => {
    e.preventDefault();
    if (!dragRepo || dragRepo === repo.path) return;
    const before = row.classList.contains('drop-before');
    const order = state.repos.map(r => r.path).filter(p => p !== dragRepo);
    let idx = order.indexOf(repo.path);
    if (!before) idx += 1;
    order.splice(idx, 0, dragRepo);
    state.repos.sort((a, b) => order.indexOf(a.path) - order.indexOf(b.path));
    dragRepo = null;
    renderTree();
    await api.reorder_repos(order);
  });
}

// ---------- ágválasztó (ágváltás és alapág) ----------
let branchMenu = null;

function closeBranchMenu() {
  if (branchMenu) { branchMenu.remove(); branchMenu = null; }
}

async function branchPicker(repoPath, anchor, { onPick, extra = [], markCurrent = null, header = null }) {
  closeBranchMenu();
  closeContextMenu();
  const menu = el('div', 'branch-menu');
  branchMenu = menu;
  const filter = el('input', 'branch-filter');
  filter.placeholder = 'Ág keresése';
  const list = el('div', 'branch-list');
  list.append(el('div', 'branch-info', 'Betöltés'));
  if (header) menu.append(header);
  menu.append(filter, list);
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 360)) + 'px';
  menu.style.top = Math.min(r.bottom + 2, window.innerHeight - 200) + 'px';
  menu.addEventListener('mousedown', e => e.stopPropagation());
  filter.focus();

  const data = await api.branches(repoPath);
  if (branchMenu !== menu) return;
  if (data.error) { list.innerHTML = ''; list.append(el('div', 'branch-info error', data.error)); return; }
  const current = markCurrent === null ? data.current : markCurrent;

  const render = () => {
    const q = filter.value.trim().toLowerCase();
    list.innerHTML = '';
    for (const x of extra) {
      if (q && !x.label.toLowerCase().includes(q)) continue;
      const item = el('div', 'branch-item extra');
      item.append(el('span', 'check', ''), el('span', '', x.label));
      item.onclick = () => { closeBranchMenu(); x.onClick(); };
      list.append(item);
    }
    const section = (title, names, isRemote) => {
      const shown = names.filter(n => n.toLowerCase().includes(q));
      if (!shown.length) return;
      list.append(el('div', 'branch-section', title));
      for (const name of shown) {
        const item = el('div', 'branch-item' + (name === current ? ' current' : ''));
        item.append(el('span', 'check', name === current ? '✓' : ''), el('span', '', name));
        item.onclick = () => onPick(name, isRemote, name === current);
        list.append(item);
      }
    };
    section('Helyi ágak', data.local, false);
    section('Távoli ágak', data.remote, true);
    if (!list.children.length) list.append(el('div', 'branch-info', 'Nincs találat.'));
    const first = list.querySelector('.branch-item');
    if (first) first.classList.add('active');
  };
  filter.addEventListener('input', render);
  filter.addEventListener('keydown', e => {
    const items = [...list.querySelectorAll('.branch-item')];
    let i = items.findIndex(x => x.classList.contains('active'));
    if (e.key === 'Escape') closeBranchMenu();
    else if (e.key === 'Enter' && items[i]) items[i].click();
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (items[i]) items[i].classList.remove('active');
      i = Math.max(0, Math.min(items.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
      items[i].classList.add('active');
      items[i].scrollIntoView({ block: 'nearest' });
    }
  });
  render();
}

function showBranchMenu(repo, anchor) {
  branchPicker(repo.path, anchor, { onPick: (name, isRemote, isCurrent) => switchBranch(repo, name, isRemote, isCurrent) });
}

async function switchBranch(repo, name, isRemote, isCurrent) {
  if (isCurrent) { closeBranchMenu(); return; }
  const cur = state.current;
  if (cur && cur.repo === repo.path && isDirty() && !confirm('A megnyitott fájlban mentetlen módosítás van. Ágváltás előtt eldobod?')) return;
  closeBranchMenu();
  const res = await api.switch_branch(repo.path, name, isRemote);
  if (!res.ok) { alert('Az ágváltás nem sikerült:\n\n' + res.error); return; }
  if (cur && cur.repo === repo.path && diffEditor.getModel()) cur.savedVersion = diffEditor.getModel().modified.getAlternativeVersionId();
  applyRepoResult(res);
}

function pickBase(repoPath, anchor) {
  if (!repoPath) return;
  const h = state.history;
  const header = el('div', 'branch-header');
  header.append(el('span', 'branch-header-label', 'Jelenlegi alapág'),
    el('span', 'branch-header-name', h && h.base ? shortRef(h.base) : 'nincs'));
  if (h && h.base_source) header.append(el('span', 'branch-header-source', h.base_source));
  branchPicker(repoPath, anchor, {
    header,
    markCurrent: h ? shortRef(h.base) : '',
    extra: [{ label: '🔍 Automatikus felismerés', onClick: () => setBase(repoPath, null) }],
    onPick: name => { closeBranchMenu(); setBase(repoPath, name); },
  });
}

async function setBase(repoPath, name) {
  const info = await api.set_base(repoPath, name);
  window.liteDiff.onReposChanged([info]);
  if (state.activeRepo === repoPath) loadHistory(false);
}

// ---------- pull request nézet kapcsoló ----------
async function compareClick(e, repo, anchor) {
  if (!repo.compare) {
    const info = await api.set_compare(repo.path, true);
    window.liteDiff.onReposChanged([info]);
    return;
  }
  const r = anchor.getBoundingClientRect();
  showContextMenu(r.left, r.bottom + 2, [
    { info: true, label: `Összevetés: ${shortRef(repo.compare.base || '?')} (${repo.compare.source})` },
    '-',
    { label: '⎇ Alapág választása…', onClick: () => pickBase(repo.path, anchor) },
    { label: '✕ Pull request nézet kikapcsolása', onClick: async () => {
      const info = await api.set_compare(repo.path, false);
      window.liteDiff.onReposChanged([info]);
    } },
  ]);
}

// ---------- jobbklikk-menük ----------
let ctxMenu = null;

function closeContextMenu() {
  if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
}

function showContextMenu(x, y, items) {
  closeContextMenu();
  closeBranchMenu();
  const menu = el('div', 'ctx-menu');
  ctxMenu = menu;
  menu.addEventListener('mousedown', e => e.stopPropagation());
  fillContextMenu(menu, items);
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 4)) + 'px';
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 4)) + 'px';
  return menu;
}

function fillContextMenu(menu, items) {
  menu.innerHTML = '';
  for (const item of items) {
    if (item === '-') { menu.append(el('div', 'ctx-sep')); continue; }
    const row = el('div', 'ctx-item' + (item.info ? ' info' : '') + (item.danger ? ' danger' : ''), item.label);
    if (!item.info) row.onclick = () => { closeContextMenu(); item.onClick(); };
    menu.append(row);
  }
}

function applyRepoResult(res) {
  if (res && res.repo) window.liteDiff.onReposChanged([res.repo]);
}

async function repoMenu(e, repo) {
  const commitItem = { label: '✔️ Commit…', onClick: () => commitDialog(repo) };
  const menu = showContextMenu(e.clientX, e.clientY, [{ info: true, label: 'Statisztika betöltése' }, '-', commitItem]);
  const st = await api.repo_stats(repo.path);
  if (ctxMenu !== menu) return;
  const items = [];
  if (!st.ok) items.push({ info: true, label: st.error });
  else {
    items.push({ info: true, label: `+${st.added}  −${st.deleted} sor (commitolatlan)` });
    if (st.behind) items.push({ info: true, label: `↓ ${st.behind} commit a távoliban (pull kell)` });
  }
  items.push('-', commitItem);
  if (st.ok && (st.ahead > 0 || !st.upstream)) {
    const label = st.upstream ? `⬆️ Push (${st.ahead} commit)` : '⬆️ Push (új ág)';
    items.push({ label, onClick: () => doPush(repo) });
  }
  fillContextMenu(menu, items);
}

async function doPush(repo) {
  flash(`Push folyamatban: ${repo.name}`, 60000);
  const res = await api.push(repo.path);
  if (!res.ok) { flash('', 1); alert('A push nem sikerült:\n\n' + res.error); return; }
  applyRepoResult(res);
  flash(`Push kész: ${repo.name}`);
}

function fileMenu(e, repo, ch) {
  showContextMenu(e.clientX, e.clientY, [
    { label: '↶ Visszaállítás', danger: true, onClick: () => revertFile(repo, ch) },
    { label: '📋 Teljes útvonal másolása', onClick: async () => {
      const res = await api.copy_path(repo.path, ch.path);
      if (res.ok) flash('Kimásolva: ' + res.path); else alert(res.error);
    } },
  ]);
}

async function revertFile(repo, ch) {
  const name = ch.path.split('/').pop();
  if (repo.compare) {
    alert('Pull request nézetben a visszaállítás nem elérhető. Kapcsold ki a nézetet, és ott állítsd vissza a commitolatlan változást.');
    return;
  }
  const what = ch.status === 'new' ? 'A fájl törlődik.' : 'A módosításai elvesznek.';
  if (!confirm(`Biztosan visszaállítod a(z) ${name} fájlt?\n\n${what}`)) return;
  const cur = state.current;
  if (cur && cur.mode === 'work' && cur.repo === repo.path && cur.change.path === ch.path && diffEditor.getModel()) {
    cur.savedVersion = diffEditor.getModel().modified.getAlternativeVersionId();
  }
  const res = await api.revert_file(repo.path, ch.path, ch.status, ch.orig_path);
  if (!res.ok) { alert('A visszaállítás nem sikerült:\n\n' + res.error); return; }
  applyRepoResult(res);
}

function commitDialog(repo) {
  const overlay = el('div', 'overlay');
  const box = el('div', 'dialog');
  const title = el('div', 'dialog-title', `Commit: ${repo.name} (⎇ ${repo.branch})`);
  const input = el('textarea', 'dialog-input');
  input.placeholder = 'Commit üzenet (Ctrl+Enter: commit)';
  const status = el('div', 'dialog-status', 'Minden commitolatlan változás bekerül a commitba.');
  const buttons = el('div', 'dialog-buttons');
  const btnCommit = el('button', 'primary', 'Commit');
  const btnPush = el('button', 'primary', 'Push');
  btnPush.title = 'Commit, majd push';
  const btnCancel = el('button', '', 'Mégse');
  buttons.append(btnCancel, btnCommit, btnPush);
  box.append(title, input, status, buttons);
  overlay.append(box);
  document.body.append(overlay);
  input.focus();
  const close = () => overlay.remove();
  const run = async andPush => {
    if (!input.value.trim()) { input.focus(); status.textContent = 'Írj commit üzenetet.'; return; }
    [btnCommit, btnPush, btnCancel, input].forEach(x => { x.disabled = true; });
    status.textContent = andPush ? 'Commit és push folyamatban' : 'Commit folyamatban';
    const res = await api.commit(repo.path, input.value, andPush);
    applyRepoResult(res);
    if (!res.ok) {
      [btnCommit, btnPush, btnCancel, input].forEach(x => { x.disabled = false; });
      status.textContent = '';
      alert(res.error);
      return;
    }
    close();
    flash(andPush ? `Commit és push kész: ${repo.name}` : `Commit kész: ${repo.name}`);
  };
  btnCancel.onclick = close;
  btnCommit.onclick = () => run(false);
  btnPush.onclick = () => run(true);
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') close();
    else if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); run(false); }
  });
}

let flashTimer = null;
function flash(text, ms = 2500) {
  let t = document.getElementById('toast');
  if (!t) { t = el('div', ''); t.id = 'toast'; document.body.append(t); }
  t.textContent = text;
  t.classList.toggle('show', !!text);
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => t.classList.remove('show'), ms);
}

document.addEventListener('mousedown', () => { closeContextMenu(); closeBranchMenu(); });
window.addEventListener('blur', () => { closeContextMenu(); closeBranchMenu(); });
document.addEventListener('contextmenu', e => { if (!e.target.closest('#editor')) e.preventDefault(); });

// ---------- Pythonból érkező értesítések ----------
window.liteDiff = {
  onReposChanged(infos) {
    const cur = state.current;
    let reopen = null;
    let historyChanged = false;
    for (const info of infos) {
      const i = state.repos.findIndex(r => r.path === info.path);
      if (i < 0) continue;
      const before = state.repos[i];
      state.repos[i] = info;
      if (before.head !== info.head || before.branch !== info.branch || before.upstream !== info.upstream) {
        // nem aktív repó listája elavult: megnyitáskor még látszik, és a háttérben frissül
        if (info.path === state.activeRepo) historyChanged = true; else if (state.historyCache.has(info.path)) state.historyCache.get(info.path).stale = true;
      }
      if (cur && cur.mode === 'work' && info.path === cur.repo) {
        const was = before.changes.find(c => c.path === cur.change.path);
        const now = info.changes.find(c => c.path === cur.change.path);
        const statusChanged = JSON.stringify(was || null) !== JSON.stringify(now || null);
        const baseChanged = JSON.stringify(before.compare || null) !== JSON.stringify(info.compare || null);
        // ágváltásnál, commit után vagy a nézet váltásakor az eredeti oldal is változhatott
        if (before.branch !== info.branch || before.head !== info.head || statusChanged || baseChanged) {
          reopen = now || { ...cur.change, status: 'modified', orig_path: null };
        }
      }
    }
    renderTree();
    renderCommits();
    if (historyChanged) loadHistory(false);
    if (reopen && !isDirty()) openFile(cur.repo, reopen, { silent: true });
  },
  onFileChanged(repo, path) {
    const cur = state.current;
    if (!cur || cur.mode !== 'work' || cur.repo !== repo || cur.change.path !== path) return;
    if (!isDirty()) { reloadModified(); return; }
    showBanner('A fájl kívülről módosult.', [
      ['Újratöltés (a módosításaid elvesznek)', reloadModified],
      ['Megtartom az enyémet', () => {}],
    ]);
  },
};
