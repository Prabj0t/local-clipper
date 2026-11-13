"use strict";

const $ = (id) => document.getElementById(id);
const activeStatuses = new Set(["queued", "running", "cancelling"]);
let token = "";
let project = null;
let dirty = false;
let captionSnapshot = "";
let polling = false;
let busy = false;

function notice(message = "") {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function showProjectWarnings() {
  notice((project.document.warnings || []).filter(
    (message) => message !== "Highlights are estimates, not measured audience retention.",
  ).join("\n"));
}

async function api(path, method = "GET", data = undefined) {
  const options = { method, headers: {} };
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Clipper-Token"] = token;
    options.body = JSON.stringify(data ?? {});
  }
  const response = await fetch(`/api${path}`, options);
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail.map((item) => `${item.loc.join(".")}: ${item.msg}`).join("\n")
      : result.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return result;
}

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function setDirty() {
  dirty = true;
  $("save-project").textContent = "Save changes *";
}

async function listProjects() {
  const items = await api("/projects");
  $("projects").replaceChildren(...items.map((item) => {
    const button = element("button", item.name, project?.id === item.id ? "active" : "");
    button.disabled = busy;
    button.addEventListener("click", async () => {
      if (busy) return;
      if (dirty && !window.confirm("Discard your unsaved changes?")) return;
      busy = true;
      showJob();
      try {
        await openProject(item.id);
      } catch (error) {
        notice(error.message);
      } finally {
        busy = false;
        showJob();
      }
    });
    return button;
  }));
}

function refreshMode() {
  const gaming = $("layout-mode").value === "gaming";
  $("gaming-controls").hidden = !gaming;
  $("podcast-controls").hidden = gaming;
}

function addClipRow(clip) {
  const row = element("div", "", "clip-row");
  row.dataset.id = clip.id;
  row.clip = clip;
  const fields = element("div", "", "clip-fields");
  const selected = document.createElement("input");
  selected.type = "checkbox";
  selected.checked = clip.selected;
  selected.setAttribute("aria-label", "Include this clip");
  const title = document.createElement("input");
  title.value = clip.title;
  title.maxLength = 160;
  title.setAttribute("aria-label", "Clip title");
  const start = document.createElement("input");
  const end = document.createElement("input");
  for (const [node, value, label] of [[start, clip.start, "Start seconds"], [end, clip.end, "End seconds"]]) {
    node.type = "number";
    node.min = "0";
    node.step = "0.1";
    node.value = value;
    node.setAttribute("aria-label", label);
  }
  const remove = element("button", "×");
  remove.setAttribute("aria-label", "Remove clip");
  remove.addEventListener("click", () => { row.remove(); setDirty(); });
  fields.append(selected, title, start, end, remove);
  row.append(fields, element("p", `${clip.reason} · score ${clip.score.toFixed(2)}`, "help"));
  row.controls = { selected, title, start, end };
  $("clips").append(row);
}

function showOutputs() {
  const items = project.document.exports;
  $("export-list").replaceChildren();
  if (!items.length) $("export-list").append(element("p", "Your rendered clips will appear here.", "help"));
  for (const item of items) {
    const row = element("div", "", "output-row");
    row.append(element("strong", `${item.preview ? "Preview" : "Export"} · ${item.title}`));
    for (const [key, label] of [["video", "Open video"], ["subtitles", "SRT"]]) {
      const link = element("a", label);
      link.href = `/api/projects/${project.id}/artifacts/${item[key].split("/").map(encodeURIComponent).join("/")}`;
      link.target = "_blank";
      link.rel = "noopener";
      row.append(link);
    }
    $("export-list").append(row);
  }
}

function showJob() {
  const jobs = project?.jobs ?? [];
  const job = jobs[0];
  const active = job && activeStatuses.has(job.status);
  $("job-panel").hidden = !job;
  if (job) {
    $("job-stage").textContent = `${job.action.toUpperCase()} · ${job.stage} · ${job.status}`;
    $("job-progress").value = job.progress;
    $("job-error").textContent = job.error || "";
    $("cancel-job").hidden = !active;
    $("cancel-job").dataset.jobId = job.id;
  }
  document.querySelectorAll("#editor-view input, #editor-view select, #editor-view textarea, [data-action], #save-project, #delete-project, #add-clip, .clip-row button").forEach((node) => {
    node.disabled = Boolean(active || busy);
  });
  document.querySelectorAll("#new-project, #projects button").forEach((node) => {
    node.disabled = busy;
  });
}

function populateEditor() {
  const settings = project.settings;
  const layout = settings.layout;
  $("create-view").hidden = true;
  $("editor-view").hidden = false;
  $("editor-title").textContent = project.name;
  $("breadcrumb").textContent = `Workspace / ${project.name}`;
  for (const [id, value] of Object.entries({
    "min-duration": settings.min_duration, "max-duration": settings.max_duration,
    "max-clips": settings.max_clips ?? "", "min-score": settings.min_score,
    "speech-model": settings.speech_model, "layout-mode": layout.mode,
    "output-width": layout.width, "focus-x": layout.focus_x,
    "gameplay-fit": layout.gameplay_fit, "facecam-height": layout.facecam_height * 100,
  })) $(id).value = value;
  $("captions-enabled").checked = settings.captions;
  $("auto-export").checked = settings.auto_export;
  $("follow-face").checked = layout.follow_face;
  for (const key of ["x", "y", "width", "height"]) {
    $(`face-${key}`).value = layout.facecam ? layout.facecam[key] * 100 : "";
  }
  $("clips").replaceChildren();
  project.document.clips.forEach(addClipRow);
  captionSnapshot = project.document.cues.map((cue) => `${cue.start} | ${cue.end} | ${cue.text}`).join("\n");
  $("caption-editor").value = captionSnapshot;
  const sourceUrl = `/api/projects/${project.id}/source`;
  const sourceExists = project.kind === "local" || Boolean(project.document.download?.local_path);
  if (sourceExists && $("source-player").getAttribute("src") !== sourceUrl) $("source-player").src = sourceUrl;
  if (!sourceExists) {
    $("source-player").removeAttribute("src");
    $("source-player").load();
  }
  const media = project.document.media;
  $("source-summary").textContent = media
    ? `${media.width} × ${media.height} · ${(media.duration / 60).toFixed(1)} minutes · Original source remains unchanged`
    : "Inspect the source to read its duration and dimensions.";
  $("download-control").hidden = project.kind !== "youtube" || Boolean(project.document.download?.local_path);
  const remote = project.document.remote;
  const replay = project.document.download?.replay?.length
    ? project.document.download.replay : remote?.replay || [];
  $("replay-summary").textContent = project.kind === "local"
    ? "Local video: no audience replay data. Highlights are estimated from the recording."
    : replay.length
      ? `Public Most replayed data available for analysis (${replay.length} segments).`
      : remote || project.document.download
        ? "Public Most replayed data was not returned by the extractor. Highlights use estimated signals instead; this is not a processing error."
        : "Inspect the YouTube source to check whether public Most replayed data is returned.";
  $("download-estimate").textContent = project.kind === "youtube" && !project.document.download
    ? `Source: up to ${settings.source_height}p, complete recording. ${remote?.estimated_bytes ? `Estimated ${(remote.estimated_bytes / 1024 ** 3).toFixed(2)} GB.` : "Download size is not yet known."} No cookies or account credentials are used.`
    : "";
  dirty = false;
  $("save-project").textContent = "Save changes";
  refreshMode();
  showJob();
  showOutputs();
}

async function openProject(id) {
  project = await api(`/projects/${id}`);
  populateEditor();
  showProjectWarnings();
  await listProjects();
}

function collectEdits() {
  const settings = structuredClone(project.settings);
  settings.min_duration = Number($("min-duration").value);
  settings.max_duration = Number($("max-duration").value);
  settings.max_clips = $("max-clips").value === "" ? null : Number($("max-clips").value);
  settings.min_score = Number($("min-score").value);
  settings.speech_model = $("speech-model").value;
  settings.captions = $("captions-enabled").checked;
  settings.auto_export = $("auto-export").checked;
  Object.assign(settings.layout, {
    mode: $("layout-mode").value, width: Number($("output-width").value),
    focus_x: Number($("focus-x").value), follow_face: $("follow-face").checked,
    gameplay_fit: $("gameplay-fit").value, facecam_height: Number($("facecam-height").value) / 100,
  });
  const regionKeys = ["x", "y", "width", "height"];
  const filled = regionKeys.filter((key) => $(`face-${key}`).value !== "");
  if (filled.length && filled.length !== 4) throw new Error("Complete all four facecam rectangle fields.");
  settings.layout.facecam = filled.length
    ? Object.fromEntries(regionKeys.map((key) => [key, Number($(`face-${key}`).value) / 100]))
    : null;
  const clips = [...$("clips").children].map((row) => ({
    ...row.clip, selected: row.controls.selected.checked, title: row.controls.title.value,
    start: Number(row.controls.start.value), end: Number(row.controls.end.value),
  }));
  const text = $("caption-editor").value;
  const cues = text === captionSnapshot ? project.document.cues : text.split("\n").filter((line) => line.trim()).map((line, index) => {
    const [start, end, ...content] = line.split("|");
    if (!content.length || !Number.isFinite(Number(start)) || !Number.isFinite(Number(end))) {
      throw new Error(`Caption line ${index + 1} must be start | end | text.`);
    }
    return { start: Number(start), end: Number(end), text: content.join("|").trim(), words: [] };
  });
  return { revision: project.revision, settings, clips, cues };
}

async function save() {
  if (!dirty) return;
  const saved = await api(`/projects/${project.id}`, "PUT", collectEdits());
  project = { ...saved, jobs: project.jobs };
  populateEditor();
}

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const button = event.submitter;
  button.disabled = true;
  try {
    const created = await api("/projects", "POST", {
      name: $("project-name").value, source: $("project-source").value,
      settings: { layout: { mode: $("project-mode").value }, auto_export: $("project-workflow").value === "auto" },
    });
    await openProject(created.id);
  } catch (error) {
    notice(error.message);
  } finally {
    button.disabled = false;
  }
});

function showNewProject() {
  project = null;
  dirty = false;
  captionSnapshot = "";
  $("editor-view").hidden = true;
  $("create-view").hidden = false;
  $("breadcrumb").textContent = "Workspace / New project";
  $("source-player").removeAttribute("src");
  $("source-player").load();
  $("approve-download").checked = false;
  notice();
}

$("new-project").addEventListener("click", () => {
  if (busy) return;
  if (dirty && !window.confirm("Discard your unsaved changes?")) return;
  showNewProject();
});
$("delete-project").addEventListener("click", async () => {
  if (busy || !project) return;
  const { id, name, revision } = project;
  if (!window.confirm(
    `Delete "${name}" from this app?\n\nAll original videos, downloaded copies, clips, captions, caches, logs, and models will stay on disk. Unsaved edits will be discarded.`,
  )) return;
  busy = true;
  showJob();
  try {
    await api(`/projects/${id}?revision=${revision}`, "DELETE");
    if (project?.id === id) showNewProject();
    await listProjects();
    notice("Project removed from the app. No files were deleted.");
  } catch (error) {
    notice(error.message);
  } finally {
    busy = false;
    showJob();
  }
});
$("editor-view").addEventListener("input", (event) => {
  if (event.target.id !== "approve-download") setDirty();
});
$("layout-mode").addEventListener("change", refreshMode);
$("save-project").addEventListener("click", async () => {
  try { await save(); notice("Changes saved."); } catch (error) { notice(error.message); }
});
$("add-clip").addEventListener("click", () => {
  const duration = project.document.media?.duration;
  const current = $("source-player").currentTime || 0;
  const start = duration ? Math.min(current, Math.max(0, duration - 1)) : current;
  addClipRow({
    id: crypto.randomUUID(), title: "My highlight", start: Math.round(start * 10) / 10,
    end: Math.min(duration || start + 30, start + 30), selected: true, score: 0,
    reason: "Manually selected",
  });
  setDirty();
});
document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    showJob();
    try {
      const approved = $("approve-download").checked;
      await save();
      await api(`/projects/${project.id}/jobs`, "POST", {
        action: button.dataset.action, download_approved: approved,
      });
      $("approve-download").checked = false;
      project = await api(`/projects/${project.id}`);
      showJob();
      notice("Job queued. Downloads are limited to the approval given for this job.");
    } catch (error) {
      notice(error.message);
    } finally {
      busy = false;
      showJob();
    }
  });
});
$("cancel-job").addEventListener("click", async () => {
  try {
    await api(`/jobs/${$("cancel-job").dataset.jobId}/cancel`, "POST");
    project = await api(`/projects/${project.id}`);
    showJob();
  } catch (error) { notice(error.message); }
});

async function poll() {
  if (!project || polling || busy) return;
  polling = true;
  const id = project.id;
  try {
    const latest = await api(`/projects/${id}`);
    if (project?.id !== id) return;
    const changed = latest.revision !== project.revision;
    if (!dirty) {
      project = latest;
      if (changed) {
        populateEditor();
        showProjectWarnings();
      }
    } else project.jobs = latest.jobs;
    showJob();
  } catch (error) {
    if (project?.id === id && !busy) notice(`Connection unavailable: ${error.message}`);
  } finally {
    polling = false;
  }
}

async function boot() {
  try {
    const bootstrap = await api("/bootstrap");
    token = bootstrap.token;
    const capabilities = bootstrap.capabilities;
    for (const [name, ready] of [
      ["FFmpeg", capabilities.ffmpeg], ["FFprobe", capabilities.ffprobe],
      ["Speech runtime", capabilities.speech_runtime], ["Whisper weights", capabilities.speech_models.length > 0],
      ["YouTube runtime", capabilities.youtube_runtime], ["Face tracking", capabilities.vision_runtime && capabilities.face_model],
    ]) $("capabilities").append(element("span", `${ready ? "Ready" : "Missing"} · ${name}`, ready ? "ready" : "missing"));
    await listProjects();
    window.setInterval(poll, 1500);
  } catch (error) {
    notice(`Unable to connect to the local application. ${error.message}`);
  }
}
boot();
