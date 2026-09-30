/* LAVKA_DESKTOP_LAYOUT_V1 */
(() => {
  const app = window.Telegram?.WebApp;
  const platform = String(app?.platform || "").toLowerCase();
  const mobile = ["android", "ios"].includes(platform) || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
  if (mobile || !(["tdesktop", "macos"].includes(platform) || matchMedia("(hover: hover) and (pointer: fine)").matches)) return;
  const root = document.documentElement;
  root.classList.add("lavka-desktop");
  const style = document.createElement("style");
  style.textContent = `
html.lavka-desktop, .lavka-desktop body {height:100%;margin:0;overflow:hidden;}
.lavka-desktop .app-shell {position:fixed;left:calc(var(--ld-left) + var(--ld-width)/2);top:calc(var(--ld-top) + var(--ld-height)/2);transform:translate(-50%,-50%);width:min(760px,var(--ld-width));height:min(900px,var(--ld-height));min-height:0;margin:0;padding:0;display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--line);border-radius:24px;box-shadow:0 12px 40px rgba(32,26,16,.18);background:var(--paper);}
.lavka-desktop .hero {flex:0 0 auto;min-height:0!important;height:clamp(160px,calc(var(--ld-height)*.25),200px);margin:8px;padding-top:16px;}
.lavka-desktop .hero .logo {width:72px!important;height:72px!important;}
.lavka-desktop .hero h1 {font-size:27px;}
.lavka-desktop .hero > .hero-social a {width:42px!important;height:42px!important;}
.lavka-desktop #content {flex:1 1 0;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:4px 16px 24px;scrollbar-gutter:stable;}
.lavka-desktop .bottom-nav {position:static;transform:none;left:auto;bottom:auto;width:100%;flex:0 0 auto;padding:7px 9px!important;}
.lavka-desktop .bottom-nav button {min-width:0;}
.lavka-desktop .modal, .lavka-desktop #lavkaPrivacyGate {inset:auto;left:var(--ld-left);top:var(--ld-top);width:var(--ld-width);height:var(--ld-height);align-items:center;padding:12px;}
.lavka-desktop .modal-card, .lavka-desktop .privacy-gate-card {max-height:calc(var(--ld-height) - 24px);overflow:auto;}
.lavka-desktop .toast {bottom:auto;top:calc(var(--ld-top) + var(--ld-height) - 100px);max-width:calc(var(--ld-width) - 24px);}
`;
  document.head.append(style);
  const positive = n => Number.isFinite(Number(n)) && Number(n) > 0 ? Number(n) : Infinity;
  const inset = side => Math.max(0, Number(app?.safeAreaInset?.[side]) || 0, Number(app?.contentSafeAreaInset?.[side]) || 0);
  function size() {
    const v = window.visualViewport;
    const width = Math.min(positive(window.innerWidth), positive(v?.width));
    const height = Math.min(positive(window.innerHeight), positive(v?.height), positive(app?.viewportStableHeight));
    const values = {
      left:(v?.offsetLeft || 0) + inset("left") + 12,
      top:(v?.offsetTop || 0) + inset("top") + 12,
      width:Math.max(1, width - inset("left") - inset("right") - 24),
      height:Math.max(1, height - inset("top") - inset("bottom") - 24)
    };
    for (const [key, value] of Object.entries(values)) root.style.setProperty("--ld-" + key, value + "px");
  }
  size();
  window.addEventListener("resize", size);
  window.visualViewport?.addEventListener("resize", size);
  window.visualViewport?.addEventListener("scroll", size);
  for (const event of ["viewportChanged", "safeAreaChanged", "contentSafeAreaChanged"]) {
    try { app?.onEvent?.(event, size); } catch (_) {}
  }
})();
