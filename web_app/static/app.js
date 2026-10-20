const $ = (id) => document.getElementById(id);
const state = { info: null, health: null, analysis: null, analysisId: 0, jobs: [], jobsKey: '', toastTimer: null, estimateTimer: null, estimateRequest: null, estimateId: 0, playlistSelected: new Set(), playlistVisible: 100, downloading: false };
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
  try {
    if (state.info?.kind === 'playlist') {
      const result = await api('/api/playlists/download',{method:'POST',body:JSON.stringify({analysis_id:state.info.analysis_id, settings:settings(), entry_indices:[...state.playlistSelected]})});
      toast(`В очередь добавлено видео: ${result.count}`);
    } else {
      await api('/api/jobs', {method:'POST', body:JSON.stringify(settings())});
      toast('Загрузка добавлена в очередь');
    }
    await pollJobs();
  } catch (error) { errorAt('download-error',error.message); }
  finally { state.downloading = false; updatePlaylistSelection(); }
});

function renderJobs() {
  const list = $('jobs-list');
  $('jobs-count').textContent = state.jobs.length;
  $('nav-count').textContent = state.jobs.filter(active).length || state.jobs.length;
  const completedFiles = state.jobs.filter(job => job.status === 'complete').flatMap(job => job.files || []);
  const totalBytes = completedFiles.reduce((total, file) => total + (Number.isFinite(file.size) ? file.size : 0), 0);
  $('downloaded-size').textContent = bytes(totalBytes);
  $('downloaded-size').title = `${totalBytes.toLocaleString('ru-RU')} байт`;
  $('downloaded-files').textContent = completedFiles.length;
  if (!state.jobs.length) return;
  list.replaceChildren();
  const batches = new Map();
  for (const job of state.jobs) if (job.batch_id) {
    if (!batches.has(job.batch_id)) batches.set(job.batch_id,[]);
    batches.get(job.batch_id).push(job);
  }
  for (const [id,jobs] of batches) {
    const batch = node('div','playlist-batch');
    batch.append(node('strong','',jobs[0].playlist_title),node('p','',`Готово ${jobs.filter(job => job.status === 'complete').length} из ${jobs.length} · ошибок ${jobs.filter(job => job.status === 'failed').length} · в очереди и обработке ${jobs.filter(active).length}`));
    if (jobs.some(active)) {
      const cancel = node('button','text-button','Отменить весь плейлист'); cancel.type = 'button';
      cancel.addEventListener('click',async () => { cancel.disabled = true; try { await api(`/api/batches/${id}/cancel`,{method:'POST',body:'{}'}); await pollJobs(); } catch(error) { toast(error.message); cancel.disabled = false; } });
      batch.append(cancel);
    }
    list.append(batch);
  }
  const ordered = [...state.jobs.filter(active).reverse(), ...state.jobs.filter(job => !active(job))];
  for (const job of ordered) {
    const card = node('article','job-card');
    const top = node('div','job-top'), jobIcon = node('div','job-icon'), titleBlock = node('div');
    jobIcon.append(icon(job.mode === 'audio' ? 'music' : job.mode === 'silent' ? 'muted' : 'video'));
    titleBlock.append(node('h3','job-title',job.title), node('p','job-preset',job.preset));
    top.append(jobIcon,titleBlock); card.append(top);
    if (job.playlist_title) card.append(node('p','job-preset',`${job.playlist_index}. ${job.playlist_title}`));
    const status = node('div',`job-status ${job.status}`);
    status.append(node('span','',job.phase), node('span','',active(job) && Number.isFinite(job.percent) ? `${job.percent}%` : job.status === 'complete' ? '✓' : ''));
    card.append(status);
    if (active(job)) {
      const track = node('div','progress-track'), fill = node('div',`progress-fill${job.percent === null ? ' indeterminate' : ''}`);
      if (job.percent !== null) fill.style.width = `${job.percent}%`;
      track.append(fill);card.append(track);
      const stats = node('div','job-stats');
      stats.append(node('span','', `${bytes(job.downloaded)}${job.total ? ' / ' + bytes(job.total) : ''}`), node('span','',job.speed ? `${bytes(job.speed)}/с · ${duration(job.eta)}` : job.status === 'processing' ? 'Обработка…' : ''));
      card.append(stats);
    }
    if (job.error) card.append(node('p','job-error', job.error));
    if (job.storage_error) card.append(node('p','job-error', job.storage_error));
    if (job.warnings?.length) { const details = node('details','job-warning'); details.append(node('summary','',`Замечания загрузчика (${job.warnings.length})`), node('p','job-error',job.warnings.join('\n'))); card.append(details); }
    const actions = node('div','job-actions');
    if (active(job)) {
      const cancel = node('button','cancel','Отменить'); cancel.type = 'button';
      cancel.disabled = job.phase === 'Сохраняем в Загрузки';
      cancel.addEventListener('click',async () => { cancel.disabled = true; try { await api(`/api/jobs/${job.id}/cancel`,{method:'POST',body:'{}'}); await pollJobs(); } catch(error) { toast(error.message); cancel.disabled = false; } });
      actions.append(cancel);
    }
    if (['failed','cancelled','interrupted'].includes(job.status)) {
      const retry = node('button','','Вставить ссылку'); retry.type = 'button';
      retry.addEventListener('click', () => { $('url').value = job.url; invalidateAnalysis(); $('url').focus(); $('url').scrollIntoView({block:'center'}); toast('Ссылка вставлена. Выберите настройки и скачайте снова.'); });
      actions.append(retry);
    }
    if (job.status === 'complete') {
      const jobBytes = job.files.reduce((total, file) => total + (Number.isFinite(file.size) ? file.size : 0), 0);
      card.append(node('p','job-volume',`Общий объём: ${bytes(jobBytes)} · файлов: ${job.files.length}`));
      if (job.saved_dir) card.append(node('p','job-preset',job.storage_error ? 'Файлы уже в папке Загрузки' : 'Уже в папке Загрузки · временные файлы очищены'));
      const files = node('div','job-files');
      for (const file of job.files) {
        const link = node('a','file-link', `${file.name} · ${bytes(file.size)}`);
        link.href = `/api/jobs/${job.id}/files/${encodeURIComponent(file.name)}`; link.download = file.name; link.title = `Скачать ещё одну копию ${file.name}`;
        files.append(link);
      }
      if (job.files.length > 1) { const bundle = node('a','file-link','Скачать копию архивом .zip ↓'); bundle.href = `/api/jobs/${job.id}/bundle`; bundle.download = ''; actions.append(bundle); }
      card.append(files);
    }
    if (actions.children.length) card.append(actions);
    list.append(card);
  }
}

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
    $('system-status').classList.toggle('warning', !healthy);
    $('system-status').replaceChildren(node('span','status-dot'), document.createTextNode(!state.health.ffmpeg || !state.health.ffprobe ? 'Нужен ffmpeg / ffprobe' : !state.health.runtimes.length ? 'Рекомендуется Node.js / Deno' : 'Локально · всё готово'));
    if (!state.health.ffmpeg || !state.health.ffprobe) errorAt('download-error','Для скачивания и конвертации установите ffmpeg (включая ffprobe), затем перезапустите приложение.');
  } catch(error) { $('system-status').textContent = 'Сервер недоступен'; errorAt('source-error',error.message); }
  await pollJobs();
  setInterval(() => { if (!document.hidden) pollJobs(); },1500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pollJobs(); });
}
init();
