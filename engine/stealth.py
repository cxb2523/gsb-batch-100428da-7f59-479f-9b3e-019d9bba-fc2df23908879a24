"""Boss 直聘反检测注入脚本（基于社区对抗实践整理）。

要点：
- 隐藏 navigator.webdriver 与 cdc_* 自动化痕迹
- 伪装 chrome / languages / plugins / permissions
- 保护 Function.prototype.toString，防止检测脚本发现 API 被篡改
- 过滤 console 参数，防止 CDP 连接信息通过 console 泄漏
"""

STEALTH_JS = r"""
(() => {
  if (window.__hrStealthInjected) return;
  try { Object.defineProperty(window, '__hrStealthInjected', { value: true }); } catch (e) {}

  // 1) webdriver 特征
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch (e) {}

  // 2) 清理 cdc_* 自动化全局变量
  try {
    Object.getOwnPropertyNames(window).forEach(k => {
      if (k.indexOf('cdc_') === 0) { delete window[k]; }
    });
  } catch (e) {}

  // 3) chrome 对象补全
  try {
    if (!window.chrome) {
      window.chrome = { runtime: {}, app: {}, csi: () => ({}), loadTimes: () => ({}) };
    }
    window.chrome.runtime = window.chrome.runtime || {};
    window.chrome.app = window.chrome.app || {};
  } catch (e) {}

  // 4) 语言 / 插件指纹
  try { Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] }); } catch (e) {}
  try { Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] }); } catch (e) {}

  // 5) notifications 权限伪装
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const q = navigator.permissions.query.bind(navigator.permissions);
      navigator.permissions.query = p => (
        p && p.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : q(p)
      );
    }
  } catch (e) {}

  // 6) Function.prototype.toString 保护（隐藏 API 篡改痕迹）
  try {
    const originalToString = Function.prototype.toString;
    Function.prototype.toString = function (...args) {
      if (this && this.__originalSource) return this.__originalSource;
      return originalToString.call(this, ...args);
    };
  } catch (e) {}

  // 7) console 参数过滤（防止对象经 console 泄漏内部状态）
  try {
    ['log', 'debug', 'info', 'warn', 'error', 'dir', 'table'].forEach(m => {
      const orig = console[m] && console[m].bind(console);
      if (!orig) return;
      console[m] = (...args) => orig(...args.map(a =>
        (a !== null && typeof a === 'object') ? {} : a
      ));
    });
  } catch (e) {}
})();
"""
