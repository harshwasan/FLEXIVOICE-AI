/* FlexiLoans Voice Agent - client (auto-VAD mode) */

const scenarioId = document.body.dataset.scenario;

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  statusText: $("status-text"),
  transcript: $("transcript"),
  micBtn: $("mic-btn"),
  micLabel: $("mic-label"),
  micHint: $("mic-hint"),
  startBtn: $("start-btn"),
  endBtn: $("end-btn"),
  customerKv: $("customer-kv"),
  hints: $("hints"),
  optionsList: $("options-list"),
  scenarioDesc: $("scenario-desc"),
  outcomeContainer: $("outcome-container"),
  backendSelect: $("backend-select"),
  usagePill: $("usage-pill"),
};

const TARGET_SR = 16000;

// ---------- Voice-Activity-Detection (energy / RMS based) ----------
const VAD = {
  preBufferMs: 350,        // keep last X ms before speech detected
  startRms: 0.018,         // RMS above this = voice start
  keepRms:  0.010,         // RMS above this keeps voice "alive" (hysteresis)
  endSilenceMs: 900,       // silence for this long after voice = end of utterance
  minSpeechMs: 250,
  maxUtteranceMs: 20000,
  noiseFloor: 0.004,
};

// ---------- Top-level state machine ----------
const ST = {
  IDLE: "idle",
  AGENT_SPEAKING: "agent_speaking",
  LISTENING: "listening",
  USER_SPEAKING: "user_speaking",
  PROCESSING: "processing",
  ENDING: "ending",                   // user clicked End Call; waiting for outcome
};
let micState = ST.IDLE;

let ws = null;
let audioCtx = null;
let workletNode = null;
let mediaStream = null;
let micSource = null;
let inputSampleRate = 48000;

let preBuffer = [];          // ring buffer of Float32Arrays (last ~350ms)
let preBufferSamples = 0;
let activeBuffer = [];       // utterance frames after speech start
let speechStartedAt = 0;
let lastVoiceAt = 0;
let maxUtteranceTimer = null;

let agentPlaybackQueue = [];
let agentPlaying = false;
let _agentDoneFlag = false;

const state = { callActive: false, sessionId: null };

// ---------- Persona card rendering ----------

function fmtINR(n) {
  if (n == null) return "—";
  return "₹" + n.toLocaleString("en-IN");
}

async function loadScenario() {
  const r = await fetch(`/api/scenario/${scenarioId}`);
  if (!r.ok) {
    els.scenarioDesc.textContent = "Failed to load scenario config.";
    return;
  }
  const s = await r.json();
  const c = s.customer;
  els.scenarioDesc.textContent = s.short_description;

  const rows = [
    ["Name", c.name],
    ["City", c.city],
    ["Occupation", c.occupation],
    ["Loan ID", c.loan_id],
    ["Loan amount", fmtINR(c.loan_amount)],
    ["EMI", fmtINR(c.emi_amount)],
    ["Interest", c.interest_rate + "% p.a."],
    ["Tenure", c.tenure_months + " months"],
    ["Outstanding", fmtINR(c.outstanding_principal)],
    ["Due date", c.next_emi_date],
  ];
  if (s.dpd_days > 0) {
    rows.push(["DPD", `${s.dpd_days} days overdue`]);
  }
  els.customerKv.innerHTML = rows
    .map(([k, v], i) => {
      const cls = (k === "DPD") ? "v dpd" : "v";
      return `<div class="k">${k}</div><div class="${cls}">${v}</div>`;
    })
    .join("");

  els.hints.innerHTML = (s.user_hints || [])
    .map((h) => `<li>${h.replace(/^Try:\s*'([^']+)'\s*->/, "<b>Try: \u201C$1\u201D</b> →")}</li>`)
    .join("");

  if (els.optionsList && s.restructuring_options?.length) {
    els.optionsList.innerHTML = s.restructuring_options
      .map(
        (o) => `
        <div class="option">
          <div class="opt-name">${o.name}</div>
          <div class="opt-desc">${o.description}</div>
          <div class="opt-impact">${o.impact}</div>
        </div>`,
      )
      .join("");
  }
}

// ---------- Transcript ----------

function clearTranscript() {
  els.transcript.innerHTML = "";
}

function addBubble(who, text) {
  const empty = els.transcript.querySelector(".empty-hint");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = `bubble ${who}`;
  div.innerHTML = `<div class="who">${who === "agent" ? "Priya · FlexiLoans" : "You · Customer"}</div><div>${text}</div>`;
  els.transcript.appendChild(div);
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return div;
}

function appendToLastAgent(text) {
  const bubbles = els.transcript.querySelectorAll(".bubble.agent");
  const last = bubbles[bubbles.length - 1];
  if (!last) return addBubble("agent", text);
  const body = last.querySelector("div:nth-child(2)");
  body.textContent = (body.textContent + " " + text).trim();
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return last;
}

// ---------- Status ----------

function setStatus(label, cls) {
  els.statusText.textContent = label;
  els.status.classList.remove("live", "ready");
  if (cls) els.status.classList.add(cls);
}

function setMicHint(label, hint) {
  els.micLabel.textContent = label;
  els.micHint.textContent = hint;
}

function formatCost(usd) {
  if (!usd || usd <= 0) return "$0.0000";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 1)      return "$" + usd.toFixed(4);
  return "$" + usd.toFixed(2);
}

function updateUsagePill(u) {
  if (!els.usagePill || !u) return;
  const tokTxt = els.usagePill.querySelector(".usage-tokens");
  const costTxt = els.usagePill.querySelector(".usage-cost");
  const tot = u.tokens_total ?? ((u.tokens_in || 0) + (u.tokens_out || 0));
  tokTxt.textContent = `${tot.toLocaleString()} tok (${(u.tokens_in||0).toLocaleString()} in / ${(u.tokens_out||0).toLocaleString()} out)`;
  costTxt.textContent = formatCost(u.cost_usd);
  els.usagePill.classList.toggle("free", !u.cost_usd || u.cost_usd <= 0);
  const modelTag = u.model ? ` · ${u.model}` : "";
  const perTurn = u.cost_usd && u.turns
    ? ` · ${formatCost(u.cost_usd / Math.max(u.turns, 1))}/turn`
    : "";
  els.usagePill.title =
    `LLM usage${modelTag}\n` +
    `${(u.tokens_in||0).toLocaleString()} input + ${(u.tokens_out||0).toLocaleString()} output tokens\n` +
    `Estimated cost: ${formatCost(u.cost_usd)}${perTurn}`;
}

function resetUsagePill() {
  if (!els.usagePill) return;
  els.usagePill.querySelector(".usage-tokens").textContent = "— tok";
  els.usagePill.querySelector(".usage-cost").textContent = "$0.0000";
  els.usagePill.classList.add("free");
  els.usagePill.title = "Live LLM token usage + estimated cost";
}

// ---------- WebSocket ----------

function openSocket() {
  return new Promise((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/call`);
    ws.onopen = () => resolve();
    ws.onerror = (e) => reject(e);
    ws.onclose = () => {
      setStatus("Disconnected");
      state.callActive = false;
      els.startBtn.disabled = false;
      els.endBtn.disabled = true;
      if (els.backendSelect) els.backendSelect.disabled = false;
      setMicState(ST.IDLE);
    };
    ws.onmessage = onMessage;
  });
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function onMessage(ev) {
  let msg;
  try { msg = JSON.parse(ev.data); } catch { return; }

  switch (msg.type) {
    case "session":
      state.sessionId = msg.session_id;
      console.log("session", msg.session_id);
      break;

    case "agent_start":
      setMicState(ST.AGENT_SPEAKING);
      setStatus("Agent speaking…", "live");
      addBubble("agent", "");
      break;

    case "agent_chunk":
      if (!state.callActive) break;   // user already ended; ignore in-flight chunks
      appendToLastAgent(msg.text);
      enqueueAgentAudio(msg.audio_b64);
      break;

    case "agent_done":
      if (msg.usage) updateUsagePill(msg.usage);
      if (!state.callActive) break;   // ditto - don't try to re-open mic post-endCall
      flushAgentDone();
      break;

    case "user_transcript":
      if (msg.text) addBubble("user", msg.text);
      else if (msg.note) {
        const tip = document.createElement("div");
        tip.className = "empty-hint";
        tip.style.fontSize = "12px";
        tip.style.color = "var(--warning)";
        tip.textContent = "(no speech detected — please try again)";
        els.transcript.appendChild(tip);
        setTimeout(() => tip.remove(), 3500);
      }
      break;

    case "outcome_pending":
      setStatus("Generating summary…", "live");
      setMicHint("Call ended", "Analysing the transcript… (can take ~10-15 s on Claude)");
      break;

    case "outcome":
      renderOutcome(msg);
      setStatus("Done", "ready");
      setMicHint("Call complete", "See outcome card below.");
      break;

    case "error":
      console.error("server error", msg.message);
      addBubble("agent", `⚠️ ${msg.message}`);
      break;

    case "pong":
      break;
  }
}

// ---------- Agent audio playback queue ----------

function base64ToBlob(b64, mime = "audio/wav") {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Blob([buf], { type: mime });
}

function enqueueAgentAudio(b64) {
  if (!b64) return;
  const blob = base64ToBlob(b64);
  agentPlaybackQueue.push(blob);
  if (!agentPlaying) playNextAgentChunk();
}

function flushAgentDone() {
  _agentDoneFlag = true;
  if (!agentPlaying && agentPlaybackQueue.length === 0) finishAgentTurn();
}

function finishAgentTurn() {
  _agentDoneFlag = false;
  if (state.callActive) {
    setStatus("Your turn", "ready");
    setMicState(ST.LISTENING);
  }
}

function playNextAgentChunk() {
  if (agentPlaybackQueue.length === 0) {
    agentPlaying = false;
    if (_agentDoneFlag) finishAgentTurn();
    return;
  }
  agentPlaying = true;
  const blob = agentPlaybackQueue.shift();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  audio.onended = () => {
    URL.revokeObjectURL(url);
    playNextAgentChunk();
  };
  audio.onerror = () => {
    URL.revokeObjectURL(url);
    playNextAgentChunk();
  };
  audio.play().catch((e) => {
    console.warn("autoplay blocked", e);
    playNextAgentChunk();
  });
}

// ---------- Mic capture: AudioWorklet -> auto-VAD -> downsample -> send ----------

async function setupMic() {
  if (audioCtx) return;
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  inputSampleRate = audioCtx.sampleRate;
  await audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
  micSource = audioCtx.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioCtx, "pcm-capture");
  workletNode.port.onmessage = (e) => onAudioFrame(e.data);
  micSource.connect(workletNode);
}

function setMicState(s) {
  // Once we're tearing the call down, refuse any further state changes from
  // late-arriving server events (agent_done, etc). Only ws.onclose can take
  // us back to IDLE.
  if (micState === ST.ENDING && s !== ST.IDLE && s !== ST.ENDING) return;
  micState = s;
  els.micBtn.classList.remove("listening", "recording");
  switch (s) {
    case ST.IDLE:
      els.micBtn.disabled = true;
      setMicHint("Not in call", "Click Start Call to begin.");
      break;
    case ST.AGENT_SPEAKING:
      els.micBtn.disabled = true;
      setMicHint("🔊 Priya is speaking…", "She'll pause and your mic opens automatically.");
      break;
    case ST.LISTENING:
      els.micBtn.classList.add("listening");
      els.micBtn.disabled = true;
      preBuffer = [];
      preBufferSamples = 0;
      activeBuffer = [];
      setMicHint("🎙️ Listening — go ahead", "Just speak. I'll send automatically when you pause.");
      break;
    case ST.USER_SPEAKING:
      els.micBtn.classList.add("recording");
      els.micBtn.disabled = false;
      setMicHint("● Recording your reply", "Click the mic (or press Space) to send immediately.");
      break;
    case ST.PROCESSING:
      els.micBtn.disabled = true;
      setMicHint("⋯ Sending to Priya", "STT → LLM → TTS pipeline running.");
      break;
    case ST.ENDING:
      els.micBtn.disabled = true;
      setMicHint("Call ended", "Generating outcome summary…");
      break;
  }
}

function computeRMS(buf) {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

function onAudioFrame(float32) {
  if (!state.callActive) return;
  if (micState !== ST.LISTENING && micState !== ST.USER_SPEAKING) return;

  const rms = computeRMS(float32);
  const now = performance.now();

  if (micState === ST.LISTENING) {
    // Maintain a small pre-buffer so we don't clip the start of the utterance.
    preBuffer.push(float32);
    preBufferSamples += float32.length;
    const maxPre = (VAD.preBufferMs / 1000) * inputSampleRate;
    while (preBufferSamples > maxPre && preBuffer.length > 1) {
      preBufferSamples -= preBuffer.shift().length;
    }
    if (rms > VAD.startRms) {
      // Speech started
      activeBuffer = preBuffer.slice();
      preBuffer = [];
      preBufferSamples = 0;
      speechStartedAt = now;
      lastVoiceAt = now;
      setMicState(ST.USER_SPEAKING);
      clearTimeout(maxUtteranceTimer);
      maxUtteranceTimer = setTimeout(finishUtterance, VAD.maxUtteranceMs);
    }
    return;
  }

  // micState === ST.USER_SPEAKING
  activeBuffer.push(float32);
  if (rms > VAD.keepRms) lastVoiceAt = now;
  const silenceMs = now - lastVoiceAt;
  const speechMs = now - speechStartedAt;
  if (silenceMs > VAD.endSilenceMs && speechMs > VAD.minSpeechMs) {
    finishUtterance();
  }
}

function finishUtterance() {
  clearTimeout(maxUtteranceTimer);
  if (micState !== ST.USER_SPEAKING) return;
  if (activeBuffer.length === 0) {
    setMicState(ST.LISTENING);
    return;
  }
  const totalLen = activeBuffer.reduce((s, a) => s + a.length, 0);
  const flat = new Float32Array(totalLen);
  let off = 0;
  for (const c of activeBuffer) {
    flat.set(c, off);
    off += c.length;
  }
  activeBuffer = [];
  preBuffer = [];
  preBufferSamples = 0;

  const pcm16 = downsampleTo16k(flat, inputSampleRate);
  const b64 = int16ToBase64(pcm16);
  send({ type: "audio", pcm_b64: b64 });
  setMicState(ST.PROCESSING);
  setStatus("Transcribing…", "live");
}

function downsampleTo16k(float32, fromRate) {
  if (fromRate === TARGET_SR) return floatToInt16(float32);
  const ratio = fromRate / TARGET_SR;
  const newLen = Math.floor(float32.length / ratio);
  const out = new Int16Array(newLen);
  let offResult = 0;
  let offBuffer = 0;
  while (offResult < newLen) {
    const nextOffBuffer = Math.round((offResult + 1) * ratio);
    let acc = 0, count = 0;
    for (let i = offBuffer; i < nextOffBuffer && i < float32.length; i++) {
      acc += float32[i];
      count++;
    }
    const sample = count > 0 ? acc / count : 0;
    const s = Math.max(-1, Math.min(1, sample));
    out[offResult] = s < 0 ? s * 0x8000 : s * 0x7fff;
    offResult++;
    offBuffer = nextOffBuffer;
  }
  return out;
}

function floatToInt16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function int16ToBase64(int16) {
  const bytes = new Uint8Array(int16.buffer);
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

// ---------- Outcome card ----------

function pillClass(outcome) {
  if (["commitment"].includes(outcome)) return "success";
  if (["partial_commitment", "callback_requested"].includes(outcome)) return "warn";
  if (["refused", "dnd_requested", "escalate"].includes(outcome)) return "danger";
  if (outcome === "call_incomplete") return "warn";
  return "";
}

function sentimentClass(s) {
  if (s === "positive") return "success";
  if (s === "concerned" || s === "neutral") return "warn";
  if (s === "frustrated" || s === "angry") return "danger";
  return "";
}

// Outcomes that operationally require human escalation.
const ESCALATION_OUTCOMES = new Set([
  "escalate", "refused", "dnd_requested", "call_incomplete",
]);

function buildActionRecommendation(o, violations) {
  const outcome = o.outcome || "info_only";
  const needsEscalation =
    ESCALATION_OUTCOMES.has(outcome) || (violations && violations.length > 0);
  let priority = "low";
  if (outcome === "escalate" || outcome === "dnd_requested") priority = "critical";
  else if (outcome === "refused" || (violations && violations.length > 0)) priority = "high";
  else if (outcome === "partial_commitment" || outcome === "callback_requested") priority = "medium";

  const queue =
    outcome === "dnd_requested" ? "compliance_dnd"
    : outcome === "escalate" ? "human_collections_supervisor"
    : outcome === "refused" ? "field_collections"
    : outcome === "callback_requested" ? "callback_scheduler"
    : outcome === "commitment" ? "auto_followup"
    : outcome === "partial_commitment" ? "restructuring_ops"
    : "review_queue";

  return {
    escalation_required: needsEscalation,
    priority,
    assign_to_queue: queue,
    suggested_next_action: o.next_action || null,
    sla_hours:
      priority === "critical" ? 2
      : priority === "high" ? 8
      : priority === "medium" ? 24
      : 72,
  };
}

function renderOutcome(msg) {
  const o = msg.data || {};
  const m = msg.metrics || {};
  const violations = msg.violations || [];
  const u = msg.usage || null;
  if (u) updateUsagePill(u);

  // The fully structured analysis blob — what an operations team would actually consume.
  const structured = {
    session_id: state.sessionId || msg.session_id || null,
    scenario: document.body.dataset.scenario,
    timestamp_utc: new Date().toISOString(),
    turns: msg.turns ?? null,
    outcome: o,
    action_recommendation: buildActionRecommendation(o, violations),
    rbi_compliance: {
      violations_count: violations.length,
      violations: violations,
    },
    llm_usage: u || null,
    latency_ms: m,
  };

  const fmtMs = (x) => x ? `${Math.round(x)} <small>ms</small>` : "—";

  const metricCard = (key, label) => {
    const v = m[key];
    if (!v) return "";
    return `<div class="metric"><div class="label">${label}</div>
      <div class="value">${fmtMs(v.avg_ms)} <small>avg · n=${v.n}</small></div></div>`;
  };

  const card = document.createElement("section");
  card.className = "card outcome";
  card.innerHTML = `
    <h3>Call outcome</h3>
    <div class="pills">
      <div class="pill ${pillClass(o.outcome)}">Outcome: <b>${o.outcome || "—"}</b></div>
      <div class="pill ${sentimentClass(o.customer_sentiment)}">Sentiment: <b>${o.customer_sentiment || "—"}</b></div>
      ${o.restructuring_option_accepted ? `<div class="pill success">Accepted: <b>${o.restructuring_option_accepted}</b></div>` : ""}
      ${o.commitment_amount ? `<div class="pill success">Committed: <b>${fmtINR(o.commitment_amount)}</b></div>` : ""}
      ${o.commitment_date ? `<div class="pill">By: <b>${o.commitment_date}</b></div>` : ""}
      <div class="pill">Turns: <b>${msg.turns ?? "—"}</b></div>
      <div class="pill ${violations.length ? "warn" : "success"}">RBI violations: <b>${violations.length}</b></div>
    </div>
    ${o.key_points?.length ? `<div style="margin-top:8px"><b>Key points</b><ul style="margin:6px 0 0;padding-left:18px;color:var(--muted);font-size:13px">${o.key_points.map(p => `<li>${p}</li>`).join("")}</ul></div>` : ""}
    ${o.next_action ? `<div style="margin-top:10px;font-size:13px"><b>Next action:</b> <span style="color:var(--muted)">${o.next_action}</span></div>` : ""}
    ${u ? `
    <div style="margin-top:10px;font-size:13px;color:var(--muted);">
      <b style="color:var(--text)">LLM usage:</b>
      ${(u.tokens_in||0).toLocaleString()} in / ${(u.tokens_out||0).toLocaleString()} out
      = <b style="color:var(--text)">${(u.tokens_total||0).toLocaleString()} tokens</b>
      · <b style="color:var(--accent-2)">${formatCost(u.cost_usd)}</b>
      ${u.turns ? ` (${formatCost(u.cost_usd / Math.max(u.turns,1))}/turn)` : ""}
      ${u.model ? `<br><span style="font-size:12px">model: <code>${u.model}</code></span>` : ""}
    </div>` : ""}
    <div class="metrics">
      ${metricCard("stt", "STT")}
      ${metricCard("llm_first_sentence", "LLM 1st sentence")}
      ${metricCard("tts", "TTS / chunk")}
      ${metricCard("total_turn", "Total turn")}
    </div>
  `;

  // Action recommendation strip (escalation banner)
  const action = structured.action_recommendation;
  const actionClass =
    action.priority === "critical" ? "danger"
    : action.priority === "high" ? "warn"
    : action.priority === "medium" ? "warn"
    : "success";
  const actionStrip = document.createElement("div");
  actionStrip.className = `action-strip ${actionClass}`;
  actionStrip.innerHTML = `
    <div class="action-row">
      <span class="action-tag ${actionClass}">
        ${action.escalation_required ? "⚠️ ESCALATE" : "✓ AUTO-HANDLE"}
      </span>
      <span class="action-priority">Priority: <b>${action.priority.toUpperCase()}</b></span>
      <span class="action-queue">Queue: <code>${action.assign_to_queue}</code></span>
      <span class="action-sla">SLA: <b>${action.sla_hours}h</b></span>
    </div>
  `;
  card.appendChild(actionStrip);

  // Raw JSON card (collapsible-ish, with copy button)
  const jsonStr = JSON.stringify(structured, null, 2);
  const jsonCard = document.createElement("section");
  jsonCard.className = "card outcome-json";
  jsonCard.innerHTML = `
    <div class="json-header">
      <h3>Structured analysis · JSON</h3>
      <button class="btn-copy" type="button">Copy JSON</button>
    </div>
    <p class="json-hint">
      This is what an operations API would consume — outcome, action recommendation
      (queue + SLA), RBI compliance, LLM usage and per-stage latency in one blob.
    </p>
    <pre class="json-block"><code></code></pre>
  `;
  jsonCard.querySelector("code").textContent = jsonStr;
  jsonCard.querySelector(".btn-copy").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    try {
      await navigator.clipboard.writeText(jsonStr);
      btn.textContent = "✓ Copied";
      setTimeout(() => { btn.textContent = "Copy JSON"; }, 1500);
    } catch {
      btn.textContent = "Copy failed";
      setTimeout(() => { btn.textContent = "Copy JSON"; }, 1500);
    }
  });

  els.outcomeContainer.innerHTML = "";
  els.outcomeContainer.appendChild(card);
  els.outcomeContainer.appendChild(jsonCard);
}

// ---------- Wire-up ----------

async function startCall() {
  els.startBtn.disabled = true;
  els.outcomeContainer.innerHTML = "";
  setStatus("Connecting…", "live");
  try {
    await setupMic();
  } catch (e) {
    setStatus("Mic permission denied");
    els.startBtn.disabled = false;
    addBubble("agent", "I need access to your microphone to take this call. Please allow it and click Start again.");
    return;
  }

  try {
    await openSocket();
  } catch (e) {
    setStatus("Connection failed");
    els.startBtn.disabled = false;
    return;
  }

  clearTranscript();
  resetUsagePill();
  state.callActive = true;
  els.endBtn.disabled = false;
  if (els.backendSelect) els.backendSelect.disabled = true;
  setStatus("Ringing…", "live");
  const backend = els.backendSelect ? els.backendSelect.value : undefined;
  send({ type: "start", scenario: scenarioId, backend });
}

function endCall() {
  if (!state.callActive) return;
  // Trapdoor: from this point on, no agent_chunk / agent_done / VAD frame
  // is allowed to re-open the mic. Order matters - flip callActive FIRST so
  // any in-flight finishAgentTurn() becomes a no-op.
  state.callActive = false;
  send({ type: "end" });

  // Stop the current playback and drop anything queued from the in-flight turn.
  agentPlaybackQueue = [];
  agentPlaying = false;
  _agentDoneFlag = false;
  // Stop the active utterance recording (if VAD was mid-capture).
  clearTimeout(maxUtteranceTimer);
  activeBuffer = [];
  preBuffer = [];
  preBufferSamples = 0;

  setStatus("Wrapping up…", "live");
  els.endBtn.disabled = true;
  setMicState(ST.ENDING);

  // Generous safety net only — the outcome usually arrives in 2-15 s and the
  // server closes the WS for us right after sending it. Don't slam it shut
  // before the classifier finishes.
  setTimeout(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      console.warn("Outcome timeout (75s) — forcing socket close");
      ws.close();
    }
  }, 75000);
}

function attachMicHandlers() {
  // Click the mic during USER_SPEAKING = "send now" (force-end utterance early).
  // Clicks in any other state are ignored.
  els.micBtn.addEventListener("click", (e) => {
    e.preventDefault();
    if (micState === ST.USER_SPEAKING) finishUtterance();
  });

  // Spacebar = same shortcut for "send now".
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space") return;
    if (document.activeElement?.tagName === "INPUT") return;
    if (micState === ST.USER_SPEAKING) {
      e.preventDefault();
      finishUtterance();
    }
  });
}

async function loadBackends() {
  if (!els.backendSelect) return;
  try {
    const r = await fetch("/api/backends");
    if (!r.ok) return;
    const data = await r.json();
    els.backendSelect.innerHTML = "";
    for (const b of data.backends) {
      const opt = document.createElement("option");
      opt.value = b.id;
      opt.textContent = `${b.label} — ${b.hint}`;
      if (b.id === data.default) opt.selected = true;
      els.backendSelect.appendChild(opt);
    }
    const stored = localStorage.getItem("voiceAgentBackend");
    if (stored && [...els.backendSelect.options].some((o) => o.value === stored)) {
      els.backendSelect.value = stored;
    }
    els.backendSelect.addEventListener("change", () => {
      localStorage.setItem("voiceAgentBackend", els.backendSelect.value);
    });
  } catch (e) {
    console.warn("backend list fetch failed", e);
  }
}

els.startBtn.addEventListener("click", startCall);
els.endBtn.addEventListener("click", endCall);
attachMicHandlers();
loadScenario();
loadBackends();
