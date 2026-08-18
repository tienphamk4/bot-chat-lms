// Lõi chat dùng chung. Mỗi popup gọi một lần:
//   initChat({ root: '#bot-tai-lieu', getHeaders: ..., getContext: ..., welcome: ... })
//
// MỌI thứ đều bám vào `root` chứ không vào id toàn cục: một trang có thể mở
// nhiều bot cạnh nhau, mỗi bot một session_id, một khung log, một ô nhập riêng.
// Dùng id toàn cục ở đây là hai bot ghi đè lên nhau ngay lượt gõ đầu tiên.

/* eslint-disable no-unused-vars */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}
const escapeHtml = esc;

function add(log, cls, text) {
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
function openPreview(c, getHeaders) {
  closePreview();
  const uid = (getHeaders ? getHeaders() : {})['X-User-Id'] || '';
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
function bindPreview(el, c, getHeaders) {
  el.addEventListener('click', e => {
    if (e.ctrlKey || e.metaKey || e.button === 1) return;
    e.preventDefault();
    openPreview(c, getHeaders);
  });
}

function addCitations(log, citations, getHeaders) {
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
    bindPreview(a, first, getHeaders);
    // Bấm đúng nhãn trang nào thì popup mở đúng đoạn trích đó
    for (const b of a.querySelectorAll('b[data-n]')) {
      const c = group.find(x => x.n === Number(b.dataset.n));
      if (c) b.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation(); openPreview(c, getHeaders);
      });
    }
    wrap.appendChild(a);
  }
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

// Màn hình trống: lời chào + các gợi ý bấm-là-gửi. Tự biến mất khi có tin đầu.
function showWelcome(log, welcome, form, input) {
  if (!welcome) return;
  const el = document.createElement('div');
  el.className = 'welcome';
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
      input.value = btn.textContent;
      form.requestSubmit();
    });
  });
}

function clearWelcome(log) {
  log.querySelector('.welcome')?.remove();
}

function addTrace(log, steps) {
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
function renderAnswer(el, text, citations, getHeaders) {
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
    if (c) bindPreview(a, c, getHeaders);
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

// crypto.randomUUID() CHỈ tồn tại trong secure context (HTTPS hoặc localhost).
// Máy dev chạy localhost nên không lộ, nhưng bản triển khai đầu tiên phục vụ
// qua http://<IP>/ thì nó undefined và cả trang chat chết ngay dòng khởi tạo
// (TypeError: crypto.randomUUID is not a function). getRandomValues thì vẫn
// dùng được ngoài secure context — chỉ randomUUID và crypto.subtle mới bị chặn.
function newSessionId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();

  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;   // phiên bản 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80;   // biến thể RFC 4122
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function initChat(config) {
  const root = typeof config.root === 'string'
    ? document.querySelector(config.root)
    : config.root;
  if (!root) return;

  const log = root.querySelector('.log');
  const form = root.querySelector('.form');
  const input = root.querySelector('.input');
  const send = root.querySelector('.send');
  const sessionId = newSessionId();
  const getHeaders = config.getHeaders;

  showWelcome(log, config.welcome, form, input);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;

    clearWelcome(log);
    add(log, 'user', message);
    input.value = '';
    send.disabled = true;

    const headers = { 'Content-Type': 'application/json', ...getHeaders() };

    const bot = add(log, 'bot typing', '');
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
            if (ev.data.trace && ev.data.trace.length) addTrace(log, ev.data.trace);
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
      renderAnswer(bot, answerText, citations, getHeaders);
      addCitations(log, citations, getHeaders);
      send.disabled = false;
      input.focus();
    }
  });
}
