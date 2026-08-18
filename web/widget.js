// Vỏ widget: mỗi bot một bong bóng ở góc phải, bấm cái nào mở popup cái đó.
//
//   mountBot({ slug, name, sub, icon, brand, placeholder, welcome,
//              getHeaders, getContext, sheet? })
//
// Markup do JS dựng chứ không chép tay vào từng trang: hai bot dùng chung một
// vỏ, sửa vỏ một chỗ là cả hai đổi theo. Mỗi bot có khung log và ô nhập riêng
// (lớp .log/.form/.input/.send trong phạm vi panel của nó) nên hai cuộc trò
// chuyện không giẫm lên nhau.

const ICONS = {
  robot: `<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V5"/>
          <circle cx="12" cy="3.8" r="1.5" fill="currentColor"/>
          <circle cx="9" cy="13" r="1.2" fill="currentColor" stroke="none"/>
          <circle cx="15" cy="13" r="1.2" fill="currentColor" stroke="none"/>
          <path d="M9.5 16.4h5"/>`,
  advisor: `<path d="M20 15a3 3 0 0 1-3 3H8l-4 3V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3z"/>
            <path d="M9.2 9.2a2.8 2.8 0 0 1 5.4 1c0 1.9-2.6 2.3-2.6 4"/>
            <circle cx="12" cy="15.6" r=".9" fill="currentColor" stroke="none"/>`,
};

function svg(name, cls) {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
     stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"
     aria-hidden="true">${ICONS[name] || ICONS.robot}</svg>`;
}

// Hàng bong bóng — dựng một lần, mọi bot cùng xếp vào đây cho nằm liền kề.
function launcherRow() {
  let row = document.querySelector('.launchers');
  if (!row) {
    row = document.createElement('div');
    row.className = 'launchers';
    document.body.appendChild(row);
  }
  return row;
}

const _panels = [];   // để mở bot này thì bot kia tự đóng

function mountBot(cfg) {
  const row = launcherRow();
  const brandVars = `--brand:${cfg.brand.c1}; --brand-2:${cfg.brand.c2};`
    + `--brand-soft:${cfg.brand.soft}; --brand-ink:${cfg.brand.ink};`
    + `--brand-shadow:${cfg.brand.shadow};`;

  // --- Bong bóng ------------------------------------------------------------
  const launcher = document.createElement('button');
  launcher.className = 'launcher';
  launcher.type = 'button';
  launcher.style.cssText = brandVars;
  launcher.setAttribute('aria-label', 'Mở ' + cfg.name);
  launcher.innerHTML = svg(cfg.icon, 'launcher-ico')
    + `<span class="launcher-tip">${cfg.name}</span>`;
  row.appendChild(launcher);

  // --- Popup ---------------------------------------------------------------
  const panel = document.createElement('section');
  panel.className = 'widget';
  panel.hidden = true;
  panel.style.cssText = brandVars;
  panel.setAttribute('aria-label', cfg.name);
  panel.innerHTML = `
    <header class="w-head">
      <div class="w-avatar">${svg(cfg.icon, 'w-avatar-ico')}</div>
      <div class="w-titles">
        <div class="w-title">${cfg.name}</div>
        <div class="w-sub">${cfg.sub}</div>
      </div>
      <button class="w-act w-reset" type="button" title="Bắt đầu lại cuộc trò chuyện">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>
        </svg>
      </button>
      <button class="w-act w-close" type="button" title="Đóng">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </header>

    <div class="log"></div>
    ${cfg.sheet ? `<div class="w-sheet" hidden>${cfg.sheet}</div>` : ''}

    <form class="form">
      <div class="composer">
        ${cfg.sheet ? `<button class="w-plus" type="button" aria-expanded="false"
                        title="Tùy chọn phiên chat">+</button>` : ''}
        <input class="input" type="text" autocomplete="off"
               placeholder="${cfg.placeholder || 'Nhập nội dung anh/chị muốn hỏi…'}">
        <button class="send" type="submit" title="Gửi">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="m4 12 16-8-6 8 6 8z"/>
          </svg>
        </button>
      </div>
    </form>`;
  document.body.appendChild(panel);
  _panels.push(panel);

  // --- Đóng / mở ------------------------------------------------------------
  const sheet = panel.querySelector('.w-sheet');
  const plus = panel.querySelector('.w-plus');

  function closeSheet() {
    if (!sheet) return;
    sheet.hidden = true;
    plus?.setAttribute('aria-expanded', 'false');
  }

  function open() {
    for (const other of _panels) if (other !== panel) other.hidden = true;
    panel.hidden = false;
    row.hidden = true;
    panel.querySelector('.input').focus();
  }

  function close() {
    panel.hidden = true;
    row.hidden = false;
    closeSheet();
    launcher.focus();
  }

  launcher.addEventListener('click', open);
  panel.querySelector('.w-close').addEventListener('click', close);

  // ↻ Xóa sạch hội thoại và bắt đầu phiên mới — không tải lại trang, vì tải
  // lại sẽ giết luôn cuộc trò chuyện của bot bên cạnh.
  panel.querySelector('.w-reset').addEventListener('click', () => {
    panel.querySelector('.log').replaceChildren();
    // Thay hẳn thẻ form bằng bản sao: cloneNode không mang theo listener, nếu
    // không thì mỗi lần đặt lại lại chồng thêm một handler submit và câu hỏi
    // sẽ bị gửi hai, ba lần.
    const form = panel.querySelector('.form');
    form.replaceWith(form.cloneNode(true));
    initChat(chatConfig());        // session_id mới, lời chào hiện lại
    panel.querySelector('.input').focus();
  });

  if (plus && sheet) {
    plus.addEventListener('click', (e) => {
      e.stopPropagation();
      const opening = sheet.hidden;
      sheet.hidden = !opening;
      plus.setAttribute('aria-expanded', String(opening));
    });
    document.addEventListener('click', (e) => {
      if (!sheet.hidden && !sheet.contains(e.target)) closeSheet();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || panel.hidden) return;
    if (document.getElementById('pv')) return;   // popup xem trước tự lo
    if (sheet && !sheet.hidden) { closeSheet(); return; }
    close();
  });

  // --- Nối vào lõi chat -----------------------------------------------------
  function chatConfig() {
    return {
      root: panel,
      getHeaders: cfg.getHeaders || (() => ({})),
      // Slug đi kèm MỌI câu hỏi: backend dựa vào nó để chọn persona — câu chào,
      // câu từ chối, phạm vi tài liệu, đầu mối liên hệ. Quên trường này là bot
      // nào cũng trả lời bằng giọng mặc định.
      getContext: () => ({ ...(cfg.getContext?.() || {}), assistant: cfg.slug }),
      welcome: cfg.welcome,
    };
  }
  initChat(chatConfig());

  cfg.onReady?.(panel);
  return panel;
}
