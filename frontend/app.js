/* =========================================================
   AI Study Assistant — app.js
   ---------------------------------------------------------
   This file does three things:
   1. Keeps track of the notes the student has uploaded (in a
      plain JS array — no browser storage, since this is a
      single-session demo and data really lives in DynamoDB/S3)
   2. Calls the API Gateway endpoints (upload / summarize / ask
      / quiz+flashcards)
   3. Updates the HTML to show results

   IMPORTANT: replace API_BASE_URL below with YOUR real
   Invoke URL from API Gateway before this will work.
   ========================================================= */

const API_BASE_URL = "https://ls6i0fxhpa.execute-api.us-east-1.amazonaws.com/dev";

// In-memory list of notes for the current session. Each entry looks like:
// { note_id, filename }
// We rebuild this from upload responses - we don't persist it across a
// page reload, since there's no "list my notes" endpoint yet.
let notes = [];
let selectedNoteId = null;

// ---------- Element references ----------
const studentIdInput = document.getElementById("studentIdInput");
const catalog = document.getElementById("catalog");
const catalogEmpty = document.getElementById("catalogEmpty");
const noteCount = document.getElementById("noteCount");

const uploadPanel = document.getElementById("uploadPanel");
const notePanel = document.getElementById("notePanel");
const showUploadBtn = document.getElementById("showUploadBtn");

const dropzone = document.getElementById("dropzone");
const dropzoneText = document.getElementById("dropzoneText");
const fileInput = document.getElementById("fileInput");
const uploadForm = document.getElementById("uploadForm");
const uploadStatus = document.getElementById("uploadStatus");

const noteTitle = document.getElementById("noteTitle");
const noteEyebrow = document.getElementById("noteEyebrow");

const tabs = document.querySelectorAll(".tab");
const tabPanels = document.querySelectorAll(".tab-panel");

const generateSummaryBtn = document.getElementById("generateSummaryBtn");
const summaryResult = document.getElementById("summaryResult");

const askForm = document.getElementById("askForm");
const questionInput = document.getElementById("questionInput");
const qaLog = document.getElementById("qaLog");

const generateQuizBtn = document.getElementById("generateQuizBtn");
const quizResult = document.getElementById("quizResult");

const generateFlashcardsBtn = document.getElementById("generateFlashcardsBtn");
const flashcardsResult = document.getElementById("flashcardsResult");

// A small rotating set of tab colors so the index-card catalog doesn't
// look flat when there are several notes.
const TAB_COLORS = ["#e07a5f", "#3d84a8", "#6a994e", "#b56576", "#8b6bb0"];

// ---------- Helpers ----------

function getStudentId() {
  return studentIdInput.value.trim() || "shreya01";
}

/** Wraps fetch with JSON handling and consistent error surfacing. */
async function apiPost(path, body) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    // Lambda's error responses always look like { "error": "..." } -
    // surface that message directly rather than a generic "failed" string.
    throw new Error(data.error || `Request failed (${res.status})`);
  }

  return data;
}

/** Reads a File object and resolves to its Base64 content (no data: prefix). */
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // reader.result looks like "data:text/plain;base64,AAAA..." -
      // the backend only wants the part after the comma.
      const base64 = reader.result.split(",")[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ---------- Sidebar catalog rendering ----------

function renderCatalog() {
  noteCount.textContent = notes.length;
  catalogEmpty.hidden = notes.length > 0;

  // Clear existing cards (but keep the empty-state paragraph in the DOM)
  catalog.querySelectorAll(".index-card").forEach((el) => el.remove());

  notes.forEach((note, index) => {
    const card = document.createElement("div");
    card.className = "index-card" + (note.note_id === selectedNoteId ? " selected" : "");
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.dataset.noteId = note.note_id;

    const tabColor = TAB_COLORS[index % TAB_COLORS.length];

    card.innerHTML = `
      <span class="index-card-tab" style="background:${tabColor}"></span>
      <span class="index-card-name">${escapeHtml(note.filename)}</span>
      <span class="index-card-meta">${note.note_id.slice(0, 8)}</span>
    `;

    card.addEventListener("click", () => selectNote(note.note_id));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") selectNote(note.note_id);
    });

    catalog.appendChild(card);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- Panel switching ----------

function showUploadPanel() {
  selectedNoteId = null;
  renderCatalog();
  uploadPanel.hidden = false;
  notePanel.hidden = true;
}

function selectNote(noteId) {
  selectedNoteId = noteId;
  const note = notes.find((n) => n.note_id === noteId);
  if (!note) return;

  renderCatalog();
  uploadPanel.hidden = true;
  notePanel.hidden = false;

  noteEyebrow.textContent = `NOTE · ${noteId.slice(0, 8)}`;
  noteTitle.textContent = note.filename;

  // Reset each tab's result area whenever a different note is opened,
  // so results from one note never appear to belong to another.
  summaryResult.innerHTML = `<p class="result-placeholder">No summary yet — generate one above.</p>`;
  qaLog.innerHTML = `<p class="result-placeholder">Your questions and answers will appear here.</p>`;
  quizResult.innerHTML = `<p class="result-placeholder">No quiz yet — generate one above.</p>`;
  flashcardsResult.innerHTML = `<p class="result-placeholder">No flashcards yet — generate some above.</p>`;

  switchTab("summary");
}

function switchTab(tabName) {
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === tabName));
  tabPanels.forEach((p) => p.classList.toggle("active", p.id === `tab-${tabName}`));
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

showUploadBtn.addEventListener("click", showUploadPanel);

// ---------- Upload flow ----------

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("drag-over");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag-over");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    updateDropzoneText();
  }
});
fileInput.addEventListener("change", updateDropzoneText);

function updateDropzoneText() {
  const file = fileInput.files[0];
  dropzoneText.textContent = file ? file.name : "Choose a .txt file or drop it here";
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  const file = fileInput.files[0];
  if (!file) {
    setUploadStatus("Please choose a .txt file first.", "error");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".txt")) {
    setUploadStatus("Only .txt files are supported right now.", "error");
    return;
  }

  const submitBtn = document.getElementById("uploadSubmitBtn");
  submitBtn.disabled = true;
  setUploadStatus("Uploading…", "");

  try {
    const base64Content = await fileToBase64(file);

    const result = await apiPost("/upload", {
      student_id: getStudentId(),
      filename: file.name,
      file_content_base64: base64Content,
    });

    notes.push({ note_id: result.note_id, filename: file.name });
    setUploadStatus("Uploaded! Opening your note…", "success");

    fileInput.value = "";
    updateDropzoneText();

    setTimeout(() => selectNote(result.note_id), 500);
  } catch (err) {
    setUploadStatus(err.message, "error");
  } finally {
    submitBtn.disabled = false;
  }
});

function setUploadStatus(message, type) {
  uploadStatus.textContent = message;
  uploadStatus.className = "form-status" + (type ? ` ${type}` : "");
}

// ---------- Summary ----------

generateSummaryBtn.addEventListener("click", async () => {
  if (!selectedNoteId) return;

  generateSummaryBtn.disabled = true;
  summaryResult.innerHTML = `<p class="result-placeholder">Generating summary…</p>`;

  try {
    const result = await apiPost("/summarize", {
      student_id: getStudentId(),
      note_id: selectedNoteId,
    });

    // The Lambda returns bullet points as plain text separated by
    // newlines (each line starting with "-"). Turn that into a real
    // <ul> so it reads cleanly.
    const bullets = result.summary
      .split("\n")
      .map((line) => line.replace(/^[-•]\s*/, "").trim())
      .filter(Boolean);

    summaryResult.innerHTML = `<ul>${bullets.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>`;
  } catch (err) {
    summaryResult.innerHTML = `<p class="result-placeholder">Couldn't generate a summary: ${escapeHtml(err.message)}</p>`;
  } finally {
    generateSummaryBtn.disabled = false;
  }
});

// ---------- Ask ----------

askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedNoteId) return;

  const question = questionInput.value.trim();
  if (!question) return;

  // Clear the placeholder the first time a question is asked.
  if (qaLog.querySelector(".result-placeholder")) qaLog.innerHTML = "";

  const pendingItem = document.createElement("div");
  pendingItem.className = "qa-item";
  pendingItem.innerHTML = `<p class="qa-question">${escapeHtml(question)}</p><p class="qa-answer">Thinking…</p>`;
  qaLog.prepend(pendingItem);

  questionInput.value = "";

  try {
    const result = await apiPost("/ask", {
      student_id: getStudentId(),
      note_id: selectedNoteId,
      question,
    });
    pendingItem.querySelector(".qa-answer").textContent = result.answer;
  } catch (err) {
    pendingItem.querySelector(".qa-answer").textContent = `Couldn't get an answer: ${err.message}`;
  }
});

// ---------- Quiz ----------

generateQuizBtn.addEventListener("click", async () => {
  if (!selectedNoteId) return;

  generateQuizBtn.disabled = true;
  quizResult.innerHTML = `<p class="result-placeholder">Generating quiz…</p>`;

  try {
    const result = await apiPost("/quiz", {
      student_id: getStudentId(),
      note_id: selectedNoteId,
    });

    quizResult.innerHTML = result.quiz
      .map(
        (q, i) => `
        <div class="quiz-question">
          <p>${i + 1}. ${escapeHtml(q.question)}</p>
          <ol type="A">
            ${q.options.map((opt) => `<li>${escapeHtml(opt)}</li>`).join("")}
          </ol>
          <p class="quiz-answer">Answer: ${escapeHtml(q.correct_answer)}</p>
        </div>
      `
      )
      .join("");
  } catch (err) {
    quizResult.innerHTML = `<p class="result-placeholder">Couldn't generate a quiz: ${escapeHtml(err.message)}</p>`;
  } finally {
    generateQuizBtn.disabled = false;
  }
});

// ---------- Flashcards ----------

generateFlashcardsBtn.addEventListener("click", async () => {
  if (!selectedNoteId) return;

  generateFlashcardsBtn.disabled = true;
  flashcardsResult.innerHTML = `<p class="result-placeholder">Generating flashcards…</p>`;

  try {
    const result = await apiPost("/flashcards", {
      student_id: getStudentId(),
      note_id: selectedNoteId,
    });

    const grid = document.createElement("div");
    grid.className = "flashcard-grid";

    result.flashcards.forEach((card) => {
      const el = document.createElement("div");
      el.className = "flashcard";
      el.innerHTML = `
        <div class="flashcard-inner">
          <div class="flashcard-face flashcard-front">${escapeHtml(card.front)}</div>
          <div class="flashcard-face flashcard-back">${escapeHtml(card.back)}</div>
        </div>
      `;
      el.addEventListener("click", () => el.classList.toggle("flipped"));
      grid.appendChild(el);
    });

    flashcardsResult.innerHTML = "";
    flashcardsResult.appendChild(grid);
  } catch (err) {
    flashcardsResult.innerHTML = `<p class="result-placeholder">Couldn't generate flashcards: ${escapeHtml(err.message)}</p>`;
  } finally {
    generateFlashcardsBtn.disabled = false;
  }
});

// ---------- Initial render ----------
renderCatalog();