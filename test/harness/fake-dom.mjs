// A minimal fake DOM just rich enough to run the REAL content-script scan functions
// (collectScan / collectScanCandidates / assignRef / getLabel / extractState /
// scanFingerprintOf) under Node. It is a TEST HARNESS, not a DOM implementation:
// only the properties those functions actually read exist, and geometry is
// explicit so viewport membership is something a test controls directly.
//
// Why this exists: the viewport/dialog/removal bugs live in the INTERACTION
// between collectScan's membership rule and the diff. A static grep cannot
// catch "a scroll produced 12 removed / 40 added"; executing the real scan
// against two fake viewport states can.

export function makeEl(tag, opts = {}) {
  const attrs = Object.assign({}, opts.attrs || {});
  const el = {
    nodeType: 1,
    tagName: String(tag).toUpperCase(),
    id: attrs.id || '',
    className: attrs.class || '',
    hidden: !!opts.hidden,
    isContentEditable: !!opts.contentEditable,
    disabled: !!opts.disabled,
    tabIndex: opts.tabIndex !== undefined ? opts.tabIndex : -1,
    parentElement: null,
    children: [],
    shadowRoot: null,
    _text: opts.text || '',
    _rect: Object.assign({ top: 0, left: 0, bottom: 20, right: 100, width: 100, height: 20 }, opts.rect || {}),
    _style: Object.assign({ display: 'block', visibility: 'visible', opacity: '1', cursor: 'auto' }, opts.style || {}),
    _attrs: attrs,
    onclick: opts.onclick || null,
    onkeydown: null,
  };
  Object.defineProperty(el, 'innerText', { get: () => el._text });
  Object.defineProperty(el, 'textContent', { get: () => el._text });
  Object.defineProperty(el, 'attributes', {
    get: () => Object.keys(attrs).map((n) => ({ name: n, value: attrs[n] })),
  });
  Object.defineProperty(el, 'classList', {
    get: () => {
      const list = String(attrs.class || '').split(/\s+/).filter(Boolean);
      return { contains: (c) => list.indexOf(c) >= 0 };
    },
  });
  el.setAttribute = (n, v) => { attrs[n] = String(v); };
  el.getAttribute = (n) => (n in attrs ? attrs[n] : null);
  el.getAttributeNames = () => Object.keys(attrs);
  el.removeAttribute = (n) => { delete attrs[n]; };
  el.appendChild = (c) => { c.parentElement = el; el.children.push(c); return c; };
  el.getBoundingClientRect = () => el._rect;
  el.checkVisibility = () => {
    // Render state walks ANCESTORS, like the real API: a child of display:none
    // is not rendered even if its own style says block.
    let n = el;
    while (n) {
      if (n._style && (n._style.display === 'none' || n._style.visibility === 'hidden')) return false;
      if (n.hidden === true) return false;   // [hidden] is display:none by default
      n = n.parentElement;
    }
    return true;
  };
  el.closest = (sel) => {
    const tags = sel.split(',').map((s) => s.trim().replace(/\[.*$/, '')).filter(Boolean);
    let n = el;
    while (n) {
      if (tags.some((t) => n.tagName === t.toUpperCase())) return n;
      n = n.parentElement;
    }
    return null;
  };
  // Only the selectors the scan's membership test asks about. `:disabled` is
  // the PLATFORM's own answer to "this control is switched off", so it must
  // track the live property exactly as a browser would.
  el.matches = (sel) => {
    const s = String(sel).trim();
    if (s === ':disabled') return el.disabled === true;
    const m = /^\[([^=\]]+)\]$/.exec(s);
    if (m) return attrs[m[1]] !== undefined;
    return false;
  };
  el.querySelectorAll = () => [];
  el._setRect = (rect) => { el._rect = Object.assign({}, el._rect, rect, {
    width: rect.width !== undefined ? rect.width : Math.max(0, (rect.right !== undefined ? rect.right : el._rect.right) - (rect.left !== undefined ? rect.left : el._rect.left)),
    height: rect.height !== undefined ? rect.height : Math.max(0, (rect.bottom !== undefined ? rect.bottom : el._rect.bottom) - (rect.top !== undefined ? rect.top : el._rect.top)),
  }); };
  el._setStyle = (style) => { el._style = Object.assign({}, el._style, style); };
  el._setAttr = (n, v) => { attrs[n] = v; };
  return el;
}

export function makeDoc(rootChildren = []) {
  const body = makeEl('body', { rect: { top: 0, left: 0, bottom: 1e6, right: 2000, width: 2000, height: 1e6 } });
  for (const c of rootChildren) body.appendChild(c);
  const doc = {
    body,
    styleSheets: [],
    documentElement: { clientWidth: 1000, clientHeight: 800 },
    querySelectorAll: () => [],
    querySelector: () => null,
    getElementById: () => null,
    getElementsByTagName: () => [],
  };
  return doc;
}

// Scroll the page: every element's rect shifts by dy. This is exactly what a
// scroll does to geometry, and it is the ONLY thing a scroll changes.
export function scrollBy(elements, dy) {
  for (const el of elements) {
    const r = el._rect;
    el._rect = { top: r.top - dy, bottom: r.bottom - dy, left: r.left, right: r.right, width: r.width, height: r.height };
  }
}
