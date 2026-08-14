// Lõi chat dùng chung cho các trang (LMS, tư vấn...). Mỗi trang gọi:
//   initChat({ getHeaders: () => ({...}), getContext: () => (null | {screen, course_code}) })
// Trang tự quyết định ngữ cảnh + danh tính — lõi chỉ lo hợp đồng SSE và render.

/* eslint-disable no-unused-vars */

const log = document.getElementById('log');

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}
const escapeHtml = esc;

function add(cls, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

// 'tr. 6' hoặc 'tr. 6–7' khi đoạn trích trải nhiều trang.
function pageLabel(c) {
  if (!c.page) return '';
  return 'tr. ' + c.page + (c.page_to && c.page_to !== c.page ? '–' + c.page_to : '');
}

function citeTooltip(c) {
  return [c.heading_path, pageLabel(c), c.title].filter(Boolean).join(' · ');
}

// --- Popup xem trước trích dẫn ----------------------------------------------
// PDF: nhúng iframe + nhảy đúng trang (#page=N của trình xem PDF Chromium),
// kèm khối "đoạn được trích" nguyên văn để thấy đúng chỗ. Khác PDF: chỉ snippet.
let _previewHeaders = () => ({});

function openPreview(c) {
  closePreview();
  const uid = _previewHeaders()['X-User-Id'] || '';
  const page = c.page || 1;

  const bk = document.createElement('div');
  bk.className = 'pv-backdrop';
  bk.id = 'pv';

  const sub = c.heading_path
    ? `<div class="pv-sub" title="${esc(c.heading_path)}">${esc(c.heading_path)}</div>` : '';
  // Đoạn trích THU GỌN mặc định (1 dòng) — bấm để mở rộng; PDF chiếm tối đa chỗ.
  const snippet = c.snippet
    ? `<div class="pv-snip collapsed">
         <div class="pv-snip-label">Đoạn được trích${c.page ? ` — ${pageLabel(c)}` : ''} <span class="pv-tgl">xem ▾</span></div>
         <div class="pv-snip-text">${esc(c.snippet)}</div>
       </div>`
    : '';
  const frameUrl = c.preview_url
    ? `${c.preview_url}${uid ? `?uid=${uid}` : ''}#page=${page}&navpanes=0&zoom=page-width`
    : null;
  const frame = frameUrl
    ? `<iframe class="pv-frame" src="${frameUrl}"></iframe>`
    : `<div class="pv-nofile">Định dạng này chưa xem trước inline được — bạn tải về để mở nhé.</div>`;

  bk.innerHTML = `
    <div class="pv-modal">
      <div class="pv-head">
        <div class="pv-titlebox">
          <div class="pv-title" title="${esc(c.title)}">📄 ${esc(c.title)}</div>
          ${sub}
        </div>
        <a class="pv-open" href="${c.url}" target="_blank" title="Tải tài liệu">⬇ Tải về</a>
        <button class="pv-close" title="Đóng (Esc)">✕</button>
      </div>
      ${snippet}
      ${frame}
    </div>`;

  bk.addEventListener('click', e => { if (e.target === bk) closePreview(); });
  bk.querySelector('.pv-close').addEventListener('click', closePreview);
  // Chromium nuốt #page= khi PDF nạp chậm trong iframe — nạp xong ép điều hướng
  // fragment lần nữa để chắc chắn nhảy đúng trang được trích.
  const f = bk.querySelector('.pv-frame');
  if (f && frameUrl && page > 1) {
    f.addEventListener('load', () => {
      setTimeout(() => {
        try { f.contentWindow.location.replace(frameUrl + '&r=1'); } catch {}
      }, 300);
    });
  }
  const snip = bk.querySelector('.pv-snip');
  if (snip) snip.querySelector('.pv-snip-label').addEventListener('click', () => {
    snip.classList.toggle('collapsed');
    snip.querySelector('.pv-tgl').textContent =
      snip.classList.contains('collapsed') ? 'xem ▾' : 'thu gọn ▴';
  });
  document.addEventListener('keydown', _pvEsc);
  document.body.appendChild(bk);
}

function closePreview() {
  document.getElementById('pv')?.remove();
  document.removeEventListener('keydown', _pvEsc);
}

function _pvEsc(e) { if (e.key === 'Escape') closePreview(); }

// Gắn citation vào phần tử: click thường mở popup, ctrl/giữa vẫn mở tab mới.
function bindPreview(el, c) {
  el.addEventListener('click', e => {
    if (e.ctrlKey || e.metaKey || e.button === 1) return;
    e.preventDefault();
    openPreview(c);
  });
}

function addCitations(citations) {
  if (!citations || !citations.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'cites';
  wrap.innerHTML = '<div class="cites-label">Nguồn</div>';

  const groups = new Map();
  for (const c of citations) {
    if (!groups.has(c.document_id)) groups.set(c.document_id, []);
    groups.get(c.document_id).push(c);
  }

  for (const group of groups.values()) {
    const first = group[0];
    const a = document.createElement('a');
    a.className = 'cite';
    a.href = first.url;
    a.target = '_blank';
    const pages = group.map(c =>
      `<b title="${esc(citeTooltip(c))}" data-n="${c.n}">${pageLabel(c) || '[' + c.n + ']'}</b>`
    ).join(' · ');
    a.innerHTML = `<span class="doc-ico">📄</span>`
      + `<span class="doc-title">${esc(first.title)}</span>`
      + `<small>${pages}</small>`;
    bindPreview(a, first);
    // Bấm đúng nhãn trang nào thì popup mở đúng đoạn trích đó
    for (const b of a.querySelectorAll('b[data-n]')) {
      const c = group.find(x => x.n === Number(b.dataset.n));
      if (c) b.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation(); openPreview(c);
      });
    }
    wrap.appendChild(a);
  }
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

// Màn hình trống: lời chào + các gợi ý bấm-là-gửi. Tự biến mất khi có tin đầu.
function showWelcome(welcome) {
  if (!welcome) return;
  const el = document.createElement('div');
  el.className = 'welcome';
  el.id = 'welcome';
  const chips = (welcome.chips || []).map(q =>
    `<button type="button" class="chip">${esc(q)}</button>`
  ).join('');
  el.innerHTML = `<div class="logo">${welcome.icon || '🎓'}</div>`
    + `<h2>${esc(welcome.title || 'EDU-DOCBOT')}</h2>`
    + `<p>${welcome.text || ''}</p>`
    + `<div class="chips">${chips}</div>`;
  log.appendChild(el);
  el.querySelectorAll('.chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById('input');
      input.value = btn.textContent;
      document.getElementById('form').requestSubmit();
    });
  });
}

function clearWelcome() {
  const el = document.getElementById('welcome');
  if (el) el.remove();
}

function addTrace(steps) {
  const wrap = document.createElement('div');
  wrap.className = 'msg tracewrap';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'tracebtn';
  btn.textContent = '⏱';
  btn.title = 'Xem thời gian từng bước';

  const detail = document.createElement('div');
  detail.className = 'trace';
  detail.style.display = 'none';
  const max = Math.max(...steps.map(s => s.ms));
  detail.innerHTML = steps.map(s => {
    const t = s.ms >= 1000 ? (s.ms / 1000).toFixed(1) + 's' : s.ms + 'ms';
    const item = `${esc(s.step)} <b>${t}</b>`;
    return (s.ms === max && s.ms > 500) ? `<span class="slow">${item}</span>` : item;
  }).join(' → ');

  btn.onclick = () => {
    detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
  };
  wrap.appendChild(btn);
  wrap.appendChild(detail);
  log.appendChild(wrap);
}

// Dựng câu trả lời cuối: đoạn văn + gạch đầu dòng, [n] (kể cả [1, 6]) → pill trang.
function renderAnswer(el, text, citations) {
  if (!text) return;
  const byN = {};
  for (const c of citations || []) byN[c.n] = c;

  const markPill = n => {
    const c = byN[n];
    if (!c) return `[${n}]`;
    const label = pageLabel(c) || `[${n}]`;
    return `<a class="mark" href="${c.url}" data-n="${n}" target="_blank" title="${esc(citeTooltip(c))}">${label}</a>`;
  };
  const inline = s => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')   // **đậm** của model → thẻ b
    .replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g,
      (m, group) => group.split(/\s*,\s*/).map(markPill).join(''));

  let html = '';
  let inList = false;
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    const bullet = t.match(/^[-•*]\s+(.*)/);
    if (bullet) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += '<li>' + inline(bullet[1]) + '</li>';
    } else {
      if (inList) { html += '</ul>'; inList = false; }
      html += '<p>' + inline(t) + '</p>';
    }
  }
  if (inList) html += '</ul>';

  el.classList.add('rich');
  el.innerHTML = html;
  // Pill [n] trong câu: click thường mở popup xem trước đúng đoạn trích
  for (const a of el.querySelectorAll('a.mark[data-n]')) {
    const c = byN[Number(a.dataset.n)];
    if (c) bindPreview(a, c);
  }
}

function parseFrame(frame) {
  let event = 'message', data = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; }
  catch { return null; }
}

function routeLabel(d) {
  const parts = [d.branch];
  if (d.cached) parts.push('cache');
  if (d.degraded) parts.push('chế độ tiết kiệm');
  if (d.course_code) parts.push(d.course_code);
  return parts.join(' · ');
}

function initChat(config) {
  const form = document.getElementById('form');
  const input = document.getElementById('input');
  const send = document.getElementById('send');
  const sessionId = crypto.randomUUID();
  _previewHeaders = config.getHeaders;  // popup cần user id cho iframe preview

  showWelcome(config.welcome);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;

    clearWelcome();
    add('user', message);
    input.value = '';
    send.disabled = true;

    const headers = { 'Content-Type': 'application/json', ...config.getHeaders() };

    const bot = add('bot typing', '');
    bot.innerHTML = '<span></span><span></span><span></span>';

    let metaEl = null;
    let answerText = '';
    let citations = null;

    const showText = () => {
      if (bot.className.includes('typing')) bot.className = 'msg bot';
      bot.textContent = answerText;
      log.scrollTop = log.scrollHeight;
    };

    try {
      const res = await fetch('/api/v1/chat', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          session_id: sessionId, message, stream: true,
          context: config.getContext(),
        }),
      });
      if (!res.ok || !res.body) {
        answerText = 'Lỗi kết nối máy chủ (' + res.status + ').';
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sep;
        while ((sep = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const ev = parseFrame(frame);
          if (!ev) continue;

          if (ev.event === 'route') {
            metaEl = document.createElement('div');
            metaEl.className = 'msg meta';
            metaEl.textContent = routeLabel(ev.data);
            log.insertBefore(metaEl, bot);
          } else if (ev.event === 'token') {
            answerText += ev.data.text;
            showText();
          } else if (ev.event === 'citations') {
            citations = ev.data.citations;
          } else if (ev.event === 'done') {
            if (metaEl) metaEl.textContent += ` · ${ev.data.latency_ms} ms`;
            if (ev.data.trace && ev.data.trace.length) addTrace(ev.data.trace);
          } else if (ev.event === 'error') {
            answerText = 'Lỗi: ' + ev.data.message;
            showText();
          }
        }
      }
    } catch (err) {
      answerText = answerText || 'Không kết nối được máy chủ.';
    } finally {
      showText();
      renderAnswer(bot, answerText, citations);
      addCitations(citations);
      send.disabled = false;
      input.focus();
    }
  });

  input.focus();
}
