/**
 * Session-owned in-page Dialog. It is independent from the Probe runtime and
 * exposes page selections for host reads and displays read-only progress.
 */
export function getAnalysisPanelScript() {
  return String.raw`
(function installDeepSpiderDialog() {
  if (globalThis.__deepspider_dialog_state__) return;

  const topFrame = globalThis === globalThis.top;
  const state = {
    selecting: false,
    current: null,
    overlay: null,
    info: null,
    selected: [],
    localSelected: [],
    listeners: [],
  };
  globalThis.__deepspider_dialog_state__ = state;

  function listen(target, type, handler, options) {
    target.addEventListener(type, handler, options);
    state.listeners.push([target, type, handler, options]);
  }

  function xpath(element) {
    if (!element) return '';
    if (element.id) return '//*[@id="' + element.id.replace(/"/g, '\\"') + '"]';
    const parts = [];
    let node = element;
    while (node && node.nodeType === 1) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(node.tagName.toLowerCase() + '[' + index + ']');
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  }

  function ensureSelectorUi() {
    if (state.overlay) return;
    const overlay = document.createElement('div');
    overlay.id = 'deepspider-selector-overlay';
    overlay.style.cssText = 'position:fixed;display:none;pointer-events:none;border:2px solid #38bdf8;background:rgba(56,189,248,.12);z-index:2147483645;';
    const info = document.createElement('div');
    info.id = 'deepspider-selector-info';
    info.style.cssText = 'position:fixed;display:none;pointer-events:none;background:#0f172a;color:#e0f2fe;padding:4px 7px;border-radius:4px;font:11px ui-monospace,monospace;z-index:2147483646;';
    document.documentElement.append(overlay, info);
    state.overlay = overlay;
    state.info = info;
  }

  function stopSelecting() {
    state.selecting = false;
    state.current = null;
    document.documentElement.style.cursor = '';
    if (state.overlay) state.overlay.style.display = 'none';
    if (state.info) state.info.style.display = 'none';
  }

  function startSelecting() {
    ensureSelectorUi();
    state.selecting = true;
    document.documentElement.style.cursor = 'crosshair';
  }

  function selectorMove(event) {
    if (!state.selecting) return;
    const target = document.elementFromPoint(event.clientX, event.clientY);
    if (!target || target.closest?.('#deepspider-panel')) { state.current = null; return; }
    state.current = target;
    const rect = target.getBoundingClientRect();
    Object.assign(state.overlay.style, {
      display: 'block', left: rect.left + 'px', top: rect.top + 'px',
      width: rect.width + 'px', height: rect.height + 'px',
    });
    state.info.textContent = target.tagName.toLowerCase() + (target.id ? '#' + target.id : '');
    Object.assign(state.info.style, {
      display: 'block', left: rect.left + 'px', top: Math.max(0, rect.top - 24) + 'px',
    });
  }

  function selectorClick(event) {
    if (!state.selecting || !state.current || event.target.closest?.('#deepspider-panel')) return;
    event.preventDefault();
    event.stopPropagation();
    const selected = {
      text: (state.current.innerText || state.current.textContent || '').trim().slice(0, 1000),
      xpath: xpath(state.current),
      frameUrl: location.href,
    };
    stopSelecting();
    if (state.localSelected.length >= 20) {
      globalThis.top.postMessage({ type: 'deepspider-selection-limit' }, '*');
      return;
    }
    state.localSelected.push({ text: selected.text, xpath: selected.xpath.slice(0, 2048) });
    if (topFrame) {
      addSelection(selected);
      forwardToChildren('deepspider-stop-select');
    } else globalThis.top.postMessage({ type: 'deepspider-iframe-selection', selected }, '*');
  }

  function forwardToChildren(type) {
    for (const frame of document.querySelectorAll('iframe')) {
      frame.contentWindow?.postMessage({ type }, '*');
    }
  }

  function clearSelections() {
    stopSelecting();
    state.localSelected.length = 0;
    state.selected.length = 0;
    if (topFrame) { renderSelections(); status.textContent = '选择已清除'; }
  }

  globalThis.__deepspider_dialog_selection__ = function readLocalSelection() {
    return state.localSelected.slice(0, 20).map(function (item) {
      return { text: item.text, xpath: item.xpath };
    });
  };

  listen(document, 'mousemove', selectorMove, true);
  listen(document, 'click', selectorClick, true);
  listen(document, 'keydown', function onKey(event) {
    if (event.key === 'Escape') stopSelecting();
  }, true);
  listen(globalThis, 'message', function onWindowMessage(event) {
    const type = event.data?.type;
    if (type === 'deepspider-start-select') startSelecting();
    else if (type === 'deepspider-stop-select') stopSelecting();
    else if (type === 'deepspider-clear-select') clearSelections();
    else if (type === 'deepspider-close-dialog') { closeDialog(); return; }
    else if (topFrame && type === 'deepspider-iframe-selection') {
      addSelection(event.data.selected);
      stopSelecting();
      forwardToChildren('deepspider-stop-select');
      return;
    } else if (topFrame && type === 'deepspider-selection-limit') {
      status.textContent = '最多选择 20 个元素，请清除后重新选择';
      stopSelecting();
      forwardToChildren('deepspider-stop-select');
      return;
    } else return;
    forwardToChildren(type);
  });

  function closeDialog() {
    forwardToChildren('deepspider-close-dialog');
    stopSelecting();
    for (const [target, type, handler, options] of state.listeners) {
      target.removeEventListener(type, handler, options);
    }
    document.getElementById('deepspider-panel')?.remove();
    document.getElementById('deepspider-panel-style')?.remove();
    state.overlay?.remove();
    state.info?.remove();
    delete globalThis.__deepspider_dialog_receive__;
    delete globalThis.__deepspider_dialog_close__;
    delete globalThis.__deepspider_dialog_state__;
    delete globalThis.__deepspider_dialog_selection__;
  }
  globalThis.__deepspider_dialog_close__ = closeDialog;

  if (!topFrame) return;

  const style = document.createElement('style');
  style.id = 'deepspider-panel-style';
  style.textContent = [
    '#deepspider-panel{all:initial;position:fixed;right:18px;bottom:18px;width:390px;max-height:72vh;display:flex;flex-direction:column;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.35);font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;z-index:2147483647}',
    '#deepspider-panel *{box-sizing:border-box}',
    '.ds-head{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #334155}.ds-head strong{flex:1;color:#f8fafc}.ds-icon{border:0;background:transparent;color:#94a3b8;font-size:18px;cursor:pointer}',
    '.ds-status{padding:6px 12px;color:#7dd3fc;border-bottom:1px solid #1e293b;font-size:12px}',
    '.ds-recovery{display:grid;grid-template-columns:1fr auto;gap:5px 10px;padding:9px 12px;border-bottom:1px solid #1e293b}.ds-recovery[hidden]{display:none}.ds-recovery-label{color:#cbd5e1}.ds-recovery-state{color:#7dd3fc}.ds-recovery-summary{grid-column:1/-1;color:#94a3b8;font-size:12px}',
    '.ds-selected{padding:0 12px;color:#cbd5e1;font-size:12px}.ds-chip{display:flex;gap:6px;align-items:center;margin:4px 0}.ds-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ds-chip button{margin-left:auto}',
    '.ds-actions{display:flex;gap:8px;padding:10px 12px}.ds-detail{color:#94a3b8;font-size:12px;padding:0 12px}.ds-button{border:0;border-radius:6px;padding:7px 10px;background:#0369a1;color:white;cursor:pointer}.ds-button.secondary{background:#334155}',
  ].join('');
  document.head.appendChild(style);

  const panel = document.createElement('section');
  panel.id = 'deepspider-panel';
  panel.innerHTML = '<header class="ds-head"><strong>DeepSpider Dialog</strong><button class="ds-icon" data-action="close" aria-label="Close">×</button></header>' +
    '<div class="ds-status">就绪</div><div class="ds-recovery" hidden></div><div class="ds-selected"></div>' +
    '<div class="ds-actions"><button class="ds-button" data-action="select">选择元素</button><button class="ds-button secondary" data-action="clear">清除选择</button></div>' +
    '<p class="ds-detail">网页选择仅作为分析材料。请回 DSH 输入任务和确认。</p>';
  document.documentElement.appendChild(panel);

  const status = panel.querySelector('.ds-status');
  const recoveryBox = panel.querySelector('.ds-recovery');
  const selectedBox = panel.querySelector('.ds-selected');

  const recoveryStages = [
    ['browserEvidence', '浏览器证据'],
    ['artifactGraph', 'Artifact Graph'],
    ['nodeGeneration', 'Node 生成'],
    ['requestValidation', '真实请求验证'],
  ];

  function renderRecovery(payload) {
    recoveryBox.hidden = false;
    recoveryBox.replaceChildren();
    for (const [key, labelText] of recoveryStages) {
      const label = document.createElement('span');
      label.className = 'ds-recovery-label';
      label.textContent = labelText;
      const value = document.createElement('span');
      value.className = 'ds-recovery-state';
      value.textContent = payload.stages?.[key] || 'pending';
      recoveryBox.append(label, value);
    }
    if (payload.type !== 'recovery/result') return;
    const summary = document.createElement('div');
    summary.className = 'ds-recovery-summary';
    const details = [
      payload.strategy,
      payload.evidenceLevels?.request,
      payload.solverId ? 'Solver ' + payload.solverId : null,
      payload.blocker?.reason,
      payload.nextAction,
    ].filter(Boolean);
    summary.textContent = details.join(' · ');
    recoveryBox.appendChild(summary);
  }

  function renderSelections() {
    selectedBox.replaceChildren();
    for (const item of state.selected) {
      const row = document.createElement('div');
      row.className = 'ds-chip';
      const label = document.createElement('span');
      label.textContent = item.text || item.xpath;
      label.title = item.xpath;
      row.append(label);
      selectedBox.appendChild(row);
    }
  }

  function addSelection(item) {
    if (typeof item?.xpath !== 'string' || typeof item.text !== 'string') return;
    if (state.selected.length >= 20) return;
    state.selected.push({ text: item.text.slice(0, 1000), xpath: item.xpath.slice(0, 2048) });
    renderSelections();
  }

  panel.querySelector('[data-action="select"]').onclick = function selectElement() {
    if (state.selected.length >= 20) {
      status.textContent = '最多选择 20 个元素，请清除后重新选择';
      stopSelecting();
      forwardToChildren('deepspider-stop-select');
      return;
    }
    status.textContent = '请选择页面元素，按 Esc 取消';
    startSelecting();
    forwardToChildren('deepspider-start-select');
  };
  panel.querySelector('[data-action="close"]').onclick = closeDialog;
  panel.querySelector('[data-action="clear"]').onclick = function clearSelected() {
    clearSelections();
    forwardToChildren('deepspider-clear-select');
  };

  globalThis.__deepspider_dialog_receive__ = function receiveDialogMessage(payload) {
    if (!payload || typeof payload !== 'object') return;
    if (payload.type === 'status') status.textContent = payload.text;
    if (payload.type === 'recovery/progress' || payload.type === 'recovery/result') renderRecovery(payload);
  };
})();`
}
