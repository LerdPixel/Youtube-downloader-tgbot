import { partitionDownloads, downloadActivities } from './downloads.mjs';

const $ = (id) => document.getElementById(id);
const state = { info: null, health: null, analysis: null, analysisId: 0, jobs: [], jobsKey: '', toastTimer: null, estimateTimer: null, estimateRequest: null, estimateId: 0, playlistSelected: new Set(), playlistVisible: 100, downloading: false, downloadsScope: null, modalLimits: {active:100, complete:100, failed:100}, tooltipRow: null };
const modes = { video: 'Видео со звуком', audio: 'Только аудио', silent: 'Видео без звука' };
const mode = () => document.querySelector('input[name="mode"]:checked').value;
const active = (job) => ['queued', 'downloading', 'processing'].includes(job.status);
const defaults = $('resolution').innerHTML;
const savedFields = ['container','resolution','video-codec','fps','audio-format','audio-quality','cover','metadata','chapters','subtitle-format','description','info-json','fragments','rate-limit'];

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  svg.setAttribute('aria-hidden', 'true');
  return svg;
}

function bytes(value) {
  if (value === null || value === undefined) return '—';
  if (value < 1024) return `${value} Б`;
  const units = ['КиБ', 'МиБ', 'ГиБ', 'ТиБ'];
  let size = value / 1024, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[index]}`;
}

function duration(value) {
  if (!Number.isFinite(value)) return '—';
  const number = Math.max(0, Math.floor(value));
  return number >= 3600 ? `${Math.floor(number/3600)}:${String(Math.floor(number/60)%60).padStart(2,'0')}:${String(number%60).padStart(2,'0')}`
    : `${Math.floor(number/60)}:${String(number%60).padStart(2,'0')}`;
}

function errorAt(id, message = '') {
  $(id).textContent = message;
  $(id).hidden = !message;
}

function toast(message) {
  clearTimeout(state.toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
}

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } }); }
  catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Нет связи с локальным сервером. Убедитесь, что web.py продолжает работать.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос.');
  return data;
}

function source() { return { url: $('url').value.trim(), cookies: $('cookies').value.trim(), cookies_browser: $('cookies-browser').value, proxy: $('proxy').value.trim() }; }

function settings() {
  return { ...source(), mode: mode(), container: $('container').value, resolution: Number($('resolution').value),
    fps: Number($('fps').value), video_codec: $('video-codec').value, audio_format: $('audio-format').value,
    audio_quality: $('audio-quality').value, audio_language: mode() === 'silent' ? '' : $('audio-language').value,
    thumbnail: $('cover').value, metadata: $('metadata').checked, chapters: $('chapters').checked,
    description: $('description').checked, info_json: $('info-json').checked,
    subtitles: [...document.querySelectorAll('#subtitle-languages input:checked')].map(input => input.value),
    auto_subtitles: $('auto-subtitles').checked, subtitle_format: $('subtitle-format').value,
    embed_subtitles: $('embed-subtitles').checked, start: $('start').value.trim(), end: $('end').value.trim(),
    precise_cuts: $('precise-cuts').checked, rate_limit: Number($('rate-limit').value), fragments: Number($('fragments').value),
    format_selector: $('format-selector').value.trim(), filename: $('filename').value.trim() };
}

function updateControls() {
  const isAudio = mode() === 'audio';
  $('video-settings').hidden = isAudio;
  $('audio-settings').hidden = !isAudio;
  $('audio-language-label').hidden = mode() === 'silent';
  $('audio-quality').disabled = ['flac', 'wav', 'alac', 'best'].includes($('audio-format').value);
  const canEmbed = isAudio ? ['mp3','m4a','flac','opus','vorbis','alac'].includes($('audio-format').value)
    : ['mp4','mkv'].includes($('container').value);
  for (const option of $('cover').options) option.disabled = ['embed','both'].includes(option.value) && !canEmbed;
  if (!canEmbed && ['embed','both'].includes($('cover').value)) $('cover').value = $('cover').value === 'both' ? 'file' : 'none';
  const noSubtitles = !document.querySelector('#subtitle-languages input:checked');
  $('embed-subtitles').disabled = isAudio || noSubtitles;
  if (isAudio || noSubtitles) $('embed-subtitles').checked = false;
  if ($('container').value === 'webm' && !isAudio && $('embed-subtitles').checked) $('subtitle-format').value = 'vtt';
  const isWebm = $('container').value === 'webm' && !isAudio;
  $('video-codec').querySelector('option[value="h264"]').disabled = isWebm;
  if (isWebm && $('video-codec').value === 'h264') $('video-codec').value = 'auto';
  const summary = $('download-summary');
  summary.replaceChildren(node('strong', '', modes[mode()] + (state.info?.kind === 'playlist' ? ' · плейлист' : '')));
  const custom = $('format-selector').value.trim();
  let detail = isAudio ? `${$('audio-format').value.toUpperCase()} · ${$('audio-quality').disabled || $('audio-quality').value === '0' ? 'лучшее качество' : $('audio-quality').value + ' кбит/с'}`
    : `${$('container').value === 'auto' ? 'Авто' : $('container').value.toUpperCase()} · ${$('resolution').value === '0' ? 'максимум' : 'до ' + $('resolution').value + 'p'}`;
  if (custom) detail = `Потоки: ${custom}`;
  if ($('start').value || $('end').value) detail += ' · фрагмент';
  if (state.info?.kind === 'playlist') detail += ` · ${state.playlistSelected.size} видео`;
  summary.append(node('span', '', detail));
  $('compatibility-note').textContent = custom ? 'Задан ручной выбор потоков. Настройки разрешения, кодека и языка не применяются. В режиме без звука аудио будет удалено.'
    : isAudio ? 'Аудио скачивается отдельно от картинки. Обложку можно сохранить или встроить ниже.'
    : mode() === 'silent' ? 'Сохраняется только видеоряд. Звуковой дорожки в итоговом файле не будет.'
    : 'Потоки выбираются без перекодирования видео. Если сочетание недоступно, загрузчик сообщит об этом.';
  $('subtitles-count').textContent = `${document.querySelectorAll('#subtitle-languages input:checked').length} выбрано`;
  updatePlaylistSelection();
  scheduleEstimate();
  try {
    const preferences = {mode: mode()};
    for (const id of savedFields) preferences[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
    localStorage.setItem('youtube-local-settings', JSON.stringify(preferences));
  } catch { /* Preferences are optional in private browser windows. */ }
}

function updatePlaylistSelection() {
  const playlist = state.info?.kind === 'playlist';
  const count = state.playlistSelected.size;
  const all = playlist && count === state.info.available_count;
  $('playlist-select-all').checked = Boolean(all && count);
  $('playlist-select-all').indeterminate = Boolean(playlist && count && !all);
  $('playlist-select-all').disabled = !playlist || !state.info.available_count;
  $('playlist-selection').textContent = playlist ? `Выбрано ${count} из ${state.info.entry_count}` : '';
  $('download-button').replaceChildren(icon('download'),document.createTextNode(playlist ? !count ? 'Выберите видео' : all ? `Скачать весь плейлист (${count})` : `Скачать выбранные (${count})` : 'Скачать'));
  $('download-button').disabled = state.downloading || (playlist && !count);
}

function renderPlaylist() {
  const playlist = state.info?.kind === 'playlist';
  $('playlist-panel').hidden = !playlist;
  $('playlist-entries').replaceChildren();
  if (!playlist) return;
  for (const entry of state.info.entries.slice(0,state.playlistVisible)) {
    const row = node('div',`playlist-entry${entry.available ? '' : ' unavailable'}`);
    const label = node('label','check');
    const check = node('input');
    check.type = 'checkbox'; check.value = entry.index; check.checked = state.playlistSelected.has(entry.index); check.disabled = !entry.available;
    check.addEventListener('change',() => { if (check.checked) state.playlistSelected.add(entry.index); else state.playlistSelected.delete(entry.index); updateControls(); });
    label.append(check,node('span','',`${entry.index}. ${entry.title}`));
    row.append(label,node('span','small-note',entry.available ? duration(entry.duration) : entry.reason));
    if (entry.available) {
      const analyze = node('button','text-button','Разобрать видео'); analyze.type = 'button';
      analyze.addEventListener('click',() => { $('url').value = entry.url; invalidateAnalysis(); $('analyze-form').requestSubmit(); });
      row.append(analyze);
    }
    $('playlist-entries').append(row);
  }
  $('playlist-more').hidden = state.playlistVisible >= state.info.entries.length;
  updatePlaylistSelection();
}

$('playlist-select-all').addEventListener('change',() => {
  state.playlistSelected = new Set($('playlist-select-all').checked ? state.info.entries.filter(entry => entry.available).map(entry => entry.index) : []);
  for (const check of document.querySelectorAll('#playlist-entries input')) check.checked = state.playlistSelected.has(Number(check.value));
  updateControls();
});
$('playlist-more').addEventListener('click',() => { state.playlistVisible += 100; renderPlaylist(); });

function scheduleEstimate() {
  clearTimeout(state.estimateTimer);
  state.estimateRequest?.abort();
  const id = ++state.estimateId;
  $('estimated-streams').textContent = '';
  $('estimated-size').removeAttribute('title');
  if (!state.info?.analysis_id) {
    $('estimated-size').textContent = '—';
    $('estimated-size-note').textContent = 'Разберите ссылку, чтобы рассчитать размер видео или аудио.';
    return;
  }
  if (state.info.kind === 'playlist') {
    $('estimated-size').textContent = 'После скачивания';
    $('estimated-size-note').textContent = 'Для плейлиста пока известен список видео. Общий объём будет суммироваться по мере скачивания; размер отдельного ролика можно узнать через «Разобрать видео».';
    return;
  }
  $('estimated-size').textContent = 'Считаем…';
  $('estimated-size-note').textContent = 'Выбираем потоки для текущих настроек.';
  state.estimateTimer = setTimeout(async () => {
    state.estimateRequest = new AbortController();
    try {
      const result = await api('/api/estimate', {method:'POST', body:JSON.stringify({analysis_id:state.info.analysis_id, settings:settings()}), signal:state.estimateRequest.signal});
      if (id !== state.estimateId) return;
      $('estimated-size').textContent = result.size_bytes === null ? 'Неизвестен' : `${result.approximate ? '≈ ' : ''}${bytes(result.size_bytes)}`;
      if (result.size_bytes !== null) $('estimated-size').title = `${result.size_bytes.toLocaleString('ru-RU')} байт`;
      $('estimated-size-note').textContent = result.note;
      $('estimated-streams').textContent = result.format_ids.length ? `Потоки ${result.format_ids.join(' + ')}` : '';
    } catch (error) {
      if (error.name !== 'AbortError' && id === state.estimateId) {
        $('estimated-size').textContent = '—';
        $('estimated-size-note').textContent = error.message;
      }
    }
  }, 250);
}

function renderSubtitles() {
  const selected = new Set([...document.querySelectorAll('#subtitle-languages input:checked')].map(input => input.value));
  const container = $('subtitle-languages');
  container.replaceChildren();
  if (!state.info) {
    container.append(node('p', 'field-note', 'Сначала разберите ссылку, чтобы увидеть доступные языки.'));
    return;
  }
  if (state.info.kind === 'playlist') {
    container.append(node('p','field-note','Субтитры и языки доступны при разборе отдельного видео из списка.'));
    return;
  }
  const languages = state.info.subtitles.filter(lang => lang.manual || $('auto-subtitles').checked);
  languages.sort((a,b) => Number(!a.code.startsWith('ru') && !a.code.startsWith('en')) - Number(!b.code.startsWith('ru') && !b.code.startsWith('en')) || a.code.localeCompare(b.code));
  if (!languages.length) container.append(node('p', 'field-note', 'Нет обычных субтитров. Попробуйте включить автоматически созданные.'));
  for (const language of languages) {
    const label = node('label', 'check');
    const input = node('input');
    input.type = 'checkbox'; input.value = language.code; input.checked = selected.has(language.code);
    label.append(input, node('span', '', `${language.name} · ${language.code}${!language.manual ? ' (авто)' : ''}`));
    container.append(label);
  }
}

function renderFormats() {
  const table = $('formats-table');
  table.replaceChildren();
  const formats = state.info?.formats || [];
  $('formats-count').textContent = formats.length ? `${formats.length} потоков` : 'после анализа ссылки';
  for (const format of [...formats].reverse()) {
    const row = node('tr');
    const video = format.vcodec && format.vcodec !== 'none';
    const audio = format.acodec && format.acodec !== 'none';
    const info = node('td', '', `${(format.ext || '').toUpperCase()} · ${video ? (format.height ? format.height + 'p' : 'видео') : 'аудио'}`);
    info.append(node('small', '', [format.fps ? format.fps + ' fps' : '', format.language || '', video && audio ? 'со звуком' : video ? 'без звука' : '', format.abr ? Math.round(format.abr) + ' кбит/с' : ''].filter(Boolean).join(' · ')));
    const size = format.filesize || format.filesize_approx;
    const action = node('td');
    const button = node('button', '', 'Выбрать');
    button.type = 'button';
    button.addEventListener('click', () => {
      if (!video) document.querySelector('input[name="mode"][value="audio"]').checked = true;
      else if (mode() === 'audio') document.querySelector('input[name="mode"][value="video"]').checked = true;
      const selector = video && !audio && mode() !== 'silent' ? `${format.format_id}+ba` : format.format_id;
      $('format-selector').value = selector;
      $('format-selector').closest('details').open = true;
      updateControls();
      toast(`Выбран поток ${selector}. При необходимости измените контейнер.`);
    });
    action.append(button);
    row.append(node('td','',format.format_id), info, node('td','', [video ? format.vcodec : '', audio ? format.acodec : ''].filter(Boolean).join(' / ')), node('td','', size ? `${!format.filesize ? '≈ ' : ''}${bytes(size)}` : '—'), action);
    table.append(row);
  }
  if (!formats.length) { const row = node('tr'); const cell = node('td','','Сначала разберите ссылку.'); cell.colSpan = 5; row.append(cell); table.append(row); }
}

function invalidateAnalysis() {
  if (state.analysis) state.analysis.abort();
  state.analysisId++;
  state.info = null;
  state.playlistSelected.clear();
  state.playlistVisible = 100;
  renderPlaylist();
  $('preview').hidden = true;
  $('analyze-button').disabled = false;
  $('analyze-button').replaceChildren(document.createTextNode('Разобрать ссылку'), icon('arrow'));
  $('analysis-note').textContent = 'Узнайте доступные разрешения, аудиодорожки и субтитры перед скачиванием.';
  $('resolution').innerHTML = defaults;
  $('audio-language').replaceChildren(new Option('Лучшая доступная дорожка',''));
  $('chapter-shortcuts').replaceChildren();
  $('format-selector').value = '';
  renderSubtitles(); renderFormats(); updateControls();
}

$('analyze-form').addEventListener('submit', async event => {
  event.preventDefault();
  errorAt('source-error');
  state.analysis?.abort();
  const id = ++state.analysisId;
  state.analysis = new AbortController();
  $('analyze-button').disabled = true;
  $('analyze-button').textContent = 'Анализируем…';
  $('analysis-note').textContent = 'Получаем данные с YouTube. Обычно это занимает несколько секунд; при медленном соединении — до двух минут.';
  try {
    const info = await api('/api/analyze', {method:'POST', body: JSON.stringify(source()), signal: state.analysis.signal});
    if (id !== state.analysisId) return;
    state.info = info;
    state.playlistSelected = new Set((info.entries || []).filter(entry => entry.available).map(entry => entry.index));
    state.playlistVisible = 100;
    renderPlaylist();
    $('preview').hidden = false;
    $('thumbnail').src = info.thumbnail;
    $('thumbnail').hidden = !info.thumbnail;
    $('video-title').textContent = info.title;
    const playlist = info.kind === 'playlist';
    $('video-meta').textContent = `${info.channel || 'YouTube'} · ${playlist ? info.entry_count + ' видео · ' : ''}${playlist && info.unknown_durations ? 'не менее ' : ''}${duration(info.duration)}`;
    $('video-tags').replaceChildren(...(playlist ? [`Доступно ${info.available_count} из ${info.entry_count}`, 'Плейлист'] : [info.resolutions.length ? `до ${info.resolutions[0]}p` : null, `${info.formats.length} потоков`, info.subtitles.length ? 'Есть субтитры' : null]).filter(Boolean).map(text => node('span','tag',text)));
    const wanted = $('resolution').value;
    if (!playlist) {
      $('resolution').replaceChildren(new Option('Максимальное доступное','0'));
      for (const height of info.resolutions) $('resolution').append(new Option(`До ${height}p`,String(height)));
    }
    if ([...$('resolution').options].some(option => option.value === wanted)) $('resolution').value = wanted;
    $('audio-language').replaceChildren(new Option('Лучшая доступная дорожка',''));
    for (const language of info.audio_languages) $('audio-language').append(new Option(language,language));
    renderSubtitles(); renderFormats();
    $('chapter-shortcuts').replaceChildren();
    for (const chapter of info.chapters) {
      const button = node('button','',`${duration(chapter.start_time)} · ${chapter.title}`);
      button.type = 'button';
      button.addEventListener('click', () => { $('start').value = String(chapter.start_time); $('end').value = chapter.end_time ? String(chapter.end_time) : ''; updateControls(); });
      $('chapter-shortcuts').append(button);
    }
    $('analysis-note').textContent = playlist ? 'Плейлист разобран. Все доступные видео выбраны; можно изменить выбор и общие настройки.' : 'Ссылка разобрана. Настройки ниже обновлены по доступным потокам.';
    updateControls();
  } catch (error) {
    if (error.name !== 'AbortError' && id === state.analysisId) { errorAt('source-error',error.message); $('analysis-note').textContent = 'Не удалось разобрать ссылку. Проверьте подключение, cookies и прокси.'; }
  } finally {
    if (id === state.analysisId) { $('analyze-button').disabled = false; $('analyze-button').replaceChildren(document.createTextNode('Разобрать ссылку'), icon('arrow')); state.analysis = null; }
  }
});

$('download-form').addEventListener('submit', async event => {
  event.preventDefault();
  errorAt('download-error');
  if (!$('url').reportValidity()) { $('url').focus(); return; }
  if (!$('url').value.trim()) { errorAt('download-error','Сначала вставьте ссылку на видео.'); $('url').focus(); return; }
  state.downloading = true;
  updatePlaylistSelection();
  let openedBatch = null;
  try {
    if (state.info?.kind === 'playlist') {
      const result = await api('/api/playlists/download',{method:'POST',body:JSON.stringify({analysis_id:state.info.analysis_id, settings:settings(), entry_indices:[...state.playlistSelected]})});
      openedBatch = result.batch_id;
      toast(`В очередь добавлено видео: ${result.count}`);
    } else {
      await api('/api/jobs', {method:'POST', body:JSON.stringify(settings())});
      toast('Загрузка добавлена в очередь');
    }
    await pollJobs();
    if (openedBatch) openDownloads(openedBatch);
  } catch (error) { errorAt('download-error',error.message); }
  finally { state.downloading = false; updatePlaylistSelection(); }
});

function renderJobs() {
  const activities = downloadActivities(state.jobs);
  $('jobs-count').textContent = activities.length;
  $('nav-count').textContent = state.jobs.filter(active).length || activities.length;
  const files = state.jobs.filter(job => job.status === 'complete').flatMap(job => job.files || []);
  const totalBytes = files.reduce((sum,file) => sum + (Number.isFinite(file.size) ? file.size : 0),0);
  $('downloaded-size').textContent = bytes(totalBytes);
  $('downloaded-size').title = `${totalBytes.toLocaleString('ru-RU')} байт`;
  $('downloaded-files').textContent = files.length;
  const list = $('jobs-list');
  list.replaceChildren();
  if (!activities.length) list.append(node('p','recent-empty','Здесь появятся три последние загрузки.'));
  for (const activity of activities.slice(0,3)) {
    const card = node('button','recent-download'); card.type = 'button';
    card.setAttribute('aria-haspopup','dialog');
    card.title = activity.title;
    const mark = node('span','recent-mark'); mark.append(icon(activity.batchId ? 'folder' : activity.jobs[0].mode === 'audio' ? 'music' : 'video'));
    const text = node('span','recent-copy');
    text.append(node('strong','',activity.title));
    const columns = partitionDownloads(activity.jobs);
    text.append(node('span','',activity.batchId ? `Плейлист · ${activity.jobs.length} видео · готово ${columns.complete.length}`
      : activity.jobs[0].phase));
    card.append(mark,text,node('span','recent-arrow','↗'));
    card.addEventListener('click',() => openDownloads(activity.batchId));
    list.append(card);
  }
  renderDownloadsDialog();
}

function openDownloads(batchId = null) {
  state.downloadsScope = batchId;
  state.modalLimits = {active:100,complete:100,failed:100};
  hideDownloadTooltip();
  if (!$('downloads-dialog').open) $('downloads-dialog').showModal();
  document.body.classList.add('dialog-open');
  renderDownloadsDialog();
}

function hideDownloadTooltip() {
  state.tooltipRow?.removeAttribute('aria-describedby');
  state.tooltipRow = null;
  $('download-tooltip').hidden = true;
}

function showDownloadTooltip(row) {
  const job = state.jobs.find(item => item.id === row.dataset.jobId);
  if (!job) return;
  if (state.tooltipRow !== row) hideDownloadTooltip();
  state.tooltipRow = row;
  row.setAttribute('aria-describedby','download-tooltip');
  const lines = [job.title,job.preset,job.playlist_title ? `${job.playlist_index}. ${job.playlist_title}` : '',job.phase];
  if (active(job)) lines.push(`${bytes(job.downloaded)}${job.total ? ' / ' + bytes(job.total) : ''}${job.speed ? ' · ' + bytes(job.speed) + '/с' : ''}${Number.isFinite(job.eta) ? ' · осталось ' + duration(job.eta) : ''}`);
  for (const file of (job.files || []).slice(0,5)) lines.push(`${file.name} · ${bytes(file.size)}`);
  if (job.files?.length > 5) lines.push(`Ещё файлов: ${job.files.length-5}`);
  if (job.saved_dir) lines.push(`Сохранено: ${job.saved_dir}`);
  if (job.error) lines.push(job.error.slice(0,1500));
  if (job.storage_error) lines.push(job.storage_error);
  if (job.warnings?.length) lines.push(job.warnings.join('\n').slice(0,1000));
  const tooltip = $('download-tooltip');
  tooltip.textContent = lines.filter(Boolean).join('\n');
  tooltip.hidden = false;
  const rect = row.getBoundingClientRect(), tip = tooltip.getBoundingClientRect();
  tooltip.style.left = `${Math.max(12,Math.min(rect.left,window.innerWidth-tip.width-12))}px`;
  tooltip.style.top = `${Math.max(12,rect.bottom+tip.height+10 > window.innerHeight ? rect.top-tip.height-8 : rect.bottom+8)}px`;
}

function createDownloadRow(job) {
  const row = node('div','download-row'); row.tabIndex = 0; row.dataset.jobId = job.id;
  row.append(node('span','download-row-title'),node('span','download-row-status'),node('span','download-row-actions'));
  row.addEventListener('pointerenter',() => showDownloadTooltip(row));
  row.addEventListener('pointerleave',hideDownloadTooltip);
  row.addEventListener('focusin',() => showDownloadTooltip(row));
  row.addEventListener('focusout',event => { if (!row.contains(event.relatedTarget)) hideDownloadTooltip(); });
  return row;
}

function updateDownloadRow(row,job) {
  const title = `${state.downloadsScope !== null && job.playlist_index ? job.playlist_index + '. ' : ''}${job.title}`;
  if (row.querySelector('.download-row-title').textContent !== title) row.querySelector('.download-row-title').textContent = title;
  row.querySelector('.download-row-status').textContent = job.status === 'complete' ? '✓' : active(job)
    ? job.status === 'queued' ? '…' : Number.isFinite(job.percent) ? `${job.percent}%` : '⋯'
    : job.status === 'cancelled' || job.status === 'interrupted' ? '×' : '!';
  const action = active(job) ? 'cancel' : job.status === 'complete' && job.files?.length ? 'file' : 'retry';
  const actions = row.querySelector('.download-row-actions');
  if (row.dataset.action !== action) {
    row.dataset.action = action;
    actions.replaceChildren();
    if (action === 'file') {
      const link = node('a','row-action','↓');
      link.href = job.files.length > 1 ? `/api/jobs/${job.id}/bundle` : `/api/jobs/${job.id}/files/${encodeURIComponent(job.files[0].name)}`;
      link.download = '';
      link.title = job.files.length > 1 ? 'Скачать копию всех файлов в ZIP' : 'Скачать дополнительную копию';
      link.setAttribute('aria-label',`Скачать копию: ${job.title}`);
      actions.append(link);
    } else {
      const button = node('button','row-action',action === 'cancel' ? '×' : '↗'); button.type = 'button';
      button.setAttribute('aria-label',`${action === 'cancel' ? 'Отменить' : 'Вставить ссылку'}: ${job.title}`);
      button.addEventListener('click',async () => {
        if (action === 'retry') {
          $('downloads-dialog').close();
          $('url').value = job.url; invalidateAnalysis(); $('url').focus();
          $('url').scrollIntoView({block:'center'});
          toast('Ссылка вставлена. Выберите настройки и скачайте снова.');
          return;
        }
        button.disabled = true;
        try { await api(`/api/jobs/${job.id}/cancel`,{method:'POST',body:'{}'}); await pollJobs(); }
        catch(error) { toast(error.message); button.disabled = false; }
      });
      actions.append(button);
    }
  }
  const cancel = action === 'cancel' && actions.querySelector('button');
  if (cancel) cancel.disabled = job.phase === 'Сохраняем в Загрузки';
}

function renderDownloadsDialog() {
  if (!$('downloads-dialog').open) return;
  const scope = state.downloadsScope;
  const columns = partitionDownloads(state.jobs,scope);
  const jobs = Object.values(columns).flat();
  $('downloads-dialog-kind').textContent = scope === null ? 'ИСТОРИЯ ЗАГРУЗОК' : 'ЗАГРУЗКА ПЛЕЙЛИСТА';
  $('downloads-dialog-title').textContent = scope === null ? 'Все загрузки' : jobs[0]?.playlist_title || 'Плейлист';
  const savedBytes = columns.complete.flatMap(job => job.files || []).reduce((sum,file) => sum+(Number.isFinite(file.size) ? file.size : 0),0);
  $('downloads-dialog-summary').textContent = `${jobs.length} загрузок · сохранено ${bytes(savedBytes)}`;
  $('cancel-download-batch').hidden = scope === null || !columns.active.length;
  const focusedId = document.activeElement?.closest('.download-row')?.dataset.jobId;
  for (const [key,items] of Object.entries(columns)) {
    const list = $(`column-${key}-list`), shown = items.slice(0,state.modalLimits[key]);
    $(`column-${key}-count`).textContent = items.length;
    $(`column-${key}-empty`).hidden = items.length !== 0;
    $(`column-${key}-more`).hidden = shown.length === items.length;
    const existing = new Map([...list.children].map(row => [row.dataset.jobId,row]));
    const keep = new Set(shown.map(job => job.id));
    for (const row of [...list.children]) if (!keep.has(row.dataset.jobId)) row.remove();
    let cursor = list.firstElementChild;
    for (const job of shown) {
      const row = existing.get(job.id) || createDownloadRow(job);
      updateDownloadRow(row,job);
      if (row !== cursor) list.insertBefore(row,cursor);
      cursor = row.nextElementSibling;
    }
  }
  if (focusedId && !document.activeElement?.closest('.download-row')) {
    const target = [...$('downloads-dialog').querySelectorAll('.download-row')].find(row => row.dataset.jobId === focusedId);
    target?.focus({preventScroll:true});
  }
  if (state.tooltipRow?.isConnected) showDownloadTooltip(state.tooltipRow);
  else hideDownloadTooltip();
}

$('open-downloads').addEventListener('click',() => openDownloads());
$('open-download-history').addEventListener('click',() => openDownloads());
$('downloads').addEventListener('click',event => { if (!event.target.closest('button,a')) openDownloads(); });
const historyNavigation = document.querySelector('a[href="#downloads"]');
historyNavigation.addEventListener('click',event => { event.preventDefault(); openDownloads(); });
$('close-downloads').addEventListener('click',() => $('downloads-dialog').close());
$('downloads-dialog').addEventListener('close',() => { hideDownloadTooltip(); document.body.classList.remove('dialog-open'); });
$('downloads-dialog').addEventListener('cancel',hideDownloadTooltip);
$('downloads-dialog').addEventListener('keydown',event => { if (event.key === 'Escape' && !$('download-tooltip').hidden) { event.preventDefault(); hideDownloadTooltip(); } });
$('downloads-dialog').addEventListener('click',event => {
  if (event.target !== $('downloads-dialog')) return;
  const rect = $('downloads-dialog').getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('downloads-dialog').close();
});
for (const key of ['active','complete','failed']) {
  $(`column-${key}-more`).addEventListener('click',() => { state.modalLimits[key] += 100; renderDownloadsDialog(); });
  $(`column-${key}-list`).addEventListener('scroll',hideDownloadTooltip,{passive:true});
}
$('cancel-download-batch').addEventListener('click',async () => {
  const batchId = state.downloadsScope;
  if (batchId === null) return;
  $('cancel-download-batch').disabled = true;
  try { await api(`/api/batches/${batchId}/cancel`,{method:'POST',body:'{}'}); await pollJobs(); }
  catch(error) { toast(error.message); }
  finally { $('cancel-download-batch').disabled = false; }
});
window.addEventListener('resize',hideDownloadTooltip);

let polling = false;
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    const jobs = await api('/api/jobs');
    errorAt('jobs-error');
    const key = JSON.stringify(jobs);
    if (key !== state.jobsKey) { state.jobs = jobs; state.jobsKey = key; renderJobs(); }
  } catch (error) { errorAt('jobs-error',error.message); }
  finally { polling = false; }
}

for (const id of ['url','cookies','proxy']) $(id).addEventListener('input', () => { if (state.info || state.analysis) invalidateAnalysis(); errorAt('source-error'); });
$('cookies-browser').addEventListener('change', () => {
  if ($('cookies-browser').value) $('cookies').value = '';
  $('cookies').disabled = !!$('cookies-browser').value;
  if (state.info || state.analysis) invalidateAnalysis();
  errorAt('source-error');
});
$('auto-subtitles').addEventListener('change', () => { renderSubtitles(); updateControls(); });
$('download-form').addEventListener('change',updateControls);
for (const id of ['start','end','format-selector']) $(id).addEventListener('input',updateControls);
$('reset-button').addEventListener('click', () => {
  $('download-form').reset();
  $('resolution').value = [...$('resolution').options].some(option => option.value === '1080') ? '1080' : '0';
  renderSubtitles();
  for (const input of document.querySelectorAll('#subtitle-languages input')) input.checked = false;
  errorAt('download-error'); updateControls(); toast('Настройки сброшены');
});
for (const button of document.querySelectorAll('[data-preset]')) button.addEventListener('click', () => {
  const preset = button.dataset.preset;
  document.querySelector(`input[name="mode"][value="${preset === 'mp3' ? 'audio' : 'video'}"]`).checked = true;
  $('format-selector').value = '';
  if (preset === 'mp3') { $('audio-format').value = 'mp3'; $('audio-quality').value = '192'; }
  else { $('container').value = preset === 'best' ? 'mkv' : 'mp4'; $('video-codec').value = 'auto'; $('fps').value = '0';
    $('resolution').value = preset === 'best' ? '0' : [...$('resolution').options].some(option => option.value === '1080') ? '1080' : '0'; }
  updateControls();
});

async function init() {
  try {
    const preferences = JSON.parse(localStorage.getItem('youtube-local-settings') || '{}');
    if (Object.hasOwn(modes,preferences.mode)) document.querySelector(`input[name="mode"][value="${preferences.mode}"]`).checked = true;
    for (const id of savedFields) if (Object.hasOwn(preferences,id)) {
      if ($(id).type === 'checkbox') $(id).checked = Boolean(preferences[id]);
      else if ($(id).tagName !== 'SELECT' || [...$(id).options].some(option => option.value === preferences[id])) $(id).value = preferences[id];
    }
  } catch { /* Corrupt preferences do not affect the downloader. */ }
  updateControls();
  try {
    state.health = await api('/api/health');
    $('download-dir').textContent = state.health.download_dir;
    $('yt-version').textContent = `yt-dlp ${state.health.version}`;
    if (state.health.cookies_available) $('cookies').value = 'yt_cookies.txt';
    const healthy = state.health.ffmpeg && state.health.ffprobe && state.health.runtimes.length;
    const currentServer = state.health.features?.playlists === true;
    $('server-version-warning').hidden = currentServer;
    $('system-status').classList.toggle('warning', !healthy || !currentServer);
    $('system-status').replaceChildren(node('span','status-dot'), document.createTextNode(!currentServer ? 'Нужен перезапуск сервера' : !state.health.ffmpeg || !state.health.ffprobe ? 'Нужен ffmpeg / ffprobe' : !state.health.runtimes.length ? 'Рекомендуется Node.js / Deno' : 'Локально · всё готово'));
    if (!state.health.ffmpeg || !state.health.ffprobe) errorAt('download-error','Для скачивания и конвертации установите ffmpeg (включая ffprobe), затем перезапустите приложение.');
  } catch(error) { $('system-status').textContent = 'Сервер недоступен'; errorAt('source-error',error.message); }
  await pollJobs();
  setInterval(() => { if (!document.hidden) pollJobs(); },1500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pollJobs(); });
}
init();
