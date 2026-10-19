/**
 * app.js — Q-PLC Supervisory Monitor Frontend Logic (Stage 1.6 Upgrade)
 *
 * Responsibilities:
 *  1. MQTT.js WebSocket connection lifecycle (read-only: plcmon/plc-gw-01/#)
 *  2. Telemetry ingestion, schema validation, and sequence monotonicity tracking
 *  3. ISA-101 Situational Awareness Tab Navigation (Overview, I/O, Events, Diag, Settings)
 *  4. Dual-mode I/O presentation: Card Grid and Compact Engineering Diagnostics Table
 *  5. Strict bit normalization: Supported ON/OFF, distinct UNKNOWN for malformed/null/missing
 *  6. State-change suppression for invalid telemetry
 *  7. Multi-filter Event Journal with real-time keyword search and CSV export
 *  8. Real-time telemetry freshness & 15-second stale data detection
 *  9. Isolated Offline Demo Mode simulation (X3 input pulse driving Y20 coil)
 *
 * NOTE: This client is strictly read-only. No write topics or opcodes exist.
 */

'use strict';

// ─── Constants & Device Catalog ───────────────────────────────────────────────

const STALE_THRESHOLD_MS  = 15_000;  // 15 s without data → stale
const AGE_UPDATE_INTERVAL = 1_000;   // Update data age displays every 1 s
const MAX_HISTORY_ITEMS   = 300;     // Event journal capacity
const DEMO_POLL_INTERVAL  = 2_000;   // Demo mode cycle period

const INPUT_DEVICES  = ['X0','X1','X2','X3','X4','X5','X6','X7'];
const OUTPUT_DEVICES = ['Y20','Y21','Y22','Y23','Y24','Y25','Y26','Y27'];

// Engineering Point Catalog (Verified assignments vs unverified ladder)
const DEVICE_METADATA = {
  X0:  { hex: '0x00', desc: 'Input Contact 0 (Spare / Laboratory Pushbutton)' },
  X1:  { hex: '0x01', desc: 'Input Contact 1 (Spare / Laboratory Pushbutton)' },
  X2:  { hex: '0x02', desc: 'Input Contact 2 (Spare / Laboratory Pushbutton)' },
  X3:  { hex: '0x03', desc: 'Trigger Pushbutton (Verified Ladder Contact → Y20)' },
  X4:  { hex: '0x04', desc: 'Input Contact 4 (Spare / Laboratory Pushbutton)' },
  X5:  { hex: '0x05', desc: 'Input Contact 5 (Spare / Laboratory Pushbutton)' },
  X6:  { hex: '0x06', desc: 'Input Contact 6 (Spare / Laboratory Pushbutton)' },
  X7:  { hex: '0x07', desc: 'Input Contact 7 (Spare / Laboratory Pushbutton)' },
  Y20: { hex: '0x20', desc: 'Master Output Relay (Verified Driven by X3 Rung)' },
  Y21: { hex: '0x21', desc: 'Output Relay 21 (Ladder unverified / Spare)' },
  Y22: { hex: '0x22', desc: 'Output Relay 22 (Ladder unverified / Spare)' },
  Y23: { hex: '0x23', desc: 'Output Relay 23 (Ladder unverified / Spare)' },
  Y24: { hex: '0x24', desc: 'Output Relay 24 (Ladder unverified / Spare)' },
  Y25: { hex: '0x25', desc: 'Output Relay 25 (Ladder unverified / Spare)' },
  Y26: { hex: '0x26', desc: 'Output Relay 26 (Ladder unverified / Spare)' },
  Y27: { hex: '0x27', desc: 'Output Relay 27 (Ladder unverified / Spare)' },
};

// ─── State Machine ────────────────────────────────────────────────────────────

const state = {
  theme:          'dark',     // 'dark' | 'light'
  mqttClient:     null,
  activeTab:      'overview',
  ioViewMode:     'cards',    // 'cards' | 'table'
  ioFilter:       'all',      // 'all' | 'on' | 'off' | 'unknown'
  
  // Independent Subsystem States
  connected:      false,      // Broker connection
  plcConnected:   false,      // PLC MC Protocol socket
  espOnline:      false,      // ESP8266 Wi-Fi bridge
  gwActive:       false,      // Python gateway telemetry stream
  
  // Telemetry Freshness & Tracking
  lastReadingTs:  null,       // Date object of last incoming data
  lastPayload:    null,       // Last valid plc_reading payload
  isStale:        false,
  lastSeq:        null,       // Sequence tracking for gap detection
  
  // Telemetry Counters
  stats: {
    packetsReceived: 0,
    heartbeats:      0,
    seqGaps:         0,
    discarded:       0,
    stateChanges:    0,
    reconnects:      0,
    lastFault:       null,
  },

  // Event Journal
  historyItems:   [],         // { id, tsIso, tsLocal, category, severity, message }
  eventCategory:  'all',      // 'all' | 'state_change' | 'comm_error' | 'system'
  eventSeverity:  'all',      // 'all' | 'info' | 'warn' | 'error'
  eventSearch:    '',

  // Demo Simulation & Scenarios
  isDemo:         false,
  demoPaused:     false,
  demoScenario:   'normal',   // 'normal' | 'delayed' | 'seq_gap' | 'stale' | 'gw_fault'
  demoInterval:   null,
  demoTimeoutId:  null,
  demoSeq:        100,
  demoStep:       0,
  
  // Timers
  ageTimerId:     null,
};

// ─── DOM Element References ───────────────────────────────────────────────────

const $ = id => document.getElementById(id);
let dom = {};

function cacheDomRefs() {
  dom = {
    // Mode Tag
    headerModeTag:     $('header-mode-tag'),

    // Theme Controls
    btnThemeToggle:    $('btn-theme-toggle'),
    themeToggleText:   $('theme-toggle-text'),
    radioThemeDark:    $('theme-radio-dark'),
    radioThemeLight:   $('theme-radio-light'),

    // Subsystem Header Status Pills
    dotMqtt:    $('dot-mqtt'),  textMqtt:    $('text-mqtt'),  pillMqtt: $('pill-mqtt'),
    dotEsp:     $('dot-esp'),   textEsp:     $('text-esp'),   pillEsp:  $('pill-esp'),
    dotGw:      $('dot-gw'),    textGw:      $('text-gw'),    pillGw:   $('pill-gw'),
    dotPlc:     $('dot-plc'),   textPlc:     $('text-plc'),   pillPlc:  $('pill-plc'),

    // Navigation & Meta
    navFreshness:   $('freshness-indicator'),
    navHeaderAge:   $('header-data-age'),
    navEventCount:  $('nav-event-count'),

    // Banners & Demo Controller
    staleBanner:        $('stale-banner'),
    staleBannerText:    $('stale-banner-text'),
    demoBanner:         $('demo-banner'),
    btnDemoPause:       $('btn-demo-pause'),
    demoPauseText:      $('demo-pause-text'),
    btnDemoReset:       $('btn-demo-reset'),
    demoScenarioBadge:  $('demo-scenario-badge'),
    selectDemoScenario: $('select-demo-scenario'),

    // Overview Tab
    badgeSubsystemPlc:  $('badge-subsystem-plc'),  detailSubsystemPlc:  $('detail-subsystem-plc'),
    badgeSubsystemGw:   $('badge-subsystem-gw'),   detailSubsystemGw:   $('detail-subsystem-gw'),
    badgeSubsystemEsp:  $('badge-subsystem-esp'),  detailSubsystemEsp:  $('detail-subsystem-esp'),
    badgeSubsystemMqtt: $('badge-subsystem-mqtt'), detailSubsystemMqtt: $('detail-subsystem-mqtt'),
    
    metaFreshnessState: $('meta-freshness-state'),
    metaFreshnessSub:   $('meta-freshness-sub'),
    metaDataAge:        $('meta-data-age'),
    metaLastTs:         $('meta-last-ts'),
    metaSeq:            $('meta-seq'),
    metaSeqHealth:      $('meta-seq-health'),
    metaGatewayId:      $('meta-gateway-id'),
    metaActiveCount:    $('meta-active-count'),
    metaUnknownCount:   $('meta-unknown-count'),

    // Ladder Contact Visuals (Overview)
    ladderX3Elem:       $('ladder-x3-elem'),
    ladderX3Symbol:     $('ladder-x3-symbol'),
    ladderX3State:      $('ladder-x3-state'),
    ladderY20Elem:      $('ladder-y20-elem'),
    ladderY20Symbol:    $('ladder-y20-symbol'),
    ladderY20State:     $('ladder-y20-state'),
    ladderFlowLine:     $('ladder-flow-line'),
    ladderModeBadge:    $('ladder-mode-badge'),
    ladderRungStatus:   $('ladder-rung-status-text'),
    ladderRungDot:      $('ladder-rung-dot'),
    miniEventList:      $('mini-event-list'),
    btnGotoEvents:      $('btn-goto-events'),
    btnGotoIo:          $('btn-goto-io'),
    overviewTileX3:     $('overview-tile-x3'),
    overviewBadgeX3:    $('overview-badge-x3'),
    overviewTileY20:    $('overview-tile-y20'),
    overviewBadgeY20:   $('overview-badge-y20'),

    // Ladder Contact Visuals (PLC I/O Tab)
    ladderIoCard:       $('ladder-io-card'),
    ladderIoX3Elem:     $('ladder-io-x3-elem'),
    ladderIoX3Symbol:   $('ladder-io-x3-symbol'),
    ladderIoX3State:    $('ladder-io-x3-state'),
    ladderIoY20Elem:    $('ladder-io-y20-elem'),
    ladderIoY20Symbol:  $('ladder-io-y20-symbol'),
    ladderIoY20State:   $('ladder-io-y20-state'),
    ladderIoFlowLine:   $('ladder-io-flow-line'),
    ladderIoModeBadge:  $('ladder-io-mode-badge'),

    // I/O Monitor Tab
    btnViewCards:       $('btn-view-cards'),
    btnViewTable:       $('btn-view-table'),
    ioContainerCards:   $('io-container-cards'),
    ioContainerTable:   $('io-container-table'),
    inputsGrid:         $('inputs-grid'),
    outputsGrid:        $('outputs-grid'),
    ioTableBody:        $('io-table-body'),

    // Event Journal Tab
    inputEventSearch:   $('input-event-search'),
    historyList:        $('history-list'),
    historyEmpty:       $('history-empty'),
    historyCount:       $('history-count'),
    btnExportCsv:       $('btn-export-csv'),
    btnClearHist:       $('btn-clear-history'),

    // Diagnostics Tab
    statPacketsReceived: $('stat-packets-received'),
    statHeartbeats:      $('stat-heartbeats'),
    statSeqGaps:         $('stat-seq-gaps'),
    statDiscarded:       $('stat-discarded'),
    statStateChanges:    $('stat-state-changes'),
    statReconnects:      $('stat-reconnects'),
    diagLastFault:       $('diag-last-fault'),
    diagSubscribedTopic: $('diag-subscribed-topic'),
    diagClientId:        $('diag-client-id'),

    // Settings Tab
    inputUrl:       $('input-broker-url'),
    inputUser:      $('input-broker-user'),
    inputPass:      $('input-broker-pass'),
    inputTopic:     $('input-topic-prefix'),
    btnConnect:     $('btn-connect'),
    btnDisconnect:  $('btn-disconnect'),
    chkDemo:        $('chk-demo'),
  };
}

// ─── Initialisation ───────────────────────────────────────────────────────────

function init() {
  cacheDomRefs();
  initTheme();
  buildDeviceGrids();
  buildDeviceTable();
  setupEventListeners();
  startAgeTimer();
  restoreSettings();
  updateSubsystemMatrix();
  updateLadderDiagram(null, null);
}

// ─── Layout Builders ──────────────────────────────────────────────────────────

function buildDeviceGrids() {
  if (dom.inputsGrid) {
    dom.inputsGrid.innerHTML = INPUT_DEVICES.map(d => deviceTileHTML(d, 'input')).join('');
  }
  if (dom.outputsGrid) {
    dom.outputsGrid.innerHTML = OUTPUT_DEVICES.map(d => deviceTileHTML(d, 'output')).join('');
  }
}

function deviceTileHTML(device, kind) {
  const meta = DEVICE_METADATA[device] || { hex: '0x??', desc: 'Digital Point' };
  return `
    <li class="device-tile ${kind}-tile loading" id="tile-${device}"
        data-device="${device}" data-kind="${kind}"
        data-state="unknown" role="listitem"
        aria-label="${device}: unknown">
      <div class="tile-top">
        <span class="tile-label">${device}</span>
        <div class="tile-led" aria-hidden="true"></div>
      </div>
      <p class="tile-desc" title="${meta.desc}">${meta.desc}</p>
      <div class="tile-bottom">
        <span class="tile-state-badge">—</span>
        <span class="tile-hex">${meta.hex}</span>
      </div>
    </li>`;
}

function buildDeviceTable() {
  if (!dom.ioTableBody) return;
  const allDevices = [...INPUT_DEVICES, ...OUTPUT_DEVICES];
  
  dom.ioTableBody.innerHTML = allDevices.map(d => {
    const isInput = d.startsWith('X');
    const meta = DEVICE_METADATA[d] || { hex: '—', desc: 'Digital Channel' };
    return `
      <tr id="row-${d}" data-device="${d}" data-kind="${isInput ? 'input' : 'output'}" data-state="unknown">
        <td class="point-tag">
          <span class="status-dot dot-unknown" id="row-dot-${d}"></span>
          <strong>${d}</strong>
        </td>
        <td class="point-hex"><code>${meta.hex}</code></td>
        <td>${isInput ? 'Input Contact' : 'Output Coil'}</td>
        <td class="table-desc">${meta.desc}</td>
        <td>
          <span class="table-state-pill unknown" id="row-state-${d}">UNKNOWN</span>
        </td>
        <td><code id="row-raw-${d}">—</code></td>
        <td>
          <div class="signal-bar-wrap">
            <div class="signal-bar" id="row-bar-${d}"></div>
          </div>
        </td>
        <td class="point-ts mono" id="row-ts-${d}">—</td>
      </tr>`;
  }).join('');
}

// ─── Theme Management Controller ─────────────────────────────────────────────

function initTheme() {
  let savedTheme = 'dark';
  try {
    const stored = localStorage.getItem('qplc_theme');
    if (stored === 'light' || stored === 'dark') savedTheme = stored;
  } catch (e) {
    savedTheme = 'dark';
  }
  setTheme(savedTheme, false);
}

function setTheme(themeName, persist = true) {
  state.theme = (themeName === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', state.theme);

  if (persist) {
    try {
      localStorage.setItem('qplc_theme', state.theme);
    } catch (e) {}
  }

  // Update header toggle button text & aria
  if (dom.themeToggleText) {
    dom.themeToggleText.textContent = state.theme === 'light' ? 'Light Theme' : 'Dark Theme';
  }
  if (dom.btnThemeToggle) {
    dom.btnThemeToggle.setAttribute('aria-label', `Theme is ${state.theme}. Click to switch.`);
  }

  // Update Settings radios
  if (dom.radioThemeDark) dom.radioThemeDark.checked = (state.theme === 'dark');
  if (dom.radioThemeLight) dom.radioThemeLight.checked = (state.theme === 'light');
}

function toggleTheme() {
  const nextTheme = (state.theme === 'dark') ? 'light' : 'dark';
  setTheme(nextTheme, true);
  addHistoryItem('system', 'info', `Visual theme switched to ${nextTheme.toUpperCase()}`);
}

// ─── Event Listeners ──────────────────────────────────────────────────────────

function setupEventListeners() {
  // Theme Toggle Button & Radios
  if (dom.btnThemeToggle) {
    dom.btnThemeToggle.addEventListener('click', toggleTheme);
  }
  if (dom.radioThemeDark) {
    dom.radioThemeDark.addEventListener('change', () => { if (dom.radioThemeDark.checked) setTheme('dark'); });
  }
  if (dom.radioThemeLight) {
    dom.radioThemeLight.addEventListener('change', () => { if (dom.radioThemeLight.checked) setTheme('light'); });
  }

  // Tab Switching
  document.querySelectorAll('.nav-tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.target));
  });

  if (dom.btnGotoEvents) {
    dom.btnGotoEvents.addEventListener('click', () => switchTab('events'));
  }
  if (dom.btnGotoIo) {
    dom.btnGotoIo.addEventListener('click', () => switchTab('io'));
  }

  // I/O View Switcher
  if (dom.btnViewCards) {
    dom.btnViewCards.addEventListener('click', () => setIoViewMode('cards'));
  }
  if (dom.btnViewTable) {
    dom.btnViewTable.addEventListener('click', () => setIoViewMode('table'));
  }

  // I/O State Filter Pills
  document.querySelectorAll('.filter-pill').forEach(btn => {
    btn.addEventListener('click', () => setIoFilter(btn.dataset.ioFilter));
  });

  // Event Journal Category Filters
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => setEventCategory(btn.dataset.filter));
  });

  // Event Journal Severity Filters
  document.querySelectorAll('.filter-btn-sev').forEach(btn => {
    btn.addEventListener('click', () => setEventSeverity(btn.dataset.severity));
  });

  // Event Journal Live Search
  if (dom.inputEventSearch) {
    dom.inputEventSearch.addEventListener('input', e => {
      state.eventSearch = e.target.value.trim().toLowerCase();
      applyEventFilters();
    });
  }

  // Event Journal Actions
  if (dom.btnClearHist)  dom.btnClearHist.addEventListener('click', clearHistory);
  if (dom.btnExportCsv)  dom.btnExportCsv.addEventListener('click', exportEventsCsv);

  // Demo Simulation Scenario Actions
  if (dom.btnDemoPause)  dom.btnDemoPause.addEventListener('click', toggleDemoPause);
  if (dom.btnDemoReset)  dom.btnDemoReset.addEventListener('click', resetDemoSimulation);
  document.querySelectorAll('.demo-scenario-pill, .scenario-btn').forEach(btn => {
    btn.addEventListener('click', () => setDemoScenario(btn.dataset.scenario));
  });
  if (dom.selectDemoScenario) {
    dom.selectDemoScenario.addEventListener('change', e => setDemoScenario(e.target.value));
  }

  // Connection & Demo Actions
  if (dom.btnConnect)    dom.btnConnect.addEventListener('click', handleConnect);
  if (dom.btnDisconnect) dom.btnDisconnect.addEventListener('click', handleDisconnect);
  if (dom.chkDemo)       dom.chkDemo.addEventListener('change', handleDemoToggle);
}

// ─── Tab Navigation Controller ────────────────────────────────────────────────

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll('.nav-tab').forEach(btn => {
    const isActive = btn.dataset.target === tabId;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });

  document.querySelectorAll('.tab-panel').forEach(panel => {
    const isTarget = panel.id === `panel-${tabId}`;
    panel.classList.toggle('active', isTarget);
  });
}

// ─── I/O View Mode Controller ─────────────────────────────────────────────────

function setIoViewMode(mode) {
  state.ioViewMode = mode;
  if (dom.btnViewCards) dom.btnViewCards.classList.toggle('active', mode === 'cards');
  if (dom.btnViewTable) dom.btnViewTable.classList.toggle('active', mode === 'table');
  if (dom.ioContainerCards) dom.ioContainerCards.hidden = mode !== 'cards';
  if (dom.ioContainerTable) dom.ioContainerTable.hidden = mode !== 'table';
}

function setIoFilter(filterKey) {
  state.ioFilter = filterKey;
  document.querySelectorAll('.filter-pill').forEach(pill => {
    pill.classList.toggle('active', pill.dataset.ioFilter === filterKey);
  });
  applyIoFilters();
}

function applyIoFilters() {
  const filter = state.ioFilter;
  // Apply to card grid
  document.querySelectorAll('.device-tile').forEach(tile => {
    const tileState = tile.dataset.state;
    let match = true;
    if (filter === 'on')      match = tileState === 'on';
    else if (filter === 'off')     match = tileState === 'off';
    else if (filter === 'unknown') match = tileState === 'unknown' || tileState === 'stale';
    tile.style.display = match ? '' : 'none';
  });

  // Apply to table rows
  document.querySelectorAll('#io-table-body tr').forEach(row => {
    const rowState = row.dataset.state;
    let match = true;
    if (filter === 'on')      match = rowState === 'on';
    else if (filter === 'off')     match = rowState === 'off';
    else if (filter === 'unknown') match = rowState === 'unknown' || rowState === 'stale';
    row.style.display = match ? '' : 'none';
  });
}

// ─── Settings Persistence ─────────────────────────────────────────────────────

function saveSettings() {
  const s = {
    url:   dom.inputUrl.value.trim(),
    user:  dom.inputUser.value.trim(),
    topic: dom.inputTopic.value.trim(),
  };
  try { localStorage.setItem('qplc_settings', JSON.stringify(s)); } catch(_) {}
}

function restoreSettings() {
  try {
    const raw = localStorage.getItem('qplc_settings');
    if (!raw) return;
    const s = JSON.parse(raw);
    if (s.url   && dom.inputUrl)   dom.inputUrl.value   = s.url;
    if (s.user  && dom.inputUser)  dom.inputUser.value  = s.user;
    if (s.topic && dom.inputTopic) dom.inputTopic.value = s.topic;
  } catch(_) {}
}

// ─── MQTT Client & Connection Lifecycle ───────────────────────────────────────

function handleConnect() {
  if (state.isDemo) {
    addHistoryItem('system', 'warn', 'Operation Blocked: Disable Demo mode before connecting to live broker.');
    return;
  }

  const url    = dom.inputUrl.value.trim();
  const user   = dom.inputUser.value.trim();
  const pass   = dom.inputPass.value;
  const prefix = dom.inputTopic.value.trim() || 'plcmon/plc-gw-01';

  if (!url) {
    alert('Please enter a valid Broker WebSocket URL (wss://...)');
    dom.inputUrl.focus();
    return;
  }

  saveSettings();
  setMqttStatus('connecting', 'Connecting…');
  dom.btnConnect.disabled    = true;
  dom.btnDisconnect.disabled = false;

  const clientId = `qplc-web-${Math.random().toString(36).slice(2, 8)}`;
  if (dom.diagClientId) dom.diagClientId.textContent = clientId;

  const opts = {
    clientId,
    protocol: 'wss',
    keepalive: 30,
    reconnectPeriod: 5000,
    connectTimeout: 10000,
  };
  if (user) { opts.username = user; opts.password = pass; }

  // Verify MQTT library dependency
  if (typeof mqtt === 'undefined' || !mqtt || typeof mqtt.connect !== 'function') {
    setMqttStatus('error', 'Library Missing');
    addHistoryItem('comm_error', 'error', 'MQTT client library (mqtt.min.js) unavailable. Check network connection to CDN.');
    dom.btnConnect.disabled = false;
    return;
  }

  try {
    state.mqttClient = mqtt.connect(url, opts);
  } catch (err) {
    setMqttStatus('error', 'Connect failed');
    addHistoryItem('comm_error', 'error', `MQTT connection failure: ${err.message}`);
    dom.btnConnect.disabled = false;
    return;
  }

  state.mqttClient.on('connect', () => {
    state.connected = true;
    setMqttStatus('ok', 'Connected');
    dom.btnConnect.disabled    = true;
    dom.btnDisconnect.disabled = false;
    addHistoryItem('system', 'info', `MQTT broker link established with ${url}`);

    const topicWildcard = `${prefix}/#`;
    if (dom.diagSubscribedTopic) dom.diagSubscribedTopic.textContent = topicWildcard;

    state.mqttClient.subscribe(topicWildcard, { qos: 1 }, (err, granted) => {
      if (err) {
        addHistoryItem('comm_error', 'error', `Subscription failed on ${topicWildcard}: ${err.message}`);
        return;
      }
      if (granted && granted.length > 0 && granted[0].qos === 128) {
        addHistoryItem('comm_error', 'error',
          `Broker ACL Rejection (QoS 128): Read-only client cannot access ${topicWildcard}. Verify credentials.`);
        return;
      }
      addHistoryItem('system', 'info', `Subscribed to ${topicWildcard} (Read-Only)`);
    });
  });

  state.mqttClient.on('message', (topic, payload) => {
    handleMqttMessage(topic, payload.toString(), prefix);
  });

  state.mqttClient.on('error', err => {
    state.stats.lastFault = err.message;
    addHistoryItem('comm_error', 'error', `Broker protocol error: ${err.message}`);
    setMqttStatus('error', 'Error');
  });

  state.mqttClient.on('reconnect', () => {
    state.stats.reconnects++;
    if (dom.statReconnects) dom.statReconnects.textContent = state.stats.reconnects;
    setMqttStatus('connecting', 'Reconnecting…');
    addHistoryItem('comm_error', 'warn', 'MQTT connection lost — attempting auto-reconnect');
  });

  state.mqttClient.on('close', () => {
    state.connected = false;
    setMqttStatus('off', 'Disconnected');
    dom.btnConnect.disabled    = false;
    dom.btnDisconnect.disabled = true;
    addHistoryItem('system', 'info', 'MQTT session terminated');
    markAllStale();
    updateSubsystemMatrix();
  });

  state.mqttClient.on('offline', () => {
    setMqttStatus('warn', 'Offline');
  });
}

function handleDisconnect() {
  if (state.mqttClient) {
    state.mqttClient.end(true);
    state.mqttClient = null;
  }
  state.connected = false;
  setMqttStatus('off', 'Disconnected');
  dom.btnConnect.disabled    = false;
  dom.btnDisconnect.disabled = true;
  addHistoryItem('system', 'info', 'Disconnected by operator');
  markAllStale();
  updateSubsystemMatrix();
}

// ─── MQTT Message Dispatcher ──────────────────────────────────────────────────

function handleMqttMessage(topic, raw, prefix) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch (e) {
    state.stats.discarded++;
    if (dom.statDiscarded) dom.statDiscarded.textContent = state.stats.discarded;
    addHistoryItem('comm_error', 'error', `Corrupt JSON frame received on topic ${topic}`);
    return;
  }

  const prefixWithSlash = `${prefix}/`;
  if (!topic.startsWith(prefixWithSlash)) return;
  const subtopic = topic.slice(prefixWithSlash.length);

  if (subtopic === 'status') {
    const online = msg.status === 'online';
    state.espOnline = online;
    setStatusPill('esp', online ? 'ok' : 'error', online ? 'Online' : 'Offline');
    if (!online) {
      addHistoryItem('comm_error', 'warn', `Edge bridge ESP8266 reported status: OFFLINE`);
      markAllStale();
    } else {
      addHistoryItem('system', 'info', 'Edge bridge ESP8266 reported status: ONLINE');
    }
    updateSubsystemMatrix();
    return;
  }

  if (subtopic === 'telemetry') {
    processTelemetry(msg);
    return;
  }

  if (subtopic === 'diag') {
    const fault = msg.diag || msg.error || JSON.stringify(msg);
    state.stats.lastFault = fault;
    if (dom.diagLastFault) dom.diagLastFault.textContent = fault;
    addHistoryItem('comm_error', 'warn', `Edge Diagnostic alert: ${fault}`);
    return;
  }
}

// ─── Telemetry Processor ──────────────────────────────────────────────────────

function processTelemetry(msg) {
  if (!msg.type || !msg.gw_id || msg.seq === undefined || !msg.ts) {
    state.stats.discarded++;
    if (dom.statDiscarded) dom.statDiscarded.textContent = state.stats.discarded;
    addHistoryItem('comm_error', 'error', 'Telemetry packet discarded: Missing mandatory contract fields');
    return;
  }

  state.stats.packetsReceived++;
  if (dom.statPacketsReceived) dom.statPacketsReceived.textContent = state.stats.packetsReceived;

  // Monotonic sequence and duplicate / out-of-order tracking
  if (state.lastSeq !== null) {
    if (msg.seq === state.lastSeq) {
      state.stats.discarded++;
      if (dom.statDiscarded) dom.statDiscarded.textContent = state.stats.discarded;
      addHistoryItem('comm_error', 'warn', `Duplicate telemetry frame ignored (seq ${msg.seq})`);
      return;
    }
    if (msg.seq < state.lastSeq) {
      if (msg.seq === 1 || (state.lastSeq - msg.seq) > 50) {
        addHistoryItem('system', 'info', `Gateway sequence reset detected (${state.lastSeq} → ${msg.seq}); re-synchronizing baseline`);
        state.lastSeq = msg.seq;
        if (dom.metaSeqHealth) dom.metaSeqHealth.textContent = 'Sequence baseline reset OK';
      } else {
        state.stats.discarded++;
        if (dom.statDiscarded) dom.statDiscarded.textContent = state.stats.discarded;
        addHistoryItem('comm_error', 'warn', `Out-of-order frame discarded: received seq ${msg.seq} while current sequence is ${state.lastSeq}`);
        return;
      }
    } else if (msg.seq > state.lastSeq + 1) {
      state.stats.seqGaps++;
      if (dom.statSeqGaps) dom.statSeqGaps.textContent = state.stats.seqGaps;
      if (dom.metaSeqHealth) dom.metaSeqHealth.textContent = `Gap detected (${state.lastSeq} → ${msg.seq})`;
      const missed = msg.seq - state.lastSeq - 1;
      const tag = state.isDemo ? '[DEMO] ' : '';
      addHistoryItem('comm_error', 'warn', `${tag}Sequence gap detected: expected seq ${state.lastSeq + 1}, received ${msg.seq} (+${missed} missed frames)`);
      state.lastSeq = msg.seq;
    } else {
      if (dom.metaSeqHealth) dom.metaSeqHealth.textContent = 'Monotonic sequence OK';
      state.lastSeq = msg.seq;
    }
  } else {
    state.lastSeq = msg.seq;
    if (dom.metaSeqHealth) dom.metaSeqHealth.textContent = 'Monotonic sequence OK';
  }

  const wasPlcConnected = state.plcConnected;
  state.plcConnected = !!msg.connected;
  state.gwActive     = true;

  // Metadata Updates
  if (dom.metaGatewayId) dom.metaGatewayId.textContent = msg.gw_id || '—';
  if (dom.metaSeq)       dom.metaSeq.textContent       = msg.seq;
  if (dom.metaLastTs)    dom.metaLastTs.textContent    = formatIsoShort(msg.ts);

  if (msg.type === 'gateway_error' || !msg.connected) {
    setStatusPill('plc', 'error', 'Error');
    state.stats.lastFault = msg.error || 'PLC Unreachable';
    if (dom.diagLastFault) dom.diagLastFault.textContent = state.stats.lastFault;

    if (wasPlcConnected) {
      addHistoryItem('comm_error', 'error', `PLC Communication Lost: ${msg.error || 'Connection timed out'}`);
    }
    markAllStale();
    updateSubsystemMatrix();
    return;
  }

  if (msg.type === 'heartbeat') {
    state.stats.heartbeats++;
    if (dom.statHeartbeats) dom.statHeartbeats.textContent = state.stats.heartbeats;
    state.lastReadingTs = new Date();
    setStatusPill('plc', 'ok', 'Connected');
    updateSubsystemMatrix();
    return;
  }

  if (msg.type !== 'plc_reading') return;

  if (!msg.inputs || !msg.outputs) {
    state.stats.discarded++;
    if (dom.statDiscarded) dom.statDiscarded.textContent = state.stats.discarded;
    addHistoryItem('comm_error', 'error', 'plc_reading discarded: inputs or outputs dictionary absent');
    return;
  }

  const prevPayload = state.lastPayload;
  state.lastPayload   = msg;

  // Check if packet timestamp is historically stale (e.g. retained MQTT message from previous session)
  const packetDate = new Date(msg.ts);
  const isValidTs  = !isNaN(packetDate.getTime());
  const packetAge  = isValidTs ? (Date.now() - packetDate.getTime()) : 0;
  const isHistoric = isValidTs && (packetAge > STALE_THRESHOLD_MS);

  if (isHistoric) {
    state.lastReadingTs = packetDate;
    state.isStale = true;
    addHistoryItem('comm_error', 'warn', `Retained/historic telemetry frame received (timestamp: ${formatIsoShort(msg.ts)}, age: ${formatAge(packetAge)}) — marked as STALE`);
  } else {
    state.lastReadingTs = (isValidTs && packetAge >= 0) ? packetDate : new Date();
    state.isStale = false;
    setStatusPill('plc', 'ok', 'Connected');
    hideStaleBanner();
  }

  // Detect state transitions
  if (prevPayload) {
    detectStateChanges(prevPayload.inputs,  msg.inputs,  'Input');
    detectStateChanges(prevPayload.outputs, msg.outputs, 'Output');
  }

  // Render device representations
  renderDeviceTiles(msg.inputs,  'input');
  renderDeviceTiles(msg.outputs, 'output');
  renderDeviceTableRows(msg.inputs, msg.outputs);
  updateLadderDiagram(msg.inputs, msg.outputs);
  updateActivePointCounters(msg.inputs, msg.outputs);

  if (isHistoric) {
    markAllStale();
  }

  if (!wasPlcConnected && state.plcConnected) {
    addHistoryItem('system', 'info', 'PLC MC Protocol communication restored');
  }

  updateSubsystemMatrix();
}

// ─── Bit Normalization & State Change Logic ───────────────────────────────────

/**
 * Normalizes a raw digital bit value from PLC telemetry.
 *
 * Supported ON values:  1, true, "1", "true", "on" (case-insensitive)
 * Supported OFF values: 0, false, "0", "false", "off" (case-insensitive)
 *
 * Returns:
 *   1 for confirmed ON
 *   0 for confirmed OFF
 *   null for UNKNOWN / invalid / malformed / missing
 */
function normalizeBitValue(val) {
  if (val === 1 || val === true) return 1;
  if (val === 0 || val === false) return 0;
  if (typeof val === 'string') {
    const s = val.trim().toLowerCase();
    if (s === '1' || s === 'true' || s === 'on') return 1;
    if (s === '0' || s === 'false' || s === 'off') return 0;
  }
  return null; // UNKNOWN / invalid / unsupported
}

function detectStateChanges(prev, curr, kind) {
  if (!prev || !curr) return;
  for (const [device, rawCurr] of Object.entries(curr)) {
    const currBit = normalizeBitValue(rawCurr);
    if (currBit === null) continue; // Safety rule: Never generate events for invalid data

    const rawPrev = prev[device];
    if (rawPrev === undefined) continue;
    const prevBit = normalizeBitValue(rawPrev);
    if (prevBit === null) continue; // Transitioning from invalid state does not generate false event

    if (prevBit !== currBit) {
      state.stats.stateChanges++;
      if (dom.statStateChanges) dom.statStateChanges.textContent = state.stats.stateChanges;

      const label = currBit === 1 ? 'ENERGIZED (ON)' : 'DE-ENERGIZED (OFF)';
      const meta = DEVICE_METADATA[device] || {};
      addHistoryItem('state_change', 'info', `${kind} ${device} (${meta.desc || 'Channel'}) transitioned to ${label}`);
    }
  }
}

// ─── Rendering: Card Grid ─────────────────────────────────────────────────────

function renderDeviceTiles(devices, kind) {
  const expectedList = kind === 'input' ? INPUT_DEVICES : OUTPUT_DEVICES;

  for (const device of expectedList) {
    const tile = document.getElementById(`tile-${device}`);
    const hasKey = devices && Object.prototype.hasOwnProperty.call(devices, device);
    const rawVal = hasKey ? devices[device] : undefined;
    const bit = normalizeBitValue(rawVal);

    if (tile) {
      tile.classList.remove('loading');
      const badge = tile.querySelector('.tile-state-badge');

      if (bit === 1) {
        tile.dataset.state = 'on';
        if (badge) badge.textContent = 'ON';
        tile.setAttribute('aria-label', `${device}: ON`);
      } else if (bit === 0) {
        tile.dataset.state = 'off';
        if (badge) badge.textContent = 'OFF';
        tile.setAttribute('aria-label', `${device}: OFF`);
      } else {
        tile.dataset.state = 'unknown';
        if (badge) badge.textContent = 'UNKNOWN';
        tile.setAttribute('aria-label', `${device}: UNKNOWN`);
      }
    }

    const quickBit = document.getElementById(`quick-bit-${device}`);
    if (quickBit) {
      const isVerified = device === 'X3' || device === 'Y20';
      const baseClass = isVerified ? 'quick-bit verified-bit' : 'quick-bit';
      if (bit === 1) {
        quickBit.className = `${baseClass} on`;
        quickBit.title = `${device}: ON (1)`;
      } else if (bit === 0) {
        quickBit.className = `${baseClass} off`;
        quickBit.title = `${device}: OFF (0)`;
      } else {
        quickBit.className = `${baseClass} unknown`;
        quickBit.title = `${device}: UNKNOWN`;
      }
    }
  }
}

// ─── Rendering: Compact Engineering Table ─────────────────────────────────────

function renderDeviceTableRows(inputs, outputs) {
  if (!dom.ioTableBody) return;
  const merged = { ...inputs, ...outputs };
  const allDevices = [...INPUT_DEVICES, ...OUTPUT_DEVICES];
  const nowTs = formatTimestamp(new Date());

  for (const dev of allDevices) {
    const row = document.getElementById(`row-${dev}`);
    if (!row) continue;

    const hasKey = Object.prototype.hasOwnProperty.call(merged, dev);
    const rawVal = hasKey ? merged[dev] : undefined;
    const bit = normalizeBitValue(rawVal);

    const pill = document.getElementById(`row-state-${dev}`);
    const rawCode = document.getElementById(`row-raw-${dev}`);
    const bar = document.getElementById(`row-bar-${dev}`);
    const tsCol = document.getElementById(`row-ts-${dev}`);
    const dot = document.getElementById(`row-dot-${dev}`);

    if (bit === 1) {
      row.dataset.state = 'on';
      if (pill) { pill.className = 'table-state-pill on'; pill.textContent = 'ON'; }
      if (rawCode) rawCode.textContent = '1 (Active)';
      if (bar) bar.className = 'signal-bar active';
      if (dot) dot.className = 'status-dot dot-ok';
      if (tsCol) tsCol.textContent = nowTs;
    } else if (bit === 0) {
      row.dataset.state = 'off';
      if (pill) { pill.className = 'table-state-pill off'; pill.textContent = 'OFF'; }
      if (rawCode) rawCode.textContent = '0 (Inactive)';
      if (bar) bar.className = 'signal-bar';
      if (dot) dot.className = 'status-dot';
      if (tsCol) tsCol.textContent = nowTs;
    } else {
      row.dataset.state = 'unknown';
      if (pill) { pill.className = 'table-state-pill unknown'; pill.textContent = 'UNKNOWN'; }
      if (rawCode) rawCode.textContent = rawVal !== undefined ? JSON.stringify(rawVal) : 'Missing';
      if (bar) bar.className = 'signal-bar';
      if (dot) dot.className = 'status-dot dot-warn';
      if (tsCol) tsCol.textContent = '—';
    }
  }
}

// ─── Rendering: Ladder Contact Rung Visual ────────────────────────────────────

function updateLadderDiagram(inputs, outputs) {
  const x3Bit = inputs ? normalizeBitValue(inputs.X3) : null;
  const y20Bit = outputs ? normalizeBitValue(outputs.Y20) : null;

  // Determine mode badge text & styling
  let modeText = 'AWAITING TELEMETRY';
  let modeClass = 'ladder-mode-badge';
  if (state.isDemo) {
    modeText = 'DEMO SIMULATION';
    modeClass = 'ladder-mode-badge demo';
  } else if (state.connected && state.lastReadingTs) {
    modeText = 'LIVE PLC SCAN';
    modeClass = 'ladder-mode-badge live';
  }

  if (dom.ladderModeBadge) {
    dom.ladderModeBadge.textContent = modeText;
    dom.ladderModeBadge.className = modeClass;
  }
  if (dom.ladderIoModeBadge) {
    dom.ladderIoModeBadge.textContent = modeText;
    dom.ladderIoModeBadge.className = modeClass;
  }

  // Update Overview verified pair tiles
  const tileX3 = dom.overviewTileX3 || document.getElementById('overview-tile-x3');
  const badgeX3 = dom.overviewBadgeX3 || document.getElementById('overview-badge-x3');
  if (tileX3 && badgeX3) {
    if (x3Bit === 1) {
      tileX3.className = 'primary-signal-tile input-signal on';
      badgeX3.textContent = 'ON';
    } else if (x3Bit === 0) {
      tileX3.className = 'primary-signal-tile input-signal off';
      badgeX3.textContent = 'OFF';
    } else {
      tileX3.className = 'primary-signal-tile input-signal unknown';
      badgeX3.textContent = 'UNKNOWN';
    }
  }

  const tileY20 = dom.overviewTileY20 || document.getElementById('overview-tile-y20');
  const badgeY20 = dom.overviewBadgeY20 || document.getElementById('overview-badge-y20');
  if (tileY20 && badgeY20) {
    if (y20Bit === 1) {
      tileY20.className = 'primary-signal-tile output-signal on';
      badgeY20.textContent = 'ON';
    } else if (y20Bit === 0) {
      tileY20.className = 'primary-signal-tile output-signal off';
      badgeY20.textContent = 'OFF';
    } else {
      tileY20.className = 'primary-signal-tile output-signal unknown';
      badgeY20.textContent = 'UNKNOWN';
    }
  }

  // Helper to update contact X3 display
  function applyX3(elem, sym, stateElem) {
    if (!elem) return;
    if (x3Bit === 1) {
      elem.dataset.state = 'on';
      if (sym) sym.textContent = '──[ ⊣■⊢ ]──';
      if (stateElem) stateElem.textContent = 'CLOSED (ON)';
    } else if (x3Bit === 0) {
      elem.dataset.state = 'off';
      if (sym) sym.textContent = '──[ ⊣ ⊢ ]──';
      if (stateElem) stateElem.textContent = 'OPEN (OFF)';
    } else {
      elem.dataset.state = 'unknown';
      if (sym) sym.textContent = '──[ ⊣ ? ⊢ ]──';
      if (stateElem) stateElem.textContent = 'UNKNOWN';
    }
  }

  // Helper to update coil Y20 display
  function applyY20(elem, sym, stateElem) {
    if (!elem) return;
    if (y20Bit === 1) {
      elem.dataset.state = 'on';
      if (sym) sym.textContent = '──( ● )──';
      if (stateElem) stateElem.textContent = 'ENERGIZED (ON)';
    } else if (y20Bit === 0) {
      elem.dataset.state = 'off';
      if (sym) sym.textContent = '──( )──';
      if (stateElem) stateElem.textContent = 'DE-ENERGIZED (OFF)';
    } else {
      elem.dataset.state = 'unknown';
      if (sym) sym.textContent = '──( ? )──';
      if (stateElem) stateElem.textContent = 'UNKNOWN';
    }
  }

  // Update Overview Ladder
  applyX3(dom.ladderX3Elem, dom.ladderX3Symbol, dom.ladderX3State);
  applyY20(dom.ladderY20Elem, dom.ladderY20Symbol, dom.ladderY20State);

  // Update I/O Tab Ladder
  applyX3(dom.ladderIoX3Elem, dom.ladderIoX3Symbol, dom.ladderIoX3State);
  applyY20(dom.ladderIoY20Elem, dom.ladderIoY20Symbol, dom.ladderIoY20State);

  // Flow line & Wire highlights
  const isEnergized = (x3Bit === 1 && y20Bit === 1);
  if (dom.ladderFlowLine) dom.ladderFlowLine.classList.toggle('energized', isEnergized);
  if (dom.ladderIoFlowLine) dom.ladderIoFlowLine.classList.toggle('energized', isEnergized);

  // Power rails wire segment highlights
  document.querySelectorAll('.ladder-wire-segment').forEach(wire => {
    wire.classList.toggle('energized', isEnergized);
  });

  // Overview Continuity Status
  if (dom.ladderRungStatus && dom.ladderRungDot) {
    if (x3Bit === 1 && y20Bit === 1) {
      dom.ladderRungStatus.textContent = 'Logic Continuity: Energized (Conductive Scan Path)';
      dom.ladderRungDot.className = 'status-dot dot-ok';
    } else if (x3Bit === 0 || y20Bit === 0) {
      dom.ladderRungStatus.textContent = 'Logic Continuity: De-energized (Contact Open)';
      dom.ladderRungDot.className = 'status-dot';
    } else {
      dom.ladderRungStatus.textContent = 'Logic Continuity: Unknown / Awaiting Telemetry';
      dom.ladderRungDot.className = 'status-dot dot-warn';
    }
  }
}

// ─── Rendering: Point Counters ────────────────────────────────────────────────

function updateActivePointCounters(inputs, outputs) {
  let onCount = 0;
  let unknownCount = 0;
  const merged = { ...inputs, ...outputs };
  const allDevices = [...INPUT_DEVICES, ...OUTPUT_DEVICES];

  for (const d of allDevices) {
    const bit = normalizeBitValue(merged[d]);
    if (bit === 1) onCount++;
    else if (bit === null) unknownCount++;
  }

  if (dom.metaActiveCount) dom.metaActiveCount.textContent = `${onCount} / 16 Active`;
  if (dom.metaUnknownCount) dom.metaUnknownCount.textContent = `${unknownCount} unknown`;
}

// ─── Subsystem Health Matrix Updates ──────────────────────────────────────────

function updateSubsystemMatrix() {
  // 1. PLC Subsystem
  if (state.plcConnected) {
    setSubsystemBadge('plc', 'ok', 'COMMUNICATING', 'MC 3E protocol link online');
  } else if (state.connected && state.stats.lastFault) {
    setSubsystemBadge('plc', 'error', 'FAULT', state.stats.lastFault);
  } else {
    setSubsystemBadge('plc', 'warn', 'UNVERIFIED', 'Awaiting gateway handshake');
  }

  // 2. Host Gateway
  if (state.gwActive && !state.isStale) {
    setSubsystemBadge('gw', 'ok', 'STREAMING', 'Serial telemetry nominal');
  } else if (state.isStale) {
    setSubsystemBadge('gw', 'warn', 'STALE', 'No serial stream >15s');
  } else {
    setSubsystemBadge('gw', 'warn', 'UNKNOWN', 'Daemon not verified');
  }

  // 3. Edge Bridge (ESP8266)
  if (state.espOnline) {
    setSubsystemBadge('esp', 'ok', 'ONLINE', 'Wi-Fi MQTT bridge active');
  } else {
    setSubsystemBadge('esp', 'warn', 'UNKNOWN', 'Awaiting bridge status');
  }

  // 4. Broker
  if (state.connected) {
    setSubsystemBadge('mqtt', 'ok', 'CONNECTED', 'WSS TLS 1.2 link open');
  } else {
    setSubsystemBadge('mqtt', 'error', 'DISCONNECTED', 'Client disconnected');
  }
}

function setSubsystemBadge(system, level, badgeText, detailText) {
  const badgeMap = {
    plc:  dom.badgeSubsystemPlc,
    gw:   dom.badgeSubsystemGw,
    esp:  dom.badgeSubsystemEsp,
    mqtt: dom.badgeSubsystemMqtt,
  };
  const detailMap = {
    plc:  dom.detailSubsystemPlc,
    gw:   dom.detailSubsystemGw,
    esp:  dom.detailSubsystemEsp,
    mqtt: dom.detailSubsystemMqtt,
  };

  const badgeElem  = badgeMap[system];
  const detailElem = detailMap[system];

  if (badgeElem) {
    badgeElem.className = `node-badge ${level}`;
    badgeElem.textContent = badgeText;
  }
  if (detailElem && detailText) {
    detailElem.textContent = detailText;
  }
}

// ─── Stale Data Handling ──────────────────────────────────────────────────────

function markAllStale() {
  if (!state.lastPayload) {
    hideStaleBanner();
    return;
  }
  state.isStale = true;
  if (dom.staleBannerText) {
    dom.staleBannerText.textContent =
      'Data is stale — communication interrupted. Values shown below reflect previous reading and not live hardware.';
  }
  showStaleBanner();

  document.querySelectorAll('.device-tile').forEach(tile => {
    if (tile.dataset.state !== 'unknown') {
      tile.dataset.state = 'stale';
      const badge = tile.querySelector('.tile-state-badge');
      if (badge) badge.textContent = 'STALE';
      tile.setAttribute('aria-label', `${tile.dataset.device}: stale`);
    }
  });

  document.querySelectorAll('#io-table-body tr').forEach(row => {
    if (row.dataset.state !== 'unknown') {
      row.dataset.state = 'stale';
      const pill = row.querySelector('.table-state-pill');
      if (pill) { pill.className = 'table-state-pill unknown'; pill.textContent = 'STALE'; }
    }
  });

  document.querySelectorAll('.quick-bit').forEach(qb => {
    if (!qb.classList.contains('unknown')) {
      const isVerified = qb.classList.contains('verified-bit');
      qb.className = isVerified ? 'quick-bit verified-bit stale' : 'quick-bit stale';
    }
  });

  const tileX3 = dom.overviewTileX3 || document.getElementById('overview-tile-x3');
  const badgeX3 = dom.overviewBadgeX3 || document.getElementById('overview-badge-x3');
  if (tileX3 && badgeX3 && tileX3.classList.contains('on')) {
    tileX3.className = 'primary-signal-tile input-signal stale';
    badgeX3.textContent = 'STALE';
  }

  const tileY20 = dom.overviewTileY20 || document.getElementById('overview-tile-y20');
  const badgeY20 = dom.overviewBadgeY20 || document.getElementById('overview-badge-y20');
  if (tileY20 && badgeY20 && tileY20.classList.contains('on')) {
    tileY20.className = 'primary-signal-tile output-signal stale';
    badgeY20.textContent = 'STALE';
  }

  updateFreshnessDisplays('stale', 'STALE (>15s)', 'Telemetry timeout');
}

function startAgeTimer() {
  state.ageTimerId = setInterval(() => {
    if (!state.lastReadingTs) {
      if (dom.metaDataAge)  dom.metaDataAge.textContent  = '—';
      if (dom.navHeaderAge) dom.navHeaderAge.textContent = 'Age: —';
      updateFreshnessDisplays('idle', 'IDLE', 'No telemetry stream');
      return;
    }

    const ageMs = Date.now() - state.lastReadingTs.getTime();
    const formattedAge = formatAge(ageMs);
    if (dom.metaDataAge)  dom.metaDataAge.textContent  = formattedAge;
    if (dom.navHeaderAge) dom.navHeaderAge.textContent = `Age: ${formattedAge}`;

    if (ageMs > STALE_THRESHOLD_MS && !state.isStale && (state.connected || state.isDemo)) {
      state.isStale = true;
      if (state.lastPayload) {
        showStaleBanner();
        markAllStale();
        addHistoryItem('comm_error', 'warn', 'Data stale: No new telemetry received for >15 seconds');
      }
    } else if (ageMs <= 5000) {
      updateFreshnessDisplays('live', 'LIVE', `${formatAge(ageMs)}`);
    } else if (ageMs <= STALE_THRESHOLD_MS) {
      updateFreshnessDisplays('warn', 'DEGRADED', `${formatAge(ageMs)}`);
    }
  }, AGE_UPDATE_INTERVAL);
}

function updateFreshnessDisplays(level, stateText, subText) {
  if (dom.metaFreshnessState) {
    dom.metaFreshnessState.textContent = stateText;
    dom.metaFreshnessState.className = `meta-val highlight ${level}`;
  }
  if (dom.metaFreshnessSub && subText) {
    dom.metaFreshnessSub.textContent = subText;
  }
  if (dom.navFreshness) {
    dom.navFreshness.textContent = `Data: ${stateText}`;
    dom.navFreshness.className = `nav-freshness-pill ${level}`;
  }
}

function showStaleBanner() { if (dom.staleBanner) dom.staleBanner.hidden = false; }
function hideStaleBanner() { if (dom.staleBanner) dom.staleBanner.hidden = true; }

// ─── Status UI Helpers ────────────────────────────────────────────────────────

function setMqttStatus(level, text) { setStatusPill('mqtt', level, text); }

function setStatusPill(name, level, text) {
  const pillMap = { mqtt: dom.pillMqtt, plc: dom.pillPlc, esp: dom.pillEsp, gw: dom.pillGw };
  const dotMap  = { mqtt: dom.dotMqtt,  plc: dom.dotPlc,  esp: dom.dotEsp,  gw: dom.dotGw };
  const textMap = { mqtt: dom.textMqtt, plc: dom.textPlc, esp: dom.textEsp, gw: dom.textGw };

  const dot  = dotMap[name];
  const textElem = textMap[name];
  const pill = pillMap[name];

  if (dot) {
    dot.className = 'status-dot';
    if (level === 'ok')         dot.classList.add('dot-ok');
    else if (level === 'error') dot.classList.add('dot-error');
    else if (level === 'warn')  dot.classList.add('dot-warn');
    else if (level === 'connecting') dot.classList.add('dot-connecting');
  }
  if (textElem) textElem.textContent = text;
  if (pill) pill.setAttribute('aria-label', `${name.toUpperCase()} Status: ${text}`);
}

// ─── Event Journal Engine ─────────────────────────────────────────────────────

function addHistoryItem(category, severity, message) {
  const now = new Date();
  const item = {
    id:       Date.now() + Math.random(),
    tsIso:    now.toISOString(),
    tsLocal:  formatTimestamp(now),
    category, // 'state_change' | 'comm_error' | 'system'
    severity, // 'info' | 'warn' | 'error'
    message,
  };

  state.historyItems.unshift(item);
  if (state.historyItems.length > MAX_HISTORY_ITEMS) {
    state.historyItems.pop();
  }

  renderEventJournal();
  renderMiniEvents();
}

function renderEventJournal() {
  if (!dom.historyList) return;

  const filtered = state.historyItems.filter(item => {
    // Category filter
    if (state.eventCategory !== 'all' && item.category !== state.eventCategory) return false;
    // Severity filter
    if (state.eventSeverity !== 'all' && item.severity !== state.eventSeverity) return false;
    // Search query
    if (state.eventSearch) {
      const matchMsg = item.message.toLowerCase().includes(state.eventSearch);
      const matchCat = item.category.toLowerCase().includes(state.eventSearch);
      if (!matchMsg && !matchCat) return false;
    }
    return true;
  });

  if (dom.historyCount)  dom.historyCount.textContent  = `${filtered.length} / ${state.historyItems.length}`;
  if (dom.navEventCount) dom.navEventCount.textContent = state.historyItems.length;

  if (filtered.length === 0) {
    dom.historyList.innerHTML = '<li class="history-empty">No events match the selected filters or search query.</li>';
    return;
  }

  dom.historyList.innerHTML = filtered.map(item => `
    <li class="history-item" role="listitem">
      <span class="history-ts">${item.tsLocal}</span>
      <span class="history-tag tag-${item.category}">${categoryLabel(item.category)}</span>
      <span class="history-sev sev-${item.severity}">${item.severity.toUpperCase()}</span>
      <span class="history-msg">${escapeHtml(item.message)}</span>
    </li>
  `).join('');
}

function renderMiniEvents() {
  if (!dom.miniEventList) return;
  const recent = state.historyItems.slice(0, 4);
  if (recent.length === 0) {
    dom.miniEventList.innerHTML = '<li class="mini-event-empty">No supervisory events logged yet. Connect or activate Demo mode.</li>';
    return;
  }
  dom.miniEventList.innerHTML = recent.map(item => `
    <li class="mini-event-item">
      <span class="mini-event-ts">${item.tsLocal.split(' ')[1] || item.tsLocal}</span>
      <span class="history-tag tag-${item.category}">${categoryLabel(item.category)}</span>
      <span class="mini-event-msg">${escapeHtml(item.message)}</span>
    </li>
  `).join('');
}

function setEventCategory(cat) {
  state.eventCategory = cat;
  document.querySelectorAll('.filter-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === cat);
    b.setAttribute('aria-pressed', b.dataset.filter === cat ? 'true' : 'false');
  });
  renderEventJournal();
}

function setEventSeverity(sev) {
  state.eventSeverity = sev;
  document.querySelectorAll('.filter-btn-sev').forEach(b => {
    b.classList.toggle('active', b.dataset.severity === sev);
    b.setAttribute('aria-pressed', b.dataset.severity === sev ? 'true' : 'false');
  });
  renderEventJournal();
}

function clearHistory() {
  state.historyItems = [];
  renderEventJournal();
  renderMiniEvents();
}

function exportEventsCsv() {
  if (state.historyItems.length === 0) {
    alert('No events to export in the journal.');
    return;
  }

  const headers = ['Timestamp_UTC', 'Timestamp_Local', 'Category', 'Severity', 'Message'];
  const rows = state.historyItems.map(item => [
    `"${item.tsIso}"`,
    `"${item.tsLocal}"`,
    `"${item.category}"`,
    `"${item.severity}"`,
    `"${item.message.replace(/"/g, '""')}"`
  ]);

  const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `qplc_event_journal_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function categoryLabel(cat) {
  if (cat === 'state_change') return 'I/O Change';
  if (cat === 'comm_error')   return 'Comms Fault';
  if (cat === 'system')       return 'System';
  return cat;
}

// ─── Offline Demo Mode Engine ─────────────────────────────────────────────────

function handleDemoToggle() {
  state.isDemo = dom.chkDemo ? dom.chkDemo.checked : false;

  if (state.isDemo) {
    if (state.connected) handleDisconnect();
    if (dom.demoBanner) dom.demoBanner.hidden = false;
    if (dom.btnConnect) dom.btnConnect.disabled = true;
    if (dom.headerModeTag) {
      dom.headerModeTag.textContent = 'OFFLINE DEMO';
      dom.headerModeTag.className   = 'system-mode-tag demo';
    }
    state.demoPaused = false;
    state.demoScenario = 'normal';
    updatePauseButtonUI();
    updateDemoScenarioUI('normal');
    startDemoMode();
    addHistoryItem('system', 'info', '[DEMO] Offline Demo simulation started — synthetic telemetry active');
  } else {
    if (dom.demoBanner) dom.demoBanner.hidden = true;
    if (dom.btnConnect) dom.btnConnect.disabled = false;
    if (dom.headerModeTag) {
      dom.headerModeTag.textContent = 'READ-ONLY';
      dom.headerModeTag.className   = 'system-mode-tag';
    }
    stopDemoMode();
    resetAllTiles();
    addHistoryItem('system', 'info', '[DEMO] Offline Demo simulation terminated — workspace reset');
  }
}

function startDemoMode() {
  stopDemoTimers();
  pushDemoReading();
  state.demoInterval = setInterval(pushDemoReading, DEMO_POLL_INTERVAL);
}

function stopDemoTimers() {
  if (state.demoInterval) {
    clearInterval(state.demoInterval);
    state.demoInterval = null;
  }
  if (state.demoTimeoutId) {
    clearTimeout(state.demoTimeoutId);
    state.demoTimeoutId = null;
  }
}

function stopDemoMode() {
  stopDemoTimers();
  state.lastPayload    = null;
  state.lastReadingTs  = null;
  state.isStale        = false;
  state.plcConnected   = false;
  state.espOnline      = false;
  state.gwActive       = false;
  state.demoPaused     = false;
  state.lastSeq        = null; // Reset sequence tracking for clean live session
  hideStaleBanner();
  setStatusPill('mqtt', 'off', 'Disconnected');
  setStatusPill('plc',  'off', 'Unknown');
  setStatusPill('esp',  'off', 'Unknown');
  setStatusPill('gw',   'off', 'Unknown');
  updateSubsystemMatrix();
  updateLadderDiagram(null, null);
}

function pushDemoReading() {
  if (!state.isDemo || state.demoPaused) return;

  state.demoStep++;
  state.demoSeq++;

  // Toggle X3 every 4 s; Y20 energizes in sync (ladder contact simulation)
  const tick = Math.floor(Date.now() / 4000) % 2;

  const fakeMsg = {
    type: 'plc_reading',
    gw_id: 'demo-gateway',
    seq: state.demoSeq,
    ts: new Date().toISOString().replace(/\.\d+/, '') + 'Z',
    connected: true,
    inputs: {
      X0: 0, X1: 0, X2: 0, X3: tick,
      X4: 0, X5: 0, X6: 0, X7: 0,
    },
    outputs: {
      Y20: tick, // Ladder rung 1: X3 contact energizes Y20 coil
      Y21: 0, Y22: 0, Y23: 0, Y24: 0, Y25: 0, Y26: 0, Y27: 0,
    },
  };

  setStatusPill('mqtt', 'warn', 'Demo');
  setStatusPill('plc',  'ok',   'Simulated');
  setStatusPill('esp',  'ok',   'Simulated');
  setStatusPill('gw',   'ok',   'Simulated');
  processTelemetry(fakeMsg);
}

function setDemoScenario(scenario) {
  if (!state.isDemo) {
    if (dom.chkDemo) {
      dom.chkDemo.checked = true;
      handleDemoToggle();
    }
  }

  state.demoScenario = scenario;
  updateDemoScenarioUI(scenario);

  if (scenario === 'normal') {
    state.demoPaused = false;
    updatePauseButtonUI();
    startDemoMode();
    addHistoryItem('system', 'info', '[DEMO] Active Scenario: Normal 2s Telemetry cycle');
  } else if (scenario === 'delayed') {
    // Stop regular interval, delay next frame by 8s
    stopDemoTimers();
    state.demoPaused = false;
    updatePauseButtonUI();
    addHistoryItem('comm_error', 'warn', '[DEMO] Active Scenario: Delayed Telemetry — frame paused for 8 seconds to demonstrate latency');
    state.demoTimeoutId = setTimeout(() => {
      pushDemoReading();
      state.demoInterval = setInterval(pushDemoReading, DEMO_POLL_INTERVAL);
      addHistoryItem('system', 'info', '[DEMO] Delayed frame delivered; resuming nominal cadence');
    }, 8000);
  } else if (scenario === 'seq_gap') {
    // Advance sequence number ahead by +4
    state.demoSeq += 4;
    pushDemoReading();
    addHistoryItem('comm_error', 'warn', '[DEMO] Active Scenario: Sequence Gap — injected jump (+3 dropped frames simulated)');
  } else if (scenario === 'stale') {
    // Stop frames completely so age timer triggers STALE warning after 15s
    stopDemoTimers();
    addHistoryItem('comm_error', 'warn', '[DEMO] Active Scenario: Stale Data — telemetry delivery halted to trigger >15s STALE alert');
  } else if (scenario === 'gw_fault') {
    // Dispatch simulated gateway comm failure
    stopDemoTimers();
    const errorMsg = {
      type: 'gateway_error',
      gw_id: 'demo-gateway',
      seq: ++state.demoSeq,
      ts: new Date().toISOString().replace(/\.\d+/, '') + 'Z',
      error: 'Serial COM9 Read Timeout (Simulated)',
      connected: false
    };
    processTelemetry(errorMsg);
    setStatusPill('gw', 'error', 'Fault (Sim)');
    setStatusPill('plc', 'error', 'Lost (Sim)');
    addHistoryItem('comm_error', 'error', '[DEMO] Active Scenario: Gateway Fault — Serial COM9 Timeout simulated');

    // Automatically recover after 6 seconds
    state.demoTimeoutId = setTimeout(() => {
      addHistoryItem('system', 'info', '[DEMO] Gateway communication recovered back online; resuming telemetry');
      startDemoMode();
    }, 6000);
  }
}

function toggleDemoPause() {
  if (!state.isDemo) return;
  state.demoPaused = !state.demoPaused;
  updatePauseButtonUI();
  if (!state.demoPaused && !state.demoInterval) {
    startDemoMode();
  }
  addHistoryItem('system', 'info', state.demoPaused ? '[DEMO] Simulation paused by operator' : '[DEMO] Simulation resumed');
}

function resetDemoSimulation() {
  if (!state.isDemo) return;
  stopDemoTimers();
  state.demoSeq = 100;
  state.demoStep = 0;
  state.demoPaused = false;
  state.demoScenario = 'normal';
  updatePauseButtonUI();
  updateDemoScenarioUI('normal');
  resetAllTiles();
  startDemoMode();
  addHistoryItem('system', 'info', '[DEMO] Simulation reset to initial state (Seq: 100)');
}

function updatePauseButtonUI() {
  if (dom.demoPauseText) {
    dom.demoPauseText.textContent = state.demoPaused ? 'Resume' : 'Pause';
  }
  if (dom.btnDemoPause) {
    dom.btnDemoPause.classList.toggle('active', state.demoPaused);
    dom.btnDemoPause.setAttribute('aria-pressed', state.demoPaused ? 'true' : 'false');
  }
}

function updateDemoScenarioUI(scenario) {
  document.querySelectorAll('.demo-scenario-pill, .scenario-btn').forEach(btn => {
    const isTarget = btn.dataset.scenario === scenario;
    btn.classList.toggle('active', isTarget);
    btn.setAttribute('aria-pressed', isTarget ? 'true' : 'false');
  });

  if (dom.selectDemoScenario) {
    dom.selectDemoScenario.value = scenario;
  }

  const labelMap = {
    normal:   'Normal Telemetry (2s Cycle)',
    delayed:  'Delayed Telemetry (8s Latency)',
    seq_gap:  'Sequence Gap (+3 Dropped)',
    stale:    'Stale Data Warning (>15s)',
    gw_fault: 'Gateway Fault & Recovery'
  };

  if (dom.demoScenarioBadge) {
    dom.demoScenarioBadge.textContent = `Scenario: ${labelMap[scenario] || scenario}`;
  }
}

function resetAllTiles() {
  document.querySelectorAll('.device-tile').forEach(tile => {
    tile.classList.add('loading');
    tile.dataset.state = 'unknown';
    const badge = tile.querySelector('.tile-state-badge');
    if (badge) badge.textContent = '—';
    tile.setAttribute('aria-label', `${tile.dataset.device}: unknown`);
  });

  document.querySelectorAll('#io-table-body tr').forEach(row => {
    row.dataset.state = 'unknown';
    const pill = row.querySelector('.table-state-pill');
    if (pill) { pill.className = 'table-state-pill unknown'; pill.textContent = 'UNKNOWN'; }
    const raw = row.querySelector('code[id^="row-raw"]');
    if (raw) raw.textContent = '—';
    const bar = row.querySelector('.signal-bar');
    if (bar) bar.className = 'signal-bar';
    const tsCol = row.querySelector('.point-ts');
    if (tsCol) tsCol.textContent = '—';
  });

  updateLadderDiagram(null, null);

  if (dom.metaGatewayId)    dom.metaGatewayId.textContent    = '—';
  if (dom.metaLastTs)       dom.metaLastTs.textContent       = '—';
  if (dom.metaDataAge)      dom.metaDataAge.textContent      = '—';
  if (dom.metaSeq)          dom.metaSeq.textContent          = '—';
  if (dom.metaActiveCount)  dom.metaActiveCount.textContent  = '0 / 16 Active';
  if (dom.metaUnknownCount) dom.metaUnknownCount.textContent = '16 unknown';
}

// ─── Formatting Helpers ───────────────────────────────────────────────────────

function formatTimestamp(d) {
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} `
       + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatIsoShort(iso) {
  if (!iso) return '—';
  return iso.replace('T', ' ').replace('Z', '');
}

function formatAge(ms) {
  if (ms < 1000)  return '<1 s ago';
  if (ms < 60000) return `${Math.floor(ms/1000)} s ago`;
  const m = Math.floor(ms/60000);
  const s = Math.floor((ms % 60000)/1000);
  return `${m}m ${s}s ago`;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);
