/* Vanleg JavaScript: API-et lagrar prosjekt og jobbar; denne fila viser dei. */
"use strict";

const app = document.querySelector("#app");
const notice = document.querySelector("#notice");
const timezone =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Oslo";
const state = {
  config: null,
  route: 0,
  timer: null,
  project: null,
  uploading: false,
  latest: null,
  completed: null,
};
const statusNames = {
  queued: "I kø",
  processing: "Behandlar",
  completed: "Ferdig",
  failed: "Mislukka",
};
const icons = (name) =>
  `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const byId = (id) => document.getElementById(id);
const active = (job) => job && ["queued", "processing"].includes(job.status);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function duration(seconds = 0) {
  const rounded = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}
function bytes(value = 0) {
  const number = Number(value) || 0;
  if (number >= 1024 ** 3) return `${(number / 1024 ** 3).toFixed(2)} GiB`;
  if (number >= 1024 ** 2) return `${(number / 1024 ** 2).toFixed(1)} MiB`;
  return `${Math.ceil(number / 1024)} KiB`;
}
function date(value, options = {}) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "Ukjend dato"
    : parsed.toLocaleString("nn-NO", {
        day: "numeric",
        month: "short",
        year: "numeric",
        ...options,
      });
}
function tell(message, error = false) {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = !message;
  notice.setAttribute("role", error ? "alert" : "status");
}
function inlineError(id, message, success = false) {
  const node = byId(id);
  if (!node) return;
  node.textContent = message;
  node.className = success ? "success-inline" : "error-inline";
  node.setAttribute("role", success ? "status" : "alert");
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new Error(
      "Får ikkje kontakt med serveren. Kontroller at Python-serveren køyrer, og prøv på nytt.",
    );
  }
  const content = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = content.detail;
    const message = Array.isArray(detail)
      ? detail
          .map(
            (item) =>
              `${item.loc?.slice(1).join(".") || "Inndata"}: ${item.msg}`,
          )
          .join(" · ")
      : detail;
    throw new Error(
      typeof message === "string"
        ? message
        : `Førespurnaden feila (${response.status}). Prøv på nytt.`,
    );
  }
  return content;
}
function badge(job) {
  return element(
    "span",
    `badge ${job?.status || ""}`,
    statusNames[job?.status] || "Ikkje eksportert",
  );
}
function loading() {
  app.innerHTML = '<p class="loading-state">Lastar …</p>';
  app.setAttribute("aria-busy", "true");
}
function ready() {
  app.setAttribute("aria-busy", "false");
}
function projectPath(id) {
  return `#editor/${encodeURIComponent(id)}`;
}

function home() {
  app.innerHTML = `
    <section class="home-hero" aria-labelledby="home-title">
      <div><p class="eyebrow">FRÅ RÅMATERIALE TIL FERDIG VIDEO</p>
        <h1 id="home-title">Du har klippa.<br><span class="gradient-text">Skap samanhengen.</span></h1>
        <p class="hero-description">Samle videoklippa dine, vel lengd og uttrykk, og set dei saman til éin video. Ei oversiktleg arbeidsflate frå første opplasting til ferdig eksport.</p>
        <div class="button-row"><a class="button" href="#prosjekt">${icons("plus")} Lag din første video</a><a class="button subtle" href="#slik-fungerer-det">Sjå arbeidsflyten ${icons("arrow")}</a></div>
        <p class="hero-note">${icons("check")} Lokal demonstrasjon. Ingen konto eller betaling nødvendig.</p>
      </div>
      <div class="flow-preview" aria-label="Illustrasjon av arbeidsflyten, ikkje ein ferdig video">
        <div class="flow-top"><span>DI NESTE HISTORIE</span><span class="pill">Frå idé til eksport</span></div>
        <div class="preview-frame"><span class="preview-play">${icons("play")}</span><span class="preview-caption">DINE KLIPP. DITT UTTRYKK.</span></div>
        <div class="timeline" aria-hidden="true"><span>${icons("film")} Opplasting</span><span>${icons("sliders")} Redigering</span><span>${icons("check")} Eksport</span></div>
        <div class="flow-caption"><span>Illustrert arbeidsflyt</span><b>16:9 · 9:16 · 1:1</b></div>
      </div>
    </section>
    <section id="slik-fungerer-det" aria-labelledby="workflow-title"><div class="section-title"><h2 id="workflow-title">Ein enkel veg til ferdig video</h2><p>Mindre friksjon. Meir skaparglede.</p></div>
      <div class="feature-grid">
        <article class="feature"><div class="feature-icon">${icons("upload")}</div><h3>01 · Samle materialet</h3><p>Opprett eit prosjekt og last opp eitt eller fleire videoklipp. Prosjektet blir lagra medan du jobbar.</p></article>
        <article class="feature"><div class="feature-icon">${icons("sliders")}</div><h3>02 · Finn uttrykket</h3><p>Vel lengd i minutt og sekund, biletformat, kantar eller beskjering og ein redigeringsstil.</p></article>
        <article class="feature"><div class="feature-icon">${icons("download")}</div><h3>03 · Sjå og last ned</h3><p>Følg den faktiske videobehandlinga. Sjå resultatet her og last ned ei MP4-fil når ho er ferdig.</p></article>
      </div>
      <div class="info-box home-limit"><strong>Kva fungerer i denne versjonen?</strong> Ekte klipping og samansetjing med FFmpeg, opptil 10 minutt per eksport. Enkel modus bruker starten av kvart klipp proporsjonalt i opplastingsrekkefølgje. AI-analyse og teksting er ikkje konfigurert. Planlegging lagrar berre lokale planar; automatisk publisering og betaling er ikkje aktive.</div>
    </section>`;
}

async function projects(token) {
  const items = await api("/projects");
  if (token !== state.route) return;
  app.innerHTML = `<div class="page-heading"><div><p class="eyebrow">DITT ARBEIDSOMRÅDE</p><h1>Mine prosjekt</h1><p class="description">Ein idé, nokre klipp og ein stad å byrje. Prosjekta dine blir lagra på denne maskina.</p></div><span class="pill" id="project-count"></span></div>
    <form id="create-project" class="panel create-project"><div class="field"><label for="project-name">Namn på nytt prosjekt</label><input id="project-name" name="name" required maxlength="100" placeholder="Til dømes: Ein dag på skulen" autocomplete="off"></div><button class="button" type="submit">${icons("plus")} Opprett prosjekt</button></form>
    <p id="create-error" class="error-inline" role="alert"></p><div class="project-grid" id="project-grid"></div>`;
  byId("project-count").textContent = `${items.length} prosjekt`;
  const grid = byId("project-grid");
  if (!items.length) {
    grid.className = "panel";
    grid.innerHTML = `<div class="empty-state">${icons("folder")}<h2>Her byrjar den neste videoen din</h2><p>Gje prosjektet eit namn i feltet over. Deretter kan du laste opp dei første klippa.</p></div>`;
  }
  for (const project of items) {
    const card = element("article", "project-card");
    card.innerHTML = `<div class="project-visual">${icons("film")}<span class="status-slot"></span></div><div class="project-body"><h2></h2><p class="project-meta"></p><p class="project-meta project-date"></p><div class="button-row"><a class="button secondary small">Opne prosjekt ${icons("arrow")}</a><button class="button subtle small delete-project" type="button">${icons("trash")}<span class="sr-only">Slett</span></button></div><div class="delete-slot"></div></div>`;
    card.querySelector("h2").textContent = project.name;
    card.querySelector(".project-meta").textContent =
      `${project.clip_count || 0} klipp · ${duration(project.source_duration)} kjeldemateriale`;
    card.querySelector(".project-date").textContent =
      `Oppretta ${date(project.created_at)}`;
    card.querySelector(".status-slot").append(badge(project.latest_job));
    card.querySelector("a").href = projectPath(project.id);
    card
      .querySelector(".delete-project")
      .setAttribute("aria-label", `Slett prosjektet ${project.name}`);
    card
      .querySelector(".delete-project")
      .addEventListener("click", () => confirmDeleteProject(card, project));
    grid.append(card);
  }
  byId("create-project").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const name = byId("project-name").value.trim();
    if (!name) {
      inlineError("create-error", "Skriv eit namn på prosjektet.");
      return;
    }
    button.disabled = true;
    try {
      const project = await api("/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      location.hash = projectPath(project.id);
    } catch (error) {
      inlineError("create-error", error.message);
      button.disabled = false;
    }
  });
}
function confirmDeleteProject(card, project) {
  const slot = card.querySelector(".delete-slot");
  slot.innerHTML =
    '<div class="confirm-row"><span>Slett prosjekt og videofiler?</span><button class="button danger small" type="button">Slett</button><button class="button subtle small" type="button">Avbryt</button></div>';
  const [confirm, cancel] = slot.querySelectorAll("button");
  confirm.focus();
  cancel.addEventListener("click", () => {
    slot.replaceChildren();
    card.querySelector(".delete-project").focus();
  });
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    try {
      await api(`/projects/${encodeURIComponent(project.id)}`, {
        method: "DELETE",
      });
      await route(false);
      tell("Prosjektet og dei tilhøyrande filene er sletta.");
    } catch (error) {
      tell(error.message, true);
      confirm.disabled = false;
    }
  });
}

async function editor(id, token) {
  const project = await api(`/projects/${encodeURIComponent(id)}`);
  if (token !== state.route) return;
  state.project = project;
  state.uploading = false;
  state.latest = project.latest_job;
  state.completed =
    project.jobs?.find((job) => job.status === "completed") || null;
  app.innerHTML = `<p class="breadcrumb"><a href="#prosjekt">Mine prosjekt</a><span>/</span>Arbeidsflate</p><div class="page-heading editor-heading"><div><p class="eyebrow">SKAP NOKO AV KLIPPA DINE</p><h1 id="editor-name"></h1><p class="description">Last opp, tilpass og eksporter. Du kan oppdatere sida utan å miste prosjektet eller jobbstatusen.</p></div><span class="pill">Enkel redigering utan AI</span></div>
    <div class="editor-layout"><div class="stack">
      <section class="panel" aria-labelledby="upload-heading"><div class="panel-header"><div class="panel-heading"><span class="section-number">01</span><h2 id="upload-heading">Videoklippa dine</h2></div><span id="clip-count" class="muted small"></span></div><div class="panel-body">
        <label class="upload-zone" id="drop-zone">${icons("upload")}<span class="upload-title">Vel videoklipp eller dra dei hit</span><span class="field-hint">MP4, MOV, MKV eller WebM · maks 200 MiB per fil<br>Opptil 12 klipp · maks 30 minutt per kjeldeklipp</span><input id="clip-upload" type="file" accept=".mp4,.mov,.mkv,.webm" multiple aria-label="Last opp videoklipp" data-lock></label>
        <p id="upload-message" class="upload-message" role="status" aria-live="polite"></p><div id="clip-list" class="clip-list"></div><div class="source-summary"><span id="source-duration"></span><strong id="source-storage"></strong></div><p id="upload-error" class="error-inline" role="alert"></p>
      </div></section>
      <section class="panel" aria-labelledby="settings-heading"><div class="panel-header"><div class="panel-heading"><span class="section-number">02</span><h2 id="settings-heading">Gje videoen ei retning</h2></div>${icons("sliders")}</div><div class="panel-body">
        <div class="info-box settings-note"><strong>Enkel modus, utan AI.</strong> Vi bruker starten av kvart klipp proporsjonalt, i opplastingsrekkefølgje. Roleg og profesjonell stil gir mjuke inn- og uttoningar; energisk stil bruker direkte klipp.</div>
        <form id="settings-form"><div class="field"><label for="description">Kva vil du lage?</label><textarea id="description" name="description" maxlength="3000" placeholder="Skildre tema, stemning og kva du vil fortelje …" data-lock></textarea><span class="field-hint">Instruksjonen blir lagra med eksporten. Enkel modus tolkar ikkje teksten og vel ikkje motiv med AI.</span></div>
        <div class="field"><label id="duration-label">Ønskt lengd</label><div class="duration-fields" role="group" aria-labelledby="duration-label"><label for="duration-minutes">Minutt<input id="duration-minutes" type="number" min="0" max="10" step="1" value="0" required data-lock></label><label for="duration-seconds">Sekund<input id="duration-seconds" type="number" min="0" max="59" step="1" value="30" required data-lock></label></div><span class="field-hint">Frå 1 sekund til 10 minutt. Videoen kan ikkje bli lengre enn kjeldematerialet.</span></div>
        <div id="duration-warning" class="info-box warning duration-warning" role="status" hidden></div>
        <div class="form-grid"><div class="field"><label for="aspect">Biletformat</label><select id="aspect" data-lock><option value="16:9">Liggjande · 16:9</option><option value="9:16">Ståande · 9:16</option><option value="1:1">Kvadratisk · 1:1</option></select></div><div class="field"><label for="fit">Tilpassing av biletet</label><select id="fit" data-lock><option value="pad">Bevar heile biletet med kantar</option><option value="crop">Beskjer for å fylle formatet</option></select></div><div class="field full"><label for="style">Redigeringsstil</label><select id="style" data-lock><option value="calm">Roleg · mjuke toningar</option><option value="energetic">Energisk · direkte klipp</option><option value="professional">Profesjonell · korte toningar</option></select></div></div>
        <label class="check-field disabled"><input type="checkbox" disabled><span>Legg til undertekstar<br><span class="field-hint">Ikkje tilgjengeleg: transkripsjon er ikkje konfigurert.</span></span></label>
        <button class="button wide" type="submit" id="render-button">${icons("play")} Lag video</button><p class="render-note">MP4 · H.264 · opptil 720p · 24 bilete/s. Éin videojobb blir behandla om gongen. Innstillingane blir lagra når du startar eksporten.</p><p id="render-error" class="error-inline" role="alert"></p></form>
      </div></section>
    </div><div class="stack"><section class="panel" aria-labelledby="preview-heading"><div class="panel-header"><div class="panel-heading"><span class="section-number">03</span><h2 id="preview-heading">Resultatet ditt</h2></div><span class="muted small">FØREHANDSVISING</span></div><div class="panel-body"><div id="result"></div><div id="job-status" aria-live="polite"></div><div id="history"></div></div></section>
      <section class="panel" aria-labelledby="schedule-heading"><div class="panel-header"><h2 id="schedule-heading">Planlegg neste steg</h2>${icons("calendar")}</div><div class="panel-body"><div id="schedule-placeholder" class="info-box">Lag ein ferdig video før du lagrar ein publiseringsplan. Automatisk publisering er ikkje aktiv.</div><div id="schedule-form-slot"></div></div></section>
    </div></div>`;
  byId("editor-name").textContent = project.name;
  const settings = project.settings || {};
  byId("description").value = project.description || "";
  const seconds = Number(settings.duration_seconds) || 30;
  byId("duration-minutes").value = Math.floor(seconds / 60);
  byId("duration-seconds").value = seconds % 60;
  byId("aspect").value = settings.aspect || "16:9";
  byId("fit").value = settings.fit || "pad";
  byId("style").value = settings.style || "professional";
  refreshClips();
  updateJob();
  updateResult();
  updateHistory();
  byId("clip-upload").addEventListener("change", (event) =>
    uploadFiles([...event.target.files], token),
  );
  const dropZone = byId("drop-zone");
  ["dragenter", "dragover"].forEach((name) =>
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add("drag-over");
    }),
  );
  ["dragleave", "drop"].forEach((name) =>
    dropZone.addEventListener(name, () =>
      dropZone.classList.remove("drag-over"),
    ),
  );
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    if (!state.uploading && !active(state.latest))
      uploadFiles([...event.dataTransfer.files], token);
  });
  byId("settings-form").addEventListener("input", updateControls);
  byId("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    inlineError("render-error", "");
    const settings = {
      description: byId("description").value,
      duration_seconds: requestedDuration(),
      aspect: byId("aspect").value,
      fit: byId("fit").value,
      style: byId("style").value,
      subtitles: false,
    };
    if (state.uploading || active(state.latest)) return;
    byId("render-button").disabled = true;
    try {
      const job = await api(`/projects/${encodeURIComponent(id)}/jobs`, {
        method: "POST",
        body: JSON.stringify(settings),
      });
      if (token !== state.route) return;
      state.latest = job;
      state.project.jobs = [state.latest, ...(state.project.jobs || [])];
      updateJob();
      updateHistory();
      updateControls();
      beginPolling(token);
      byId("job-status").scrollIntoView({ behavior: "auto", block: "nearest" });
    } catch (error) {
      inlineError("render-error", error.message);
      updateControls();
    }
  });
  updateControls();
  if (active(state.latest)) beginPolling(token);
}

function requestedDuration() {
  return (
    Number(byId("duration-minutes")?.value || 0) * 60 +
    Number(byId("duration-seconds")?.value || 0)
  );
}
function updateControls() {
  if (!byId("settings-form")) return;
  const locked = state.uploading || active(state.latest);
  document.querySelectorAll("[data-lock]").forEach((input) => {
    input.disabled = locked;
  });
  document.querySelectorAll(".delete-clip").forEach((button) => {
    button.disabled = locked;
  });
  const requested = requestedDuration();
  const source = Number(state.project.source_duration) || 0;
  const max = state.config?.limits?.max_export_seconds || 600;
  let message = "";
  if (state.project.clips.length && requested > source + 0.01)
    message = `Du ønskjer ${duration(requested)}, men har berre ${duration(source)} kjeldemateriale. Last opp fleire klipp eller vel ei kortare lengd.`;
  else if (requested > max)
    message =
      "Førsteversjonen støttar maksimalt 10 minutt per eksport. Vel ei kortare lengd.";
  else if (requested < 1) message = "Vel minst 1 sekund.";
  const warning = byId("duration-warning");
  warning.textContent = message;
  warning.hidden = !message;
  byId("render-button").disabled =
    locked ||
    !state.project.clips.length ||
    Boolean(message) ||
    !state.config?.ffmpeg_available;
  if (!state.config?.ffmpeg_available)
    inlineError(
      "render-error",
      "FFmpeg er ikkje tilgjengeleg. Sjå README for installasjon før du behandlar video.",
    );
}
function refreshClips() {
  if (!byId("clip-list")) return;
  const clips = state.project.clips || [];
  const list = byId("clip-list");
  list.replaceChildren();
  byId("clip-count").textContent =
    `${clips.length} / ${state.config?.limits?.max_files || 12}`;
  byId("source-duration").textContent =
    `${duration(state.project.source_duration)} kjeldemateriale`;
  byId("source-storage").textContent = bytes(
    clips.reduce((sum, clip) => sum + Number(clip.size_bytes || 0), 0),
  );
  clips.forEach((clip) => {
    const row = element("div", "clip-item");
    row.innerHTML = `<span class="clip-symbol">${icons("film")}</span><span class="clip-info"><span class="clip-name"></span><span class="clip-meta"></span></span><button class="icon-button delete-clip" type="button">${icons("trash")}</button>`;
    row.querySelector(".clip-name").textContent = clip.original_name;
    row.querySelector(".clip-meta").textContent =
      `${duration(clip.duration)} · ${clip.width} × ${clip.height} · ${bytes(clip.size_bytes)}`;
    const remove = row.querySelector("button");
    remove.setAttribute("aria-label", `Fjern klippet ${clip.original_name}`);
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await api(
          `/projects/${encodeURIComponent(state.project.id)}/clips/${encodeURIComponent(clip.id)}`,
          { method: "DELETE" },
        );
        await reloadProject();
        refreshClips();
        updateControls();
      } catch (error) {
        inlineError("upload-error", error.message);
        remove.disabled = false;
      }
    });
    list.append(row);
  });
}
async function reloadProject() {
  const id = state.project.id;
  const project = await api(`/projects/${encodeURIComponent(id)}`);
  if (state.project?.id === id) state.project = project;
}
async function uploadFiles(files, token) {
  if (!files.length || state.uploading || active(state.latest)) return;
  state.uploading = true;
  inlineError("upload-error", "");
  updateControls();
  const errors = [];
  let uploaded = 0;
  for (let index = 0; index < files.length; index++) {
    if (token !== state.route) break;
    const file = files[index];
    byId("upload-message").textContent =
      `Lastar opp ${index + 1} av ${files.length}: ${file.name}. Serveren kontrollerer videoen før han blir lagra.`;
    if (file.size > (state.config?.limits?.max_upload_mb || 200) * 1024 ** 2) {
      errors.push(`${file.name}: fila er større enn 200 MiB.`);
      continue;
    }
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/projects/${encodeURIComponent(state.project.id)}/clips`, {
        method: "POST",
        body: form,
      });
      uploaded++;
    } catch (error) {
      errors.push(`${file.name}: ${error.message}`);
    }
  }
  if (token !== state.route) return;
  try {
    await reloadProject();
    if (token !== state.route) return;
    refreshClips();
  } catch (error) {
    errors.push(error.message);
  }
  if (token !== state.route) return;
  state.uploading = false;
  byId("clip-upload").value = "";
  byId("upload-message").textContent =
    `${uploaded} av ${files.length} klipp lasta opp og kontrollert.`;
  inlineError("upload-error", errors.join(" "));
  updateControls();
}
function updateJob() {
  const slot = byId("job-status");
  if (!slot) return;
  slot.replaceChildren();
  const job = state.latest;
  if (!job) return;
  const box = element("div", "job-box");
  const header = element("div", "job-heading");
  header.append(element("strong", "", "Siste videojobb"), badge(job));
  box.append(header);
  if (active(job)) {
    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = Number(job.progress) || 0;
    progress.setAttribute("aria-label", "Faktisk framdrift for videojobben");
    box.append(progress);
  }
  box.append(element("p", "job-stage", job.stage || statusNames[job.status]));
  if (job.status === "failed")
    box.append(
      element(
        "p",
        "error-inline",
        job.error ||
          "Videobehandlinga feila. Kontroller klippa og prøv på nytt.",
      ),
    );
  if (job.status === "completed")
    box.append(
      element(
        "p",
        "job-stage",
        "Eksporten er ferdig. Sjå gjennom resultatet før du lagrar ein publiseringsplan.",
      ),
    );
  slot.append(box);
}
function updateResult() {
  const slot = byId("result");
  if (!slot) return;
  const job = state.completed;
  if (!job) {
    slot.innerHTML = `<div class="preview-empty">${icons("play")}<h3>Her kjem videoen din</h3><p>Last opp klipp og start behandlinga. Det ferdige resultatet kan spelast av og lastast ned her.</p></div>`;
    return;
  }
  if (slot.dataset.job === job.id) return;
  slot.dataset.job = job.id;
  slot.replaceChildren();
  const video = document.createElement("video");
  video.className = "video-preview";
  video.controls = true;
  video.preload = "metadata";
  video.playsInline = true;
  video.src = `/api/jobs/${encodeURIComponent(job.id)}/video`;
  video.setAttribute("aria-label", "Førehandsvising av den ferdige videoen");
  const metadata = element("div", "result-meta");
  metadata.append(
    element("span", "", duration(job.output_duration)),
    element("span", "", `${job.output_width} × ${job.output_height}`),
    element("span", "", bytes(job.output_bytes)),
  );
  const download = element("a", "button secondary wide");
  download.innerHTML = `${icons("download")} Last ned MP4`;
  download.href = `/api/jobs/${encodeURIComponent(job.id)}/download`;
  download.setAttribute("download", "");
  slot.append(video, metadata, download);
  scheduleForm(job);
}
function updateHistory() {
  const slot = byId("history");
  if (!slot) return;
  const jobs = state.project.jobs || [];
  slot.replaceChildren();
  if (jobs.length < 2) return;
  const details = element("details", "job-history");
  details.append(
    element("summary", "", `Tidlegare eksportar (${jobs.length - 1})`),
  );
  for (const job of jobs.slice(1)) {
    const row = element("div", "history-row");
    row.append(
      element(
        "span",
        "",
        date(job.created_at, { hour: "2-digit", minute: "2-digit" }),
      ),
      badge(job),
    );
    if (job.status === "completed") {
      const link = element("a", "", "Last ned");
      link.href = `/api/jobs/${encodeURIComponent(job.id)}/download`;
      row.append(link);
    }
    details.append(row);
  }
  slot.append(details);
}
function beginPolling(token) {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    if (token !== state.route || !state.latest) return;
    try {
      const job = await api(`/jobs/${encodeURIComponent(state.latest.id)}`);
      if (token !== state.route) return;
      state.latest = job;
      inlineError("render-error", "");
      state.project.jobs = (state.project.jobs || []).map((item) =>
        item.id === job.id ? job : item,
      );
      updateJob();
      updateControls();
      if (job.status === "completed") {
        state.completed = job;
        updateResult();
        updateHistory();
      }
      if (active(job)) beginPolling(token);
    } catch (error) {
      if (token === state.route) {
        inlineError(
          "render-error",
          `${error.message} Status blir prøvd igjen.`,
        );
        beginPolling(token);
      }
    }
  }, 1500);
}
function scheduleForm(job) {
  const slot = byId("schedule-form-slot");
  if (!slot) return;
  byId("schedule-placeholder").hidden = true;
  slot.innerHTML = `<form id="schedule-form" class="schedule-panel"><div class="info-box"><strong>Berre ein lokal plan.</strong> Ingen konto er tilkopla. Videoen blir ikkje publisert automatisk, og må lastast opp manuelt til den valde kanalen.</div><div class="field"><label for="channel">Kanal du vil publisere på</label><select id="channel"><option value="YouTube">YouTube</option><option value="Instagram">Instagram</option><option value="TikTok">TikTok</option></select></div><div class="field"><label for="scheduled-at">Dato og klokkeslett</label><input id="scheduled-at" type="datetime-local" required><span id="timezone-label" class="field-hint"></span></div><label class="check-field"><input id="approved" type="checkbox" required><span>Eg har sett gjennom den ferdige videoen og godkjenner video, kanal og tidspunkt for denne lokale planen.</span></label><button class="button secondary wide" type="submit">${icons("calendar")} Lagre lokal plan</button><p id="schedule-message" role="status"></p></form>`;
  byId("timezone-label").textContent =
    `Tidssone: ${timezone}. Tidspunktet blir lagra med tidssone.`;
  byId("schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const value = byId("scheduled-at").value;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime()) || parsed <= new Date()) {
      inlineError("schedule-message", "Vel eit gyldig tidspunkt i framtida.");
      return;
    }
    // Avvis datoar som nettlesaren flyttar automatisk ved overgang til sommartid.
    const [day, time] = value.split("T");
    const [year, month, dateNumber] = day.split("-").map(Number);
    const [hour, minute] = time.split(":").map(Number);
    if (
      parsed.getFullYear() !== year ||
      parsed.getMonth() !== month - 1 ||
      parsed.getDate() !== dateNumber ||
      parsed.getHours() !== hour ||
      parsed.getMinutes() !== minute
    ) {
      inlineError(
        "schedule-message",
        "Klokkeslettet finst ikkje i denne tidssona på grunn av sommartid. Vel eit anna tidspunkt.",
      );
      return;
    }
    button.disabled = true;
    try {
      await api("/schedules", {
        method: "POST",
        body: JSON.stringify({
          job_id: job.id,
          channel: byId("channel").value,
          scheduled_at: parsed.toISOString(),
          timezone,
          approved: byId("approved").checked,
        }),
      });
      inlineError(
        "schedule-message",
        "Planen er lagra. Du finn han under Publiseringsplanar. Automatisk publisering er ikkje aktiv.",
        true,
      );
    } catch (error) {
      inlineError("schedule-message", error.message);
    } finally {
      button.disabled = false;
    }
  });
}

async function calendar(token) {
  const items = await api("/schedules");
  if (token !== state.route) return;
  app.innerHTML = `<div class="page-heading"><div><p class="eyebrow">HALD OVERSIKTA</p><h1>Publiseringsplanar</h1><p class="description">Samle planar for ferdige videoar. Kanalar og tidspunkt er hugselappar for manuell publisering.</p></div><a class="button secondary" href="#prosjekt">${icons("folder")} Gå til prosjekta</a></div><div class="info-box warning"><strong>Automatisk publisering er ikkje aktiv.</strong> Ingen sosiale kontoar er tilkopla. Desse planane blir berre lagra lokalt; systemet sender ingen videoar til sosiale medium.</div><div id="schedule-list" class="calendar-list"></div>`;
  const list = byId("schedule-list");
  if (!items.length) {
    list.className = "panel calendar-list";
    list.innerHTML = `<div class="empty-state">${icons("calendar")}<h2>Plass til den neste videoen din</h2><p>Når ein video er ferdig, kan du godkjenne resultatet og lagre ein plan frå arbeidsflata.</p><a class="button secondary" href="#prosjekt">Opne prosjekta ${icons("arrow")}</a></div>`;
  }
  const sorted = [...items].sort(
    (a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at),
  );
  for (const item of sorted) {
    const card = element("article", "panel schedule-card");
    const when = new Date(item.scheduled_at);
    card.innerHTML = `<div class="schedule-date"><strong></strong><span></span></div><div class="schedule-info"><h2></h2><p></p><span class="badge">Lokal plan · manuell publisering</span></div><div class="schedule-actions"><a class="button secondary small">Opne video</a><button class="button subtle small" type="button">${icons("trash")} Slett plan</button></div>`;
    card.querySelector(".schedule-date strong").textContent =
      when.toLocaleDateString("nn-NO", {
        day: "numeric",
        timeZone: item.timezone,
      });
    card.querySelector(".schedule-date span").textContent =
      when.toLocaleDateString("nn-NO", {
        month: "short",
        timeZone: item.timezone,
      });
    card.querySelector("h2").textContent = item.project_name;
    card.querySelector(".schedule-info p").textContent =
      `${item.channel} · ${date(item.scheduled_at, { hour: "2-digit", minute: "2-digit", timeZone: item.timezone })} · ${item.timezone}`;
    card.querySelector("a").href = projectPath(item.project_id);
    card.querySelector("button").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`/schedules/${encodeURIComponent(item.id)}`, {
          method: "DELETE",
        });
        await route(false);
        tell("Den lokale planen er sletta.");
      } catch (error) {
        tell(error.message, true);
        button.disabled = false;
      }
    });
    list.append(card);
  }
}
async function subscription(token) {
  const usage = await api("/usage");
  if (token !== state.route) return;
  app.innerHTML = `<div class="page-heading"><div><p class="eyebrow">PLASS TIL Å VEKSE</p><h1>Abonnement og bruk</h1><p class="description">Først får vi arbeidsflyten til å fungere. Abonnementa under viser korleis ei framtidig teneste kan bli prisa.</p></div><span class="pill">Lokal demonstrasjon</span></div><div class="stats-grid"><section class="stat-card"><p class="stat-label">Brukt lagring</p><p id="storage-value" class="stat-value"></p><p id="storage-caption" class="stat-caption"></p><div class="usage-track"><span id="storage-bar"></span></div></section><section class="stat-card"><p class="stat-label">Video eksportert</p><p id="rendered-value" class="stat-value"></p><p class="stat-caption">Sum ferdige eksportar i dette systemet</p></section><section class="stat-card"><p class="stat-label">Lengd per eksport</p><p id="export-value" class="stat-value"></p><p class="stat-caption">Gjeldande grense i førsteversjonen</p></section></div><div class="info-box"><strong>Dette er ikkje eit aktivt abonnement.</strong> Brukstala kjem frå dei lokale prosjekta dine. Ingen betaling, månadsfakturering eller kjøpsfunksjon er aktivert.</div><div class="section-title"><h2>Moglege abonnement</h2><p>Alle prisar og grenser er døme.</p></div><div class="pricing-grid"><article class="pricing-card"><h3>Start</h3><p class="price">99 kr <span>/ månad</span></p><ul><li>30 videominutt per månad</li><li>5 GB lagring</li><li>For små prosjekt og nye idear</li></ul><span class="pill">Døme · ikkje til sals</span></article><article class="pricing-card highlight"><h3>Skapar</h3><p class="price">249 kr <span>/ månad</span></p><ul><li>120 videominutt per månad</li><li>25 GB lagring</li><li>For jamnleg videoproduksjon</li></ul><span class="pill">Døme · ikkje til sals</span></article><article class="pricing-card"><h3>Studio</h3><p class="price">599 kr <span>/ månad</span></p><ul><li>360 videominutt per månad</li><li>100 GB lagring</li><li>For fleire og større prosjekt</li></ul><span class="pill">Døme · ikkje til sals</span></article></div><p class="muted small pricing-note">Innlogging, månadskvotar og betaling må implementerast før tenesta kan få ekte kundar. Eventuell betaling skal først prøvast i testmodus.</p>`;
  byId("storage-value").textContent = bytes(usage.storage_bytes);
  byId("storage-caption").textContent =
    `av ${bytes(usage.storage_limit_bytes)} lokal lagringsgrense`;
  byId("storage-bar").style.width =
    `${Math.min(100, Math.max(0, (usage.storage_bytes / usage.storage_limit_bytes) * 100))}%`;
  byId("rendered-value").textContent = duration(usage.rendered_seconds);
  byId("export-value").textContent =
    `${Math.floor(usage.max_export_seconds / 60)} min`;
}

async function route(focus = true) {
  const token = ++state.route;
  clearTimeout(state.timer);
  const hash = location.hash.slice(1) || "heim";
  const [page, id] = hash.split("/");
  // Den interne framsidelenkja skal rulle til ei seksjon utan å miste framsida.
  if (page === "slik-fungerer-det") {
    home();
    ready();
    requestAnimationFrame(() => byId("slik-fungerer-det")?.scrollIntoView());
    return;
  }
  const activePage = page === "editor" ? "prosjekt" : page;
  document.querySelectorAll("[data-route]").forEach((link) => {
    if (link.dataset.route === activePage)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  notice.hidden = true;
  loading();
  try {
    if (page === "heim") home();
    else if (page === "prosjekt") await projects(token);
    else if (page === "editor" && id)
      await editor(decodeURIComponent(id), token);
    else if (page === "kalender") await calendar(token);
    else if (page === "abonnement") await subscription(token);
    else {
      app.innerHTML =
        '<div class="empty-state"><h1>Sida finst ikkje</h1><p>Vel ei side frå menyen for å halde fram.</p><a class="button" href="#heim">Til framsida</a></div>';
    }
  } catch (error) {
    if (token !== state.route) return;
    app.innerHTML =
      '<div class="empty-state panel"><h1>Vi fekk ikkje lasta inn sida</h1><p id="route-error"></p><div class="button-row"><button id="retry-route" class="button" type="button">Prøv igjen</button><a class="button secondary" href="#prosjekt">Mine prosjekt</a></div></div>';
    byId("route-error").textContent = error.message;
    byId("retry-route").addEventListener("click", () => route(false));
  }
  if (token !== state.route) return;
  ready();
  if (focus) {
    byId("innhald").focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
  }
}
document.querySelector(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  byId("innhald").focus({ preventScroll: true });
  byId("innhald").scrollIntoView({ behavior: "auto" });
});
window.addEventListener("hashchange", () => route());
(async function start() {
  try {
    state.config = await api("/config");
  } catch (error) {
    tell(error.message, true);
  }
  await route(false);
})();
