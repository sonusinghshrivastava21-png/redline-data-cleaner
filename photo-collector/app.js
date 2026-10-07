// Photo Collector: pick photos, read the date they were taken, tag people,
// and save filtered "collections" (by person and date). Everything stays on
// the device in IndexedDB — nothing is uploaded.

// ---------- Storage (IndexedDB) ----------
const DB_NAME = 'photo-collector';
let dbPromise;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        db.createObjectStore('photos', { keyPath: 'id' });
        db.createObjectStore('collections', { keyPath: 'id' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

async function dbAll(store) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = db.transaction(store).objectStore(store).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function dbPut(store, values) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    for (const v of [].concat(values)) tx.objectStore(store).put(v);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

async function dbDelete(store, ids) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    for (const id of [].concat(ids)) tx.objectStore(store).delete(id);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

// ---------- State ----------
const state = {
  photos: [],          // {id, name, type, taken (ms), dateSource, people[], blob, thumb}
  collections: [],     // {id, name, filter: {people[], mode, from, to}, created}
  filter: { people: [], mode: 'any', from: '', to: '' },
  selecting: false,
  selected: new Set(),
  viewerList: [],
  viewerIndex: 0,
  openCollection: null,
};
const urlCache = new Map(); // blob -> object URL

const $ = (id) => document.getElementById(id);
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

function blobUrl(blob) {
  if (!urlCache.has(blob)) urlCache.set(blob, URL.createObjectURL(blob));
  return urlCache.get(blob);
}

// ---------- Dates ----------
const pad = (n) => String(n).padStart(2, '0');
function dayKey(ms) {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function toLocalInput(ms) {
  const d = new Date(ms);
  return `${dayKey(ms)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function formatDay(key) {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined,
    { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}
function formatShort(ms) {
  return new Date(ms).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

// ---------- Filtering ----------
function allPeople() {
  const set = new Set();
  for (const p of state.photos) for (const n of p.people) set.add(n);
  return [...set].sort((a, b) => a.localeCompare(b));
}

function matches(photo, f) {
  const day = dayKey(photo.taken);
  if (f.from && day < f.from) return false;
  if (f.to && day > f.to) return false;
  if (f.people.length) {
    const has = (n) => photo.people.includes(n);
    if (f.mode === 'all' ? !f.people.every(has) : !f.people.some(has)) return false;
  }
  return true;
}

function filterIsEmpty(f) {
  return !f.people.length && !f.from && !f.to;
}

function describeFilter(f) {
  const parts = [];
  if (f.people.length) parts.push(f.people.join(f.mode === 'all' ? ' & ' : ' or '));
  if (f.from && f.to && f.from === f.to) parts.push('on ' + formatDay(f.from));
  else {
    if (f.from) parts.push('from ' + formatDay(f.from));
    if (f.to) parts.push('to ' + formatDay(f.to));
  }
  return parts.join(', ') || 'All photos';
}

const sortByDate = (list) => list.sort((a, b) => b.taken - a.taken);

// ---------- Adding photos ----------
async function makeThumb(file) {
  try {
    const bmp = await createImageBitmap(file);
    const size = 320;
    const scale = Math.min(1, size / Math.min(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close && bmp.close();
    return await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.8));
  } catch (e) {
    return file; // e.g. HEIC on a browser that cannot decode it
  }
}

async function addFiles(files) {
  const added = [];
  const existing = new Set(state.photos.map((p) => `${p.name}|${p.size}`));
  for (const file of files) {
    if (existing.has(`${file.name}|${file.size}`)) continue; // skip duplicates
    const exifDate = await readExifDate(file);
    added.push({
      id: uid(),
      name: file.name,
      size: file.size,
      type: file.type,
      taken: (exifDate || new Date(file.lastModified)).getTime(),
      dateSource: exifDate ? 'camera' : 'file',
      people: [],
      blob: file,
      thumb: await makeThumb(file),
    });
  }
  if (added.length) {
    await dbPut('photos', added);
    state.photos.push(...added);
  }
  render();
  return added.length;
}

// ---------- Rendering ----------
function thumbButton(photo, { selectable }) {
  const btn = document.createElement('button');
  btn.className = 'thumb' + (state.selected.has(photo.id) ? ' sel' : '');
  btn.dataset.id = photo.id;
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.alt = photo.name;
  img.src = blobUrl(photo.thumb);
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = photo.people.join(', ');
  btn.append(img, tag);
  if (selectable) {
    const check = document.createElement('span');
    check.className = 'check';
    btn.append(check);
  }
  return btn;
}

function renderGroupedGrid(container, photos, opts) {
  container.innerHTML = '';
  let currentKey = null;
  let grid = null;
  const counts = {};
  for (const p of photos) counts[dayKey(p.taken)] = (counts[dayKey(p.taken)] || 0) + 1;
  for (const p of photos) {
    const key = dayKey(p.taken);
    if (key !== currentKey) {
      currentKey = key;
      const head = document.createElement('div');
      head.className = 'day-head';
      head.innerHTML = `<span></span><span class="muted"></span>`;
      head.children[0].textContent = formatDay(key);
      head.children[1].textContent = `${counts[key]} photo${counts[key] > 1 ? 's' : ''}`;
      grid = document.createElement('div');
      grid.className = 'grid';
      container.append(head, grid);
    }
    grid.append(thumbButton(p, opts));
  }
}

function renderPeopleChips(container, names, isOn, onTap) {
  container.innerHTML = '';
  for (const name of names) {
    const chip = document.createElement('button');
    chip.className = 'chip' + (isOn(name) ? ' on' : '');
    chip.textContent = name;
    chip.onclick = () => onTap(name);
    container.append(chip);
  }
}

function filteredPhotos() {
  return sortByDate(state.photos.filter((p) => matches(p, state.filter)));
}

function renderPhotos() {
  const people = allPeople();
  $('people-list').innerHTML = people.map((n) => `<option value="${n.replace(/"/g, '&quot;')}">`).join('');

  const f = state.filter;
  f.people = f.people.filter((n) => people.includes(n));
  if (people.length) {
    renderPeopleChips($('filter-people'), people, (n) => f.people.includes(n), (n) => {
      f.people = f.people.includes(n) ? f.people.filter((x) => x !== n) : [...f.people, n];
      render();
    });
  } else {
    $('filter-people').innerHTML =
      '<span class="muted">No people yet — tap a photo (or use Select) to tag who is in it.</span>';
  }
  $('match-row').classList.toggle('hidden', f.people.length < 2);
  $('match-mode').value = f.mode;
  $('date-from').value = f.from;
  $('date-to').value = f.to;

  const list = filteredPhotos();
  $('save-collection').disabled = filterIsEmpty(f) || !list.length;
  $('photo-count').textContent = state.photos.length
    ? (filterIsEmpty(f) ? `${state.photos.length} photos` : `${list.length} of ${state.photos.length} photos`)
    : '';
  $('filter-card').classList.toggle('hidden', !state.photos.length);

  const container = $('photo-list');
  container.classList.toggle('selecting', state.selecting);
  if (!state.photos.length) {
    container.innerHTML = `<div class="empty"><div class="big">🖼️</div>
      <p><b>Add photos from your phone</b></p>
      <p>We read the date each photo was taken. Then tag the people in them and
      make collections like “Mom in December 2024”.</p></div>`;
  } else if (!list.length) {
    container.innerHTML = '<div class="empty">No photos match this person and date.</div>';
  } else {
    renderGroupedGrid(container, list, { selectable: true });
  }
  renderSelectBar();
}

function renderSelectBar() {
  $('select-bar').classList.toggle('hidden', !state.selecting);
  $('select-btn').textContent = state.selecting ? 'Cancel' : 'Select';
  const n = state.selected.size;
  $('sel-count').textContent = `${n} selected`;
  $('sel-tag').disabled = $('sel-delete').disabled = !n;
}

function renderCollections() {
  const listEl = $('collection-list');
  const detail = $('collection-detail');
  const open = state.collections.find((c) => c.id === state.openCollection);
  listEl.classList.toggle('hidden', !!open);
  detail.classList.toggle('hidden', !open);

  if (open) {
    const photos = sortByDate(state.photos.filter((p) => matches(p, open.filter)));
    $('coll-title').textContent = open.name;
    $('coll-desc').textContent = `${describeFilter(open.filter)} · ${photos.length} photo${photos.length === 1 ? '' : 's'}`;
    $('share-btn').disabled = $('download-btn').disabled = !photos.length;
    if (photos.length) renderGroupedGrid($('coll-photos'), photos, { selectable: false });
    else $('coll-photos').innerHTML = '<div class="empty">No photos match this collection right now.</div>';
    return;
  }

  if (!state.collections.length) {
    listEl.innerHTML = `<div class="empty"><div class="big">📚</div>
      <p><b>No collections yet</b></p>
      <p>On the Photos tab, pick a person and/or dates, then tap “Save as collection”.</p></div>`;
    return;
  }
  listEl.innerHTML = '';
  for (const c of [...state.collections].sort((a, b) => b.created - a.created)) {
    const photos = sortByDate(state.photos.filter((p) => matches(p, c.filter)));
    const card = document.createElement('button');
    card.className = 'card coll';
    const cover = photos[0] ? `<img alt="" src="${blobUrl(photos[0].thumb)}">` : '<div class="ph"></div>';
    card.innerHTML = `${cover}<div><h3></h3><div class="muted"></div></div>`;
    card.querySelector('h3').textContent = c.name;
    card.querySelector('.muted').textContent =
      `${describeFilter(c.filter)} · ${photos.length} photo${photos.length === 1 ? '' : 's'}`;
    card.onclick = () => { state.openCollection = c.id; render(); window.scrollTo(0, 0); };
    listEl.append(card);
  }
}

function render() {
  renderPhotos();
  renderCollections();
}

// ---------- Viewer ----------
function openViewer(list, id) {
  state.viewerList = list.map((p) => p.id);
  state.viewerIndex = Math.max(0, state.viewerList.indexOf(id));
  renderViewer();
  if (!$('viewer').open) $('viewer').showModal();
}

function currentViewerPhoto() {
  return state.photos.find((p) => p.id === state.viewerList[state.viewerIndex]);
}

function renderViewer() {
  const p = currentViewerPhoto();
  if (!p) return $('viewer').close();
  $('viewer-img').src = blobUrl(p.blob);
  $('viewer-date').value = toLocalInput(p.taken);
  $('viewer-source').textContent = { camera: 'from camera', file: 'file date (no camera date)', manual: 'edited' }[p.dateSource];
  const names = [...new Set([...p.people, ...allPeople()])].sort((a, b) => a.localeCompare(b));
  renderPeopleChips($('viewer-people'), names, (n) => p.people.includes(n), async (n) => {
    p.people = p.people.includes(n) ? p.people.filter((x) => x !== n) : [...p.people, n];
    await dbPut('photos', p);
    renderViewer();
  });
  $('viewer-prev').disabled = state.viewerIndex === 0;
  $('viewer-next').disabled = state.viewerIndex >= state.viewerList.length - 1;
}

async function addPersonInViewer() {
  const name = $('viewer-new-person').value.trim();
  const p = currentViewerPhoto();
  if (!name || !p) return;
  if (!p.people.includes(name)) p.people.push(name);
  $('viewer-new-person').value = '';
  await dbPut('photos', p);
  renderViewer();
}

// ---------- Multi-select tagging ----------
let taggerChoice = new Set();
function openTagger() {
  taggerChoice = new Set();
  renderTagger();
  $('tagger').showModal();
}
function renderTagger(extra = []) {
  const names = [...new Set([...allPeople(), ...taggerChoice, ...extra])].sort((a, b) => a.localeCompare(b));
  renderPeopleChips($('tagger-people'), names, (n) => taggerChoice.has(n), (n) => {
    taggerChoice.has(n) ? taggerChoice.delete(n) : taggerChoice.add(n);
    renderTagger();
  });
  if (!names.length) $('tagger-people').innerHTML = '<span class="muted">Type a name below to add the first person.</span>';
}

// ---------- Collection actions ----------
function collectionPhotos() {
  const c = state.collections.find((x) => x.id === state.openCollection);
  return c ? sortByDate(state.photos.filter((p) => matches(p, c.filter))) : [];
}

function asFile(p) {
  return p.blob instanceof File ? p.blob : new File([p.blob], p.name, { type: p.type || p.blob.type });
}

// ---------- Events ----------
function wire() {
  document.querySelectorAll('.tab').forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
      $('tab-photos').classList.toggle('hidden', t.dataset.tab !== 'photos');
      $('tab-collections').classList.toggle('hidden', t.dataset.tab !== 'collections');
      if (t.dataset.tab !== 'photos') { state.selecting = false; state.selected.clear(); }
      render();
    };
  });

  $('file-input').onchange = async (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    if (!files.length) return;
    $('photo-count').textContent = `Reading ${files.length} photo${files.length > 1 ? 's' : ''}…`;
    await addFiles(files);
  };

  $('select-btn').onclick = () => {
    state.selecting = !state.selecting;
    state.selected.clear();
    render();
  };
  $('sel-done').onclick = $('select-btn').onclick;
  $('sel-all').onclick = () => {
    const ids = filteredPhotos().map((p) => p.id);
    const allOn = ids.every((id) => state.selected.has(id));
    ids.forEach((id) => (allOn ? state.selected.delete(id) : state.selected.add(id)));
    render();
  };
  $('sel-tag').onclick = openTagger;
  $('sel-delete').onclick = async () => {
    const n = state.selected.size;
    if (!confirm(`Remove ${n} photo${n > 1 ? 's' : ''} from this app? (Photos on your phone are not deleted.)`)) return;
    await dbDelete('photos', [...state.selected]);
    state.photos = state.photos.filter((p) => !state.selected.has(p.id));
    state.selected.clear();
    render();
  };

  $('photo-list').onclick = (e) => {
    const btn = e.target.closest('.thumb');
    if (!btn) return;
    const id = btn.dataset.id;
    if (state.selecting) {
      state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id);
      btn.classList.toggle('sel', state.selected.has(id));
      renderSelectBar();
    } else {
      openViewer(filteredPhotos(), id);
    }
  };
  $('coll-photos').onclick = (e) => {
    const btn = e.target.closest('.thumb');
    if (btn) openViewer(collectionPhotos(), btn.dataset.id);
  };

  // Filters
  $('match-mode').onchange = (e) => { state.filter.mode = e.target.value; render(); };
  $('date-from').onchange = (e) => {
    state.filter.from = e.target.value;
    if (state.filter.to && state.filter.from > state.filter.to) state.filter.to = state.filter.from;
    render();
  };
  $('date-to').onchange = (e) => {
    state.filter.to = e.target.value;
    if (state.filter.from && state.filter.to < state.filter.from) state.filter.from = state.filter.to;
    render();
  };
  $('clear-filter').onclick = () => { state.filter = { people: [], mode: 'any', from: '', to: '' }; render(); };
  $('save-collection').onclick = async () => {
    const f = state.filter;
    const name = prompt('Name this collection:', describeFilter(f));
    if (!name || !name.trim()) return;
    const c = { id: uid(), name: name.trim(), filter: { ...f, people: [...f.people] }, created: Date.now() };
    await dbPut('collections', c);
    state.collections.push(c);
    state.openCollection = c.id;
    document.querySelector('.tab[data-tab="collections"]').click();
  };

  // Viewer
  $('viewer-close').onclick = () => $('viewer').close();
  $('viewer').addEventListener('close', render);
  $('viewer-prev').onclick = () => { state.viewerIndex--; renderViewer(); };
  $('viewer-next').onclick = () => { state.viewerIndex++; renderViewer(); };
  $('viewer-add-person').onclick = addPersonInViewer;
  $('viewer-new-person').onkeydown = (e) => { if (e.key === 'Enter') addPersonInViewer(); };
  $('viewer-date').onchange = async (e) => {
    const p = currentViewerPhoto();
    const ms = new Date(e.target.value).getTime();
    if (!p || Number.isNaN(ms)) return;
    p.taken = ms;
    p.dateSource = 'manual';
    await dbPut('photos', p);
    renderViewer();
  };

  // Tagger
  const addTaggerName = () => {
    const name = $('tagger-new').value.trim();
    if (!name) return;
    taggerChoice.add(name);
    $('tagger-new').value = '';
    renderTagger();
  };
  $('tagger-add').onclick = addTaggerName;
  $('tagger-new').onkeydown = (e) => { if (e.key === 'Enter') addTaggerName(); };
  $('tagger-cancel').onclick = () => $('tagger').close();
  $('tagger-apply').onclick = async () => {
    const pending = $('tagger-new').value.trim();
    if (pending) taggerChoice.add(pending);
    const changed = state.photos.filter((p) => state.selected.has(p.id));
    for (const p of changed) p.people = [...new Set([...p.people, ...taggerChoice])];
    await dbPut('photos', changed);
    $('tagger').close();
    state.selecting = false;
    state.selected.clear();
    render();
  };

  // Collection detail
  $('back-btn').onclick = () => { state.openCollection = null; render(); };
  $('delete-collection').onclick = async () => {
    const c = state.collections.find((x) => x.id === state.openCollection);
    if (!c || !confirm(`Delete collection “${c.name}”? Photos are kept.`)) return;
    await dbDelete('collections', c.id);
    state.collections = state.collections.filter((x) => x.id !== c.id);
    state.openCollection = null;
    render();
  };
  $('share-btn').onclick = async () => {
    const files = collectionPhotos().map(asFile);
    const c = state.collections.find((x) => x.id === state.openCollection);
    if (navigator.canShare && navigator.canShare({ files })) {
      try { await navigator.share({ files, title: c.name }); } catch (e) { /* cancelled */ }
    } else {
      alert('Sharing files is not supported in this browser. Use Download instead.');
    }
  };
  $('download-btn').onclick = async () => {
    for (const f of collectionPhotos().map(asFile)) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(f);
      a.download = f.name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      await new Promise((r) => setTimeout(r, 300));
    }
  };
}

// ---------- Start ----------
(async function init() {
  wire();
  try {
    [state.photos, state.collections] = await Promise.all([dbAll('photos'), dbAll('collections')]);
  } catch (e) {
    console.warn('Storage unavailable; photos will not be saved between visits.', e);
  }
  render();
})();
