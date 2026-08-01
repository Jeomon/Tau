"""JavaScript injected via Page.addScriptToEvaluateOnNewDocument for stealth mode.

An anti-detection script. It runs before any page script on every new document
(when BrowserSettings.stealth is True) and normalizes the browser fingerprint to
a common, automation-free profile: hides navigator.webdriver, adds imperceptible
canvas/WebGL/audio noise so per-visit fingerprints don't stay constant, blocks
WebRTC IP leaks, presents a realistic plugin/mimeType set and window.chrome
object, and strips leaked CDP/ChromeDriver globals.

Deliberately omitted:
  * navigator.platform = 'Win32' — only consistent when the User-Agent (and the
    sec-ch-ua client-hint headers Chrome sends) are also forced to Windows.
    Spoofing platform alone contradicts the real UA and is a stronger tell than
    leaving it untouched. Do full Windows normalization (UA + userAgentMetadata
    client hints together) if you want a cross-OS-consistent identity.

devicePixelRatio is intentionally NOT spoofed: the DOM service reads it via JS
to convert DOMSnapshot device-pixel bounds to CSS pixels, so overriding it
would break interactive-element detection.
"""

from __future__ import annotations

STEALTH_SCRIPT = r"""
(() => {
  // 1. Remove webdriver flag
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  // 2. Languages
  Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
    configurable: true,
  });

  // 3. Vendor
  Object.defineProperty(navigator, 'vendor', {
    get: () => 'Google Inc.',
    configurable: true,
  });

  // 4. Hardware concurrency
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
    configurable: true,
  });

  // 5. Device memory
  Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
    configurable: true,
  });

  // 6. Max touch points (desktop = 0)
  Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 0,
    configurable: true,
  });

  // 7. Realistic Chrome object (only fill it in when missing, e.g. headless)
  if (!window.chrome || !window.chrome.runtime) {
    window.chrome = {
      app: {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
      },
      runtime: {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', GC: 'gc', OS_UPDATE: 'os_update' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
      },
    };
  }

  // 8. Permissions — handle notifications correctly, pass others through
  const _origPermQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origPermQuery(parameters)
  );

  // 9. Canvas fingerprint noise — add imperceptible jitter to text rendering
  const _origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...args) {
    const ctx = _origGetContext.call(this, type, ...args);
    if (type === '2d' && ctx) {
      const _origFillText = ctx.fillText.bind(ctx);
      ctx.fillText = function (text, x, y, ...rest) {
        ctx.shadowBlur = Math.random() * 0.02;
        return _origFillText(text, x, y, ...rest);
      };
      const _origStrokeText = ctx.strokeText.bind(ctx);
      ctx.strokeText = function (text, x, y, ...rest) {
        ctx.shadowBlur = Math.random() * 0.02;
        return _origStrokeText(text, x, y, ...rest);
      };
    }
    return ctx;
  };

  // 10. WebGL — spoof UNMASKED_VENDOR and UNMASKED_RENDERER
  const _webglParamHandler = {
    apply(target, ctx, args) {
      const param = args[0];
      if (param === 37445) return 'Intel Inc.';              // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
      return Reflect.apply(target, ctx, args);
    },
  };
  try {
    WebGLRenderingContext.prototype.getParameter = new Proxy(
      WebGLRenderingContext.prototype.getParameter, _webglParamHandler
    );
  } catch (_) {}
  try {
    WebGL2RenderingContext.prototype.getParameter = new Proxy(
      WebGL2RenderingContext.prototype.getParameter, _webglParamHandler
    );
  } catch (_) {}

  // 11. Battery API — report always charging and full
  if (navigator.getBattery) {
    navigator.getBattery = () => Promise.resolve({
      charging: true,
      chargingTime: 0,
      dischargingTime: Infinity,
      level: 1.0,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() { return true; },
    });
  }

  // 12. Network connection — report stable 4G
  try {
    Object.defineProperty(navigator, 'connection', {
      get: () => ({
        rtt: 100,
        downlink: 10,
        effectiveType: '4g',
        saveData: false,
        addEventListener() {},
        removeEventListener() {},
      }),
      configurable: true,
    });
  } catch (_) {}

  // 13. Clean up CDP / ChromeDriver artifacts leaked onto window
  try {
    Object.keys(window).forEach(key => {
      if (key.startsWith('cdc_') || key.startsWith('__cdc_')) {
        try { delete window[key]; } catch (_) {}
      }
    });
  } catch (_) {}

  // 14. Canvas getImageData pixel noise — imperceptible per-pixel jitter on read-back
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (...args) {
    const imageData = _origGetImageData.apply(this, args);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      data[i]     = Math.max(0, Math.min(255, data[i]     + (Math.random() < 0.05 ? (Math.random() < 0.5 ? 1 : -1) : 0)));
      data[i + 1] = Math.max(0, Math.min(255, data[i + 1] + (Math.random() < 0.05 ? (Math.random() < 0.5 ? 1 : -1) : 0)));
      data[i + 2] = Math.max(0, Math.min(255, data[i + 2] + (Math.random() < 0.05 ? (Math.random() < 0.5 ? 1 : -1) : 0)));
    }
    return imageData;
  };

  // 15. Audio context fingerprint noise — perturb analyser frequency data
  try {
    const _origCreateAnalyser = AudioContext.prototype.createAnalyser;
    AudioContext.prototype.createAnalyser = function (...args) {
      const analyser = _origCreateAnalyser.apply(this, args);
      const _origGetFloatFrequencyData = analyser.getFloatFrequencyData.bind(analyser);
      analyser.getFloatFrequencyData = function (array) {
        _origGetFloatFrequencyData(array);
        for (let i = 0; i < array.length; i++) {
          array[i] += (Math.random() * 0.0002 - 0.0001);
        }
      };
      const _origGetByteFrequencyData = analyser.getByteFrequencyData.bind(analyser);
      analyser.getByteFrequencyData = function (array) {
        _origGetByteFrequencyData(array);
        for (let i = 0; i < array.length; i++) {
          array[i] = Math.max(0, Math.min(255, array[i] + (Math.random() < 0.05 ? (Math.random() < 0.5 ? 1 : -1) : 0)));
        }
      };
      return analyser;
    };
    OfflineAudioContext.prototype.createAnalyser = AudioContext.prototype.createAnalyser;
  } catch (_) {}

  // 16. WebRTC IP leak prevention — suppress ICE candidates that expose real/local IPs
  try {
    const _origRTCPC = window.RTCPeerConnection;
    if (_origRTCPC) {
      window.RTCPeerConnection = function (config, ...rest) {
        const pc = new _origRTCPC(config, ...rest);
        const _origAddICE = pc.addIceCandidate.bind(pc);
        pc.addIceCandidate = function (candidate, ...a) {
          if (candidate && candidate.candidate) {
            const c = candidate.candidate;
            if (/typ (host|srflx)/.test(c) && !/typ relay/.test(c)) {
              return Promise.resolve();
            }
          }
          return _origAddICE(candidate, ...a);
        };
        return pc;
      };
      Object.setPrototypeOf(window.RTCPeerConnection, _origRTCPC);
      window.RTCPeerConnection.prototype = _origRTCPC.prototype;
    }
  } catch (_) {}

  // 17. navigator.plugins and mimeTypes — spoof a realistic Chrome plugin set
  try {
    const _fakePlugin = (name, desc, filename, mimeTypes) => {
      const p = Object.create(Plugin.prototype || {});
      Object.defineProperties(p, {
        name:        { get: () => name,     enumerable: true },
        description: { get: () => desc,     enumerable: true },
        filename:    { get: () => filename, enumerable: true },
        length:      { get: () => mimeTypes.length, enumerable: true },
      });
      mimeTypes.forEach((mt, i) => { p[i] = mt; p[mt.type] = mt; });
      p[Symbol.iterator] = function* () { for (let i = 0; i < mimeTypes.length; i++) yield mimeTypes[i]; };
      return p;
    };
    const pdfPlugin = _fakePlugin('PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer', []);
    const chromePDF = _fakePlugin('Chrome PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer', []);
    const nativeClient = _fakePlugin('Native Client', '', 'internal-nacl-plugin', []);

    const pluginArray = [pdfPlugin, chromePDF, nativeClient];
    const fakePlugins = Object.create(PluginArray.prototype || {});
    pluginArray.forEach((p, i) => { fakePlugins[i] = p; fakePlugins[p.name] = p; });
    Object.defineProperty(fakePlugins, 'length', { get: () => pluginArray.length });
    fakePlugins[Symbol.iterator] = function* () { yield* pluginArray; };
    fakePlugins.item = (i) => pluginArray[i];
    fakePlugins.namedItem = (n) => fakePlugins[n] || null;
    fakePlugins.refresh = () => {};

    Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins, configurable: true });
  } catch (_) {}

  // 18. Screen / outer dimensions consistency — eliminate automation-induced mismatches.
  // devicePixelRatio is intentionally NOT spoofed (the DOM service depends on it).
  try {
    const _w = 1920, _h = 1080;
    for (const [prop, val] of [['width', _w], ['height', _h], ['availWidth', _w], ['availHeight', _h - 40]]) {
      Object.defineProperty(screen, prop, { get: () => val, configurable: true });
    }
    Object.defineProperty(screen, 'colorDepth',  { get: () => 24, configurable: true });
    Object.defineProperty(screen, 'pixelDepth',  { get: () => 24, configurable: true });
    Object.defineProperty(window, 'outerWidth',  { get: () => _w, configurable: true });
    Object.defineProperty(window, 'outerHeight', { get: () => _h, configurable: true });
  } catch (_) {}
})();
"""
