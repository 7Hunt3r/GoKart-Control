/* Browser-local presentation preferences. No server or vehicle commands. */
"use strict";
(() => {
  const key = "gk_driver_display_preferences";
  const root = document.documentElement;
  const defaults = { theme: "dark", contrast: false, brightness: 80 };
  function normalize(value) {
    if (!value || typeof value !== "object") return { ...defaults };
    return {
      theme: value.theme === "light" ? "light" : "dark",
      contrast: value.contrast === true,
      brightness: Number.isFinite(value.brightness)
        ? Math.max(30, Math.min(100, Math.round(value.brightness / 5) * 5)) : 80,
    };
  }
  let settings;
  try { settings = normalize(JSON.parse(localStorage.getItem(key))); }
  catch (_) { settings = { ...defaults }; }

  const dialog = document.createElement("dialog");
  dialog.id = "displaySettings";
  dialog.className = "display-dialog";
  dialog.setAttribute("aria-labelledby", "displayTitle");
  dialog.innerHTML = `
    <header><h2 id="displayTitle">Display settings</h2><button type="button" data-display-done>Done</button></header>
    <fieldset><legend>Appearance</legend><div class="display-themes">
      <button type="button" data-display-theme="light">Light</button>
      <button type="button" data-display-theme="dark">Dark</button>
    </div></fieldset>
    <label class="display-contrast"><span>High contrast<small>Stronger text and map overlays</small></span><input type="checkbox" id="displayContrast"></label>
    <div class="display-brightness-label"><label for="displayBrightness">Brightness</label><output id="displayBrightnessValue" for="displayBrightness"></output></div>
    <div class="display-brightness-row">
      <button type="button" data-brightness-step="-10" aria-label="Decrease display brightness">−</button>
      <input id="displayBrightness" type="range" min="30" max="100" step="5" aria-describedby="displayBrightnessNote">
      <button type="button" data-brightness-step="10" aria-label="Increase display brightness">+</button>
    </div>
    <p id="displayBrightnessNote">Adjusts the page appearance, not the physical backlight.</p>
    <p class="display-status" aria-live="polite"></p>`;
  document.body.appendChild(dialog);
  const find = (selector) => dialog.querySelector(selector);
  let opener = null;
  function apply(save) {
    root.dataset.theme = settings.theme;
    root.dataset.contrast = settings.contrast ? "high" : "standard";
    root.style.colorScheme = settings.theme;
    root.style.setProperty("--display-brightness", String(settings.brightness / 80));
    dialog.querySelectorAll("[data-display-theme]").forEach((button) => {
      const selected = button.dataset.displayTheme === settings.theme;
      button.classList.toggle("sel", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    find("#displayContrast").checked = settings.contrast;
    find("#displayBrightness").value = settings.brightness;
    find("#displayBrightnessValue").textContent = settings.brightness + "%";
    find('[data-brightness-step="-10"]').disabled = settings.brightness <= 30;
    find('[data-brightness-step="10"]').disabled = settings.brightness >= 100;
    find(".display-status").textContent = `${settings.theme === "dark" ? "Dark" : "Light"} mode · ${settings.contrast ? "high" : "standard"} contrast`;
    if (save) {
      try { localStorage.setItem(key, JSON.stringify(settings)); } catch (_) { /* storage is optional */ }
    }
    window.dispatchEvent(new CustomEvent("gokart-display-change", {detail: {contrast: settings.contrast}}));
  }
  document.querySelectorAll("[data-display-open]").forEach((button) => {
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-controls", dialog.id);
    button.addEventListener("click", () => {
      opener = button;
      dialog.showModal();
      find("[data-display-done]").focus();
    });
  });
  find("[data-display-done]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { if (opener) opener.focus(); });
  dialog.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.displayTheme) settings.theme = button.dataset.displayTheme;
    else if (button.dataset.brightnessStep) settings.brightness = Math.max(30, Math.min(100, settings.brightness + Number(button.dataset.brightnessStep)));
    else return;
    apply(true);
  });
  find("#displayContrast").addEventListener("change", (event) => {
    settings.contrast = event.target.checked;
    apply(true);
  });
  find("#displayBrightness").addEventListener("input", (event) => {
    settings.brightness = Number(event.target.value);
    apply(false);
  });
  find("#displayBrightness").addEventListener("change", () => apply(true));
  window.addEventListener("storage", (event) => {
    if (event.key !== key) return;
    try { settings = normalize(JSON.parse(event.newValue)); apply(false); } catch (_) { /* ignore invalid data */ }
  });
  window.addEventListener("gokart-sun-change", (event) => {
    if (settings.contrast !== event.detail) { settings.contrast = event.detail; apply(true); }
  });
  apply(false);
})();
