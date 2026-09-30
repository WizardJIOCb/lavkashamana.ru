/* LAVKA_SCREEN_CHECK_V1 */
(() => {
  const errors = [];
  window.addEventListener("error", e => errors.push(String(e.message || "script error")));
  function openCheck() {
    const tg = window.Telegram?.WebApp;
    if (!/Windows|Macintosh|X11|Linux.*x86_64/i.test(navigator.userAgent) && !["tdesktop", "macos"].includes(tg?.platform)) return;
    const button = document.createElement("button");
    button.textContent = "Экран";
    button.style.cssText = "position:fixed;left:10px;top:8px;z-index:20000;padding:8px 14px;background:#203d28;color:white;border:1px solid white;font:14px system-ui;cursor:pointer";
    let panel;
    const rect = s => {
      const el = document.querySelector(s);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Object.fromEntries(["left", "top", "right", "bottom", "width", "height"].map(k => [k, Math.round(r[k])]));
    };
    button.onclick = () => {
      if (panel) {panel.remove();panel = null;button.textContent = "Экран";return;}
      const shell = document.querySelector(".app-shell");
      const css = shell ? getComputedStyle(shell) : null;
      const v = window.visualViewport;
      const report = {
        version:"screen-check-1",
        site:location.origin + location.pathname,
        platform:tg?.platform || "нет",
        desktop:document.documentElement.classList.contains("lavka-desktop"),
        pointer:matchMedia("(hover: hover) and (pointer: fine)").matches,
        window:[innerWidth,innerHeight],
        visual:v ? [v.width,v.height,v.offsetLeft,v.offsetTop] : null,
        telegram:[tg?.viewportHeight,tg?.viewportStableHeight,tg?.isFullscreen],
        screen:[screen.width,screen.height,screen.availLeft,screen.availTop,screen.availWidth,screen.availHeight],
        native:[screenX,screenY,outerWidth,outerHeight],
        scale:devicePixelRatio,
        shell:rect(".app-shell"),
        menu:rect(".bottom-nav"),
        css:css ? [css.position,css.left,css.top,css.transform] : null,
        safe:tg?.safeAreaInset,
        contentSafe:tg?.contentSafeAreaInset,
        scripts:Array.from(document.scripts).map(s => s.getAttribute("src")).filter(s => s && s.startsWith("/")),
        errors
      };
      panel = document.createElement("textarea");
      panel.readOnly = true;
      panel.value = JSON.stringify(report,null,2);
      panel.style.cssText = "position:fixed;left:10px;top:50px;z-index:20000;width:min(370px,calc(100vw - 20px));height:min(500px,calc(100vh - 65px));padding:12px;background:white;color:black;font:13px monospace;line-height:1.35;resize:none;overflow:auto";
      panel.onclick = () => panel.select();
      document.body.append(panel);
      button.textContent = "Закрыть проверку";
    };
    document.body.append(button);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded",openCheck,{once:true});
  else openCheck();
})();
