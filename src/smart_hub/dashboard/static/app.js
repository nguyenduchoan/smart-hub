// Smart Hub Local Dashboard Client-Side Logic
let CSRF_TOKEN = '';
let activeApplianceId = null;
let currentReviewSample = null;
let recordingPollInterval = null;

// Utility: UUID generator for idempotent request IDs
function generateUUID() {
  return 'req_' + Math.random().toString(36).substring(2, 9) + Date.now().toString(36);
}

// Utility: Toast notifications
function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast-msg toast-${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

// Utility: Authenticated fetch with CSRF header
async function apiFetch(url, options = {}) {
  options.headers = options.headers || {};
  if (CSRF_TOKEN) {
    options.headers['X-CSRF-Token'] = CSRF_TOKEN;
  }
  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(url, options);
  if (!res.ok) {
    let errDetail = res.statusText;
    try {
      const errJson = await res.json();
      errDetail = errJson.detail || JSON.stringify(errJson);
    } catch (_) {}
    throw new Error(errDetail);
  }
  return res.json();
}

// --- App Initialization ---
document.addEventListener('DOMContentLoaded', async () => {
  // 1. Fetch CSRF token
  try {
    const data = await fetch('/api/csrf-token').then(r => r.json());
    CSRF_TOKEN = data.csrf_token;
  } catch (e) {
    console.error('Failed to get CSRF token', e);
  }

  // 2. Setup tab navigation
  setupNavigation();

  // 3. Setup modals
  setupModals();

  // 4. Initial load
  loadOverview();
  loadGateways();
  loadAppliances();
  loadSamples();
  loadWakeCandidates();
  loadEvaluations();

  // 5. Periodic status poll (every 4 seconds)
  setInterval(pollSystemStatus, 4000);
  pollSystemStatus();
});

// --- Tab Navigation ---
function setupNavigation() {
  const buttons = document.querySelectorAll('.nav-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      buttons.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPane = document.getElementById(`tab-${tabId}`);
      if (targetPane) targetPane.classList.add('active');

      // Refresh specific tab contents when navigated
      if (tabId === 'overview') loadOverview();
      else if (tabId === 'devices') { loadGateways(); loadAppliances(); }
      else if (tabId === 'data') loadSamples();
      else if (tabId === 'wake') loadWakeCandidates();
      else if (tabId === 'results') loadEvaluations();
    });
  });
}

// --- Modals Management ---
function setupModals() {
  // Modal open buttons
  document.getElementById('btn-open-gw-modal')?.addEventListener('click', () => {
    openModal('modal-gateway');
  });

  document.getElementById('btn-open-dev-modal')?.addEventListener('click', async () => {
    await populateGatewaySelect();
    await populateBrandSelect();
    openModal('modal-appliance');
  });

  document.getElementById('btn-panel-learn')?.addEventListener('click', () => {
    if (!activeApplianceId) return;
    openModal('modal-learning');
  });

  document.getElementById('btn-open-create-candidate')?.addEventListener('click', async () => {
    await populateCandidateSampleSelector();
    openModal('modal-candidate');
  });

  document.getElementById('btn-open-run-eval')?.addEventListener('click', async () => {
    await populateEvalCandidateSelector();
    openModal('modal-run-eval');
  });

  document.getElementById('btn-open-catalog-import')?.addEventListener('click', () => {
    closeModal('modal-appliance');
    openModal('modal-import-catalog');
  });

  // Modal close buttons
  document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', () => {
      const modalId = btn.getAttribute('data-close');
      closeModal(modalId);
    });
  });

  // Close on clicking backdrop
  document.querySelectorAll('.modal-backdrop').forEach(modal => {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.style.display = 'none';
    });
  });

  // Gateway modal subtabs
  document.getElementById('tab-gw-scan-btn')?.addEventListener('click', () => {
    document.getElementById('tab-gw-scan-btn').classList.add('active');
    document.getElementById('tab-gw-ip-btn').classList.remove('active');
    document.getElementById('gw-scan-section').style.display = 'block';
    document.getElementById('gw-ip-section').style.display = 'none';
  });

  document.getElementById('tab-gw-ip-btn')?.addEventListener('click', () => {
    document.getElementById('tab-gw-ip-btn').classList.add('active');
    document.getElementById('tab-gw-scan-btn').classList.remove('active');
    document.getElementById('gw-scan-section').style.display = 'none';
    document.getElementById('gw-ip-section').style.display = 'block';
  });
}

function openModal(modalId) {
  const m = document.getElementById(modalId);
  if (m) m.style.display = 'flex';
}

function closeModal(modalId) {
  const m = document.getElementById(modalId);
  if (m) m.style.display = 'none';
}

// --- Overview Tab ---
async function pollSystemStatus() {
  try {
    const status = await apiFetch('/api/status');
    // Mic status
    const micDot = document.querySelector('#badge-mic .status-dot');
    const micText = document.getElementById('status-mic-text');
    if (status.mic_busy) {
      micDot.className = 'status-dot dot-busy';
      micText.textContent = 'Đang bận';
    } else {
      micDot.className = 'status-dot dot-free';
      micText.textContent = 'Rảnh';
    }

    // Gateway status
    const gwDot = document.querySelector('#badge-gateway .status-dot');
    const gwText = document.getElementById('status-gw-text');
    if (status.online_gateways > 0) {
      gwDot.className = 'status-dot dot-online';
      gwText.textContent = `${status.online_gateways} Online`;
    } else {
      gwDot.className = 'status-dot dot-offline';
      gwText.textContent = 'Chưa kết nối';
    }

    // Counters
    document.getElementById('overview-gw-count').textContent = status.gateways_count;
    document.getElementById('overview-gw-sub').textContent = `${status.online_gateways} online`;
    document.getElementById('overview-dev-count').textContent = status.appliances_count;
    document.getElementById('overview-samples-count').textContent = status.samples_total;
    document.getElementById('overview-accepted-sub').textContent = `${status.accepted_samples} đã duyệt accepted`;
    document.getElementById('overview-pending-count').textContent = status.pending_reviews;
  } catch (e) {
    console.error('Failed to poll status', e);
  }
}

document.getElementById('btn-refresh-overview')?.addEventListener('click', () => {
  pollSystemStatus();
  showToast('Đã làm mới thông tin tổng quan', 'success');
});

// --- Devices & Gateways Tab ---
let discoveredGatewayTemp = null;

document.getElementById('btn-trigger-scan')?.addEventListener('click', async () => {
  const box = document.getElementById('scan-results-box');
  box.innerHTML = '<div class="spinner"></div> Đang quét mạng LAN tìm Broadlink RM4...';
  try {
    const res = await apiFetch('/api/gateway-discoveries', { method: 'POST' });
    if (!res.gateways || res.gateways.length === 0) {
      box.innerHTML = '<div class="alert alert-warning">Không tìm thấy thiết bị Broadlink nào qua broadcast UDP. Hãy thử chuyển sang tab <strong>Nhập IP trực tiếp</strong>.</div>';
      return;
    }
    box.innerHTML = '';
    res.gateways.forEach(gw => {
      const item = document.createElement('div');
      item.className = 'scan-item p-2 border rounded mb-2';
      item.innerHTML = `
        <strong>${gw.model_name}</strong> (${gw.ip_address})<br>
        <small class="text-muted">MAC: ${gw.mac} | DevType: 0x${gw.devtype.toString(16)}</small>
        <button class="btn btn-sm btn-primary float-right mt-1">Chọn gateway này</button>
      `;
      item.querySelector('button').addEventListener('click', () => {
        selectGatewayForSave(gw);
      });
      box.appendChild(item);
    });
  } catch (err) {
    box.innerHTML = `<div class="alert alert-danger">Lỗi quét: ${err.message}</div>`;
  }
});

document.getElementById('btn-trigger-ip-probe')?.addEventListener('click', async () => {
  const ip = document.getElementById('input-gw-ip').value.trim();
  if (!ip) {
    showToast('Vui lòng nhập IP', 'error');
    return;
  }
  const box = document.getElementById('ip-probe-results-box');
  box.innerHTML = `<div class="spinner"></div> Đang kết nối tới ${ip}...`;
  try {
    const gw = await apiFetch('/api/gateways/discover-ip', {
      method: 'POST',
      body: { ip_address: ip, timeout: 5.0 },
    });
    box.innerHTML = `
      <div class="alert alert-success">
        Kết nối thành công!<br>
        <strong>${gw.model_name}</strong> - MAC: ${gw.mac}
      </div>
    `;
    selectGatewayForSave(gw);
  } catch (err) {
    box.innerHTML = `<div class="alert alert-danger">Không thể kết nối: ${err.message}</div>`;
  }
});

function selectGatewayForSave(gw) {
  discoveredGatewayTemp = gw;
  document.getElementById('gw-save-form').style.display = 'block';
  document.getElementById('save-gw-name').value = `RM4 mini (${gw.ip_address})`;
}

document.getElementById('btn-save-gateway-confirm')?.addEventListener('click', async () => {
  if (!discoveredGatewayTemp) return;
  const name = document.getElementById('save-gw-name').value.trim();
  const room = document.getElementById('save-gw-room').value.trim();
  try {
    await apiFetch('/api/gateways', {
      method: 'POST',
      body: {
        id: discoveredGatewayTemp.id,
        provider: discoveredGatewayTemp.provider,
        model_name: discoveredGatewayTemp.model_name,
        ip_address: discoveredGatewayTemp.ip_address,
        mac: discoveredGatewayTemp.mac,
        devtype: discoveredGatewayTemp.devtype,
        name: name || discoveredGatewayTemp.model_name,
        room: room || 'Phòng Khách',
        is_locked: discoveredGatewayTemp.is_locked,
        fwversion: discoveredGatewayTemp.fwversion,
      },
    });
    showToast('Đã lưu Gateway thành công!', 'success');
    closeModal('modal-gateway');
    loadGateways();
    pollSystemStatus();
  } catch (err) {
    showToast(`Lỗi lưu gateway: ${err.message}`, 'error');
  }
});

async function loadGateways() {
  const container = document.getElementById('gateways-list');
  try {
    const gateways = await apiFetch('/api/gateways');
    if (!gateways || gateways.length === 0) {
      container.innerHTML = '<div class="empty-state">Chưa có RM4 nào được lưu. Bấm "Kết nối RM4 mini" để bắt đầu.</div>';
      return;
    }
    container.innerHTML = '';
    gateways.forEach(gw => {
      const card = document.createElement('div');
      card.className = 'gw-item-card';
      card.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-1">
          <strong>${gw.name}</strong>
          <span class="badge ${gw.status === 'online' ? 'badge-success' : 'badge-danger'}">${gw.status}</span>
        </div>
        <div class="small text-muted mb-2">
          ${gw.room} · IP: <code>${gw.ip_address}</code> · MAC: <code>${gw.mac}</code>
        </div>
        <button class="btn btn-sm btn-secondary btn-check-gw">🔍 Kiểm tra kết nối</button>
      `;
      card.querySelector('.btn-check-gw').addEventListener('click', async () => {
        try {
          const res = await apiFetch(`/api/gateways/${gw.id}/checks`, { method: 'POST' });
          showToast(`[${gw.name}] ${res.message}`, res.is_online ? 'success' : 'error');
          loadGateways();
        } catch (e) {
          showToast(`Lỗi kiểm tra gateway: ${e.message}`, 'error');
        }
      });
      container.appendChild(card);
    });
  } catch (e) {
    container.innerHTML = `<div class="text-danger">Lỗi tải gateway: ${e.message}</div>`;
  }
}

document.getElementById('btn-refresh-gateways')?.addEventListener('click', () => {
  loadGateways();
});

// --- Appliances & Remote Panel ---
async function loadAppliances() {
  const container = document.getElementById('appliances-list');
  try {
    const appliances = await apiFetch('/api/devices');
    if (!appliances || appliances.length === 0) {
      container.innerHTML = '<div class="empty-state">Chưa có thiết bị nào.</div>';
      return;
    }
    container.innerHTML = '';
    appliances.forEach(app => {
      const item = document.createElement('div');
      item.className = `dev-nav-item ${app.id === activeApplianceId ? 'active' : ''}`;
      item.innerHTML = `
        <div class="dev-item-title">${app.name}</div>
        <div class="dev-item-sub">${app.room || 'Chưa phân phòng'} · ${app.brand} · ${app.buttons_count} nút</div>
      `;
      item.addEventListener('click', () => {
        selectAppliance(app.id);
      });
      container.appendChild(item);
    });

    if (activeApplianceId) {
      selectAppliance(activeApplianceId);
    }
  } catch (e) {
    container.innerHTML = `<div class="text-danger">Lỗi tải thiết bị: ${e.message}</div>`;
  }
}

async function selectAppliance(appId) {
  activeApplianceId = appId;
  document.querySelectorAll('.dev-nav-item').forEach(el => el.classList.remove('active'));
  // Re-highlight
  const allItems = document.querySelectorAll('.dev-nav-item');
  // Load appliance detail
  try {
    const app = await apiFetch(`/api/devices/${appId}`);
    document.getElementById('appliance-panel-placeholder').style.display = 'none';
    const panel = document.getElementById('appliance-active-panel');
    panel.style.display = 'block';

    document.getElementById('panel-dev-name').textContent = app.name;
    document.getElementById('panel-dev-meta').textContent = `${app.room || 'Phòng'} · Hãng: ${app.brand} (${app.model}) · Gateway: ${app.gateway ? app.gateway.name : 'N/A'}`;

    // Render remote buttons
    renderRemoteButtons(app);

    // Render observations log
    renderObservationsLog(app.recent_observations || []);
  } catch (e) {
    showToast(`Lỗi mở bảng điều khiển: ${e.message}`, 'error');
  }
}

function renderRemoteButtons(app) {
  const grid = document.getElementById('remote-buttons-grid');
  grid.innerHTML = '';
  if (!app.buttons || app.buttons.length === 0) {
    grid.innerHTML = '<div class="text-muted p-3">Thiết bị chưa có nút hoặc preset nào. Hãy bấm "Học thêm nút IR" để bắt đầu gán nút.</div>';
    return;
  }

  app.buttons.forEach(btn => {
    const b = document.createElement('button');
    b.className = `remote-btn ${btn.button_key.includes('power') ? 'btn-power' : ''} ${btn.is_verified ? 'btn-verified' : ''}`;
    b.innerHTML = `
      <span>${btn.button_name || btn.button_key}</span>
      ${btn.is_verified ? '<span class="btn-badge-verified">✓ Đã xác minh</span>' : '<span class="text-muted" style="font-size:10px;">Chưa kiểm chứng</span>'}
    `;
    b.addEventListener('click', () => {
      sendIRButton(app.id, btn);
    });
    grid.appendChild(b);
  });
}

let lastSentContext = null;

async function sendIRButton(appId, btn) {
  const reqId = generateUUID();
  const obsBox = document.getElementById('observation-box');
  const obsText = document.getElementById('obs-result-text');
  obsBox.style.display = 'block';
  obsText.textContent = `Đang gửi lệnh '${btn.button_name}' tới RM4 mini...`;

  try {
    const res = await apiFetch(`/api/devices/${appId}/actions`, {
      method: 'POST',
      body: {
        request_id: reqId,
        button_key: btn.button_key,
        code_revision_id: btn.id,
      },
    });
    obsText.textContent = res.message;
    lastSentContext = {
      appId,
      codeRevisionId: btn.id,
      buttonKey: btn.button_key,
    };
    showToast(res.message, res.gateway_ack ? 'success' : 'warning');
  } catch (err) {
    obsText.textContent = `Lỗi gửi lệnh: ${err.message}`;
    showToast(`Lỗi gửi: ${err.message}`, 'error');
  }
}

// Observation feedback buttons
document.getElementById('btn-obs-accurate')?.addEventListener('click', async () => {
  if (!lastSentContext) return;
  await submitObservation('accurate', 'Người dùng xác nhận thiết bị phản hồi đúng');
});

document.getElementById('btn-obs-inaccurate')?.addEventListener('click', async () => {
  if (!lastSentContext) return;
  await submitObservation('inaccurate', 'Người dùng báo thiết bị không phản hồi hoặc sai chức năng');
});

document.getElementById('btn-obs-unknown')?.addEventListener('click', async () => {
  if (!lastSentContext) return;
  await submitObservation('unknown', 'Chưa rõ');
});

async function submitObservation(outcome, defaultNote) {
  if (!lastSentContext) return;
  try {
    await apiFetch(`/api/code-revisions/${lastSentContext.codeRevisionId}/observations`, {
      method: 'POST',
      body: { outcome, user_notes: defaultNote },
    });
    showToast('Đã lưu quan sát thực tế!', 'success');
    document.getElementById('observation-box').style.display = 'none';
    // Reload active appliance
    selectAppliance(lastSentContext.appId);
  } catch (err) {
    showToast(`Lỗi lưu quan sát: ${err.message}`, 'error');
  }
}

function renderObservationsLog(observations) {
  const list = document.getElementById('recent-obs-list');
  if (!observations || observations.length === 0) {
    list.innerHTML = '<div class="text-muted small">Chưa có nhật ký quan sát nào.</div>';
    return;
  }
  list.innerHTML = observations.map(o => `
    <div class="obs-item p-1 border-bottom small">
      <strong>${o.button_key}</strong>: 
      <span class="badge ${o.outcome === 'accurate' ? 'badge-success' : (o.outcome === 'inaccurate' ? 'badge-danger' : 'badge-warning')}">
        ${o.outcome === 'accurate' ? 'Đúng chức năng' : (o.outcome === 'inaccurate' ? 'Sai/Không phản hồi' : 'Chưa rõ')}
      </span>
      <span class="text-muted float-right">${o.recorded_at ? o.recorded_at.substring(11, 19) : ''}</span>
      <div class="text-muted">${o.user_notes || ''}</div>
    </div>
  `).join('');
}

// Delete appliance
document.getElementById('btn-panel-delete')?.addEventListener('click', async () => {
  if (!activeApplianceId) return;
  if (!confirm('Bạn có chắc chắn muốn xóa thiết bị này?')) return;
  try {
    await apiFetch(`/api/devices/${activeApplianceId}`, { method: 'DELETE' });
    showToast('Đã xóa thiết bị thành công.', 'success');
    activeApplianceId = null;
    document.getElementById('appliance-active-panel').style.display = 'none';
    document.getElementById('appliance-panel-placeholder').style.display = 'block';
    loadAppliances();
  } catch (err) {
    showToast(`Lỗi khi xóa: ${err.message}`, 'error');
  }
});

// Create Appliance Form handling
async function populateGatewaySelect() {
  const select = document.getElementById('new-dev-gateway');
  select.innerHTML = '';
  const gateways = await apiFetch('/api/gateways');
  if (gateways.length === 0) {
    select.innerHTML = '<option value="">-- Chưa có Gateway nào (hãy kết nối RM4 trước) --</option>';
    return;
  }
  gateways.forEach(g => {
    const opt = document.createElement('option');
    opt.value = g.id;
    opt.textContent = `${g.name} (${g.ip_address})`;
    select.appendChild(opt);
  });
}

async function populateBrandSelect() {
  const category = document.getElementById('new-dev-category').value;
  const select = document.getElementById('new-dev-brand');
  select.innerHTML = '<option value="">-- Chọn hãng --</option>';
  try {
    const brands = await apiFetch(`/api/catalog/brands?category=${category}`);
    brands.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b;
      opt.textContent = b;
      select.appendChild(opt);
    });
  } catch (e) {}
}

document.getElementById('new-dev-category')?.addEventListener('change', async () => {
  await populateBrandSelect();
  document.getElementById('new-dev-codeset').innerHTML = '<option value="">-- Chọn bộ mã --</option>';
  document.getElementById('codeset-preview-box').style.display = 'none';
});

document.getElementById('new-dev-brand')?.addEventListener('change', async () => {
  const category = document.getElementById('new-dev-category').value;
  const brand = document.getElementById('new-dev-brand').value;
  const select = document.getElementById('new-dev-codeset');
  select.innerHTML = '<option value="">-- Chọn bộ mã --</option>';
  if (!brand) return;

  try {
    const codesets = await apiFetch(`/api/catalog/code-sets?category=${category}&brand=${encodeURIComponent(brand)}`);
    codesets.forEach(cs => {
      const opt = document.createElement('option');
      opt.value = cs.id;
      opt.textContent = `${cs.source_name} (${cs.models.join(', ')})`;
      select.appendChild(opt);
    });
  } catch (e) {}
});

document.getElementById('new-dev-codeset')?.addEventListener('change', async () => {
  const csId = document.getElementById('new-dev-codeset').value;
  const previewBox = document.getElementById('codeset-preview-box');
  const previewContent = document.getElementById('codeset-preview-content');
  if (!csId) {
    previewBox.style.display = 'none';
    return;
  }
  try {
    const cs = await apiFetch(`/api/catalog/code-sets/${csId}`);
    previewBox.style.display = 'block';
    const buttonKeys = Object.keys(cs.codes || {});
    previewContent.innerHTML = `
      <div class="small">
        <strong>Nguồn:</strong> ${cs.source_name} (${cs.license})<br>
        <strong>Các model hỗ trợ:</strong> ${cs.models.join(', ')}<br>
        <strong>Số nút/preset có sẵn:</strong> ${buttonKeys.length} nút (${buttonKeys.slice(0, 5).join(', ')}...)
      </div>
    `;
  } catch (e) {}
});

document.getElementById('form-create-appliance')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const gateway_id = document.getElementById('new-dev-gateway').value;
  const category = document.getElementById('new-dev-category').value;
  const brand = document.getElementById('new-dev-brand').value;
  const code_set_id = document.getElementById('new-dev-codeset').value;
  const name = document.getElementById('new-dev-name').value.trim();
  const room = document.getElementById('new-dev-room').value.trim();

  if (!gateway_id) {
    showToast('Vui lòng chọn Gateway RM4', 'error');
    return;
  }

  try {
    const app = await apiFetch('/api/devices', {
      method: 'POST',
      body: {
        name,
        room,
        category,
        brand,
        model: 'Universal',
        gateway_id,
        code_set_id: code_set_id || null,
      },
    });
    showToast('Tạo thiết bị thành công!', 'success');
    closeModal('modal-appliance');
    activeApplianceId = app.id;
    loadAppliances();
  } catch (err) {
    showToast(`Lỗi tạo thiết bị: ${err.message}`, 'error');
  }
});

// Learning IR Flow
let activeLearningJobId = null;
let learningPollTimer = null;

document.getElementById('btn-start-ir-learn')?.addEventListener('click', async () => {
  if (!activeApplianceId) return;
  const btnKey = document.getElementById('learn-btn-key').value.trim();
  const btnName = document.getElementById('learn-btn-name').value.trim();
  if (!btnKey) {
    showToast('Vui lòng nhập khóa nút (ví dụ: power_toggle)', 'error');
    return;
  }

  document.getElementById('learning-status-area').style.display = 'block';
  document.getElementById('learning-status-text').textContent = 'Đang chuyển RM4 vào chế độ học mã (hãy nhấn remote gốc)...';
  document.getElementById('btn-start-ir-learn').disabled = true;

  try {
    const res = await apiFetch(`/api/devices/${activeApplianceId}/learning-jobs`, {
      method: 'POST',
      body: {
        button_key: btnKey,
        button_name: btnName || btnKey,
        timeout_seconds: 30.0,
      },
    });
    activeLearningJobId = res.job_id;
    learningPollTimer = setInterval(pollLearningJob, 1000);
  } catch (err) {
    showToast(`Lỗi khởi động học mã: ${err.message}`, 'error');
    document.getElementById('learning-status-area').style.display = 'none';
    document.getElementById('btn-start-ir-learn').disabled = false;
  }
});

async function pollLearningJob() {
  if (!activeLearningJobId || !activeApplianceId) return;
  try {
    const job = await apiFetch(`/api/devices/${activeApplianceId}/learning-jobs/${activeLearningJobId}`);
    document.getElementById('learning-status-text').textContent = job.message;
    if (job.status === 'completed') {
      clearInterval(learningPollTimer);
      showToast('Đã nhận mã IR thành công!', 'success');
      closeModal('modal-learning');
      document.getElementById('btn-start-ir-learn').disabled = false;
      document.getElementById('learning-status-area').style.display = 'none';
      selectAppliance(activeApplianceId);
    } else if (job.status === 'failed' || job.status === 'cancelled') {
      clearInterval(learningPollTimer);
      showToast(`Học mã dừng: ${job.error || job.message}`, 'error');
      document.getElementById('btn-start-ir-learn').disabled = false;
    }
  } catch (e) {}
}

document.getElementById('btn-cancel-learning')?.addEventListener('click', async () => {
  if (!activeLearningJobId || !activeApplianceId) return;
  try {
    await apiFetch(`/api/devices/${activeApplianceId}/learning-jobs/${activeLearningJobId}/cancel`, { method: 'POST' });
    clearInterval(learningPollTimer);
    showToast('Đã gửi yêu cầu hủy học mã', 'info');
    document.getElementById('btn-start-ir-learn').disabled = false;
    document.getElementById('learning-status-area').style.display = 'none';
  } catch (e) {}
});

// Import Catalog JSON
document.getElementById('btn-submit-catalog-json')?.addEventListener('click', async () => {
  const raw = document.getElementById('catalog-json-textarea').value.trim();
  if (!raw) {
    showToast('Vui lòng dán nội dung JSON', 'error');
    return;
  }
  try {
    const data = JSON.parse(raw);
    const res = await apiFetch('/api/catalog/imports', {
      method: 'POST',
      body: { catalog_json: data, source_name: 'Custom User Import' },
    });
    showToast(res.message, 'success');
    closeModal('modal-import-catalog');
  } catch (err) {
    showToast(`Lỗi nhập catalog: ${err.message}`, 'error');
  }
});

// --- Recording Tab ---
document.querySelectorAll('input[name="rec-label-type"]').forEach(r => {
  r.addEventListener('change', () => {
    const isNegative = document.querySelector('input[name="rec-label-type"]:checked').value === 'negative';
    document.getElementById('group-negative-preset').style.display = isNegative ? 'block' : 'none';
    document.getElementById('group-custom-phrase').style.display = isNegative ? 'none' : 'block';
  });
});

document.getElementById('form-start-recording')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const speaker = document.querySelector('input[name="speaker"]:checked').value;
  const speaker_id = document.getElementById('rec-speaker-id').value.trim();
  const split = document.getElementById('rec-split').value;
  const isNegative = document.querySelector('input[name="rec-label-type"]:checked').value === 'negative';
  const preset = isNegative ? parseInt(document.getElementById('rec-preset-select').value, 10) : null;
  const phrase = isNegative ? null : document.getElementById('rec-phrase').value.trim();
  const takes_planned = parseInt(document.getElementById('rec-takes').value, 10);
  const distance_m = parseFloat(document.getElementById('rec-distance').value);
  const manual_advance = document.getElementById('rec-manual-advance').checked;
  const mock = document.getElementById('rec-mock-mode').checked;

  try {
    const res = await apiFetch('/api/recording/start', {
      method: 'POST',
      body: {
        speaker,
        speaker_id,
        split,
        phrase,
        preset,
        takes_planned,
        distance_m,
        manual_advance,
        mock,
      },
    });
    showToast(res.message, 'success');
    document.getElementById('btn-advance-take').disabled = false;
    document.getElementById('btn-stop-session').disabled = false;
    startRecordingPolling();
  } catch (err) {
    showToast(`Lỗi khởi động phiên thu: ${err.message}`, 'error');
  }
});

function startRecordingPolling() {
  if (recordingPollInterval) clearInterval(recordingPollInterval);
  recordingPollInterval = setInterval(pollRecordingStatus, 350);
  pollRecordingStatus();
}

async function pollRecordingStatus() {
  try {
    const st = await apiFetch('/api/recording/status');
    const badge = document.getElementById('rec-state-badge');
    const prompt = document.getElementById('studio-prompt');
    const progress = document.getElementById('studio-progress');
    const btnAdvance = document.getElementById('btn-advance-take');
    const btnStop = document.getElementById('btn-stop-session');

    badge.textContent = st.state.toUpperCase();
    badge.className = `session-badge ${st.state === 'recording' || st.state === 'waiting_user' ? 'active' : ''}`;
    progress.textContent = `Lượt: ${st.current_take} / ${st.takes_planned}`;

    if (st.state === 'preparing') {
      prompt.textContent = 'Đang ổn định mic...';
      btnAdvance.disabled = true;
    } else if (st.state === 'waiting_user') {
      prompt.textContent = `Chuẩn bị nói: '${st.phrase}'. Bấm "Bắt đầu lượt tiếp theo" để phát tít!`;
      btnAdvance.disabled = false;
    } else if (st.state === 'cue') {
      prompt.textContent = 'Đang phát tiếng tít (chuẩn bị nói)...';
      btnAdvance.disabled = true;
    } else if (st.state === 'recording') {
      prompt.textContent = `🔴 Đang thu âm (${st.phrase})...`;
      btnAdvance.disabled = true;
    } else if (st.state === 'processing') {
      prompt.textContent = 'Đang xử lý & tính toán RMS/clipping...';
      btnAdvance.disabled = true;
    } else if (st.state === 'completed') {
      prompt.textContent = '🎉 Hoàn tất phiên thu! Mẫu đã được lưu vào recordings/.';
      btnAdvance.disabled = true;
      btnStop.disabled = true;
      clearInterval(recordingPollInterval);
      loadSamples();
      pollSystemStatus();
    } else if (st.state === 'interrupted' || st.state === 'failed') {
      prompt.textContent = `⚠️ Phiên thu dừng: ${st.error || 'Đã ngắt theo yêu cầu'}`;
      btnAdvance.disabled = true;
      btnStop.disabled = true;
      clearInterval(recordingPollInterval);
      loadSamples();
      pollSystemStatus();
    }

    // Render clips list
    renderStudioClips(st.clips || []);
  } catch (e) {}
}

function renderStudioClips(clips) {
  const container = document.getElementById('studio-clips-list');
  if (clips.length === 0) {
    container.innerHTML = '<div class="text-muted small">Chưa có take nào hoàn thành trong phiên này.</div>';
    return;
  }
  container.innerHTML = clips.map(c => `
    <div class="clip-item p-2 border-bottom small">
      <strong>Lượt ${c.take_number}:</strong> ${c.file_name} 
      · Peak: <code>${c.peak}</code> · RMS: <code>${c.rms.toFixed(1)}</code> 
      · Clipping: <code class="${c.clipped_percent > 1 ? 'text-danger' : ''}">${c.clipped_percent.toFixed(2)}%</code>
    </div>
  `).join('');
}

document.getElementById('btn-advance-take')?.addEventListener('click', async () => {
  try {
    await apiFetch('/api/recording/advance', { method: 'POST' });
  } catch (err) {
    showToast(`Lỗi: ${err.message}`, 'error');
  }
});

document.getElementById('btn-stop-session')?.addEventListener('click', async () => {
  try {
    await apiFetch('/api/recording/stop', { method: 'POST' });
    showToast('Đã dừng phiên thu', 'info');
  } catch (err) {
    showToast(`Lỗi dừng: ${err.message}`, 'error');
  }
});

// --- Data & Review Tab ---
async function loadSamples() {
  const speaker = document.getElementById('filter-speaker')?.value;
  const split = document.getElementById('filter-split')?.value;
  const review_status = document.getElementById('filter-status')?.value;
  const label = document.getElementById('filter-label')?.value;

  const params = new URLSearchParams();
  if (speaker) params.append('speaker', speaker);
  if (split) params.append('split', split);
  if (review_status) params.append('review_status', review_status);
  if (label) params.append('label', label);

  const tbody = document.getElementById('samples-tbody');
  tbody.innerHTML = '<tr><td colspan="8" class="text-center">Đang tải danh sách mẫu...</td></tr>';

  try {
    const res = await apiFetch(`/api/samples?${params.toString()}`);
    document.getElementById('samples-count-header').textContent = `Danh sách mẫu (${res.count} / ${res.total} mẫu)`;
    if (res.samples.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Không có mẫu nào khớp bộ lọc.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    res.samples.forEach(s => {
      const tr = document.createElement('tr');
      const badgeClass = s.review_status === 'accepted' ? 'badge-success' : (s.review_status === 'rejected' ? 'badge-danger' : 'badge-warning');
      tr.innerHTML = `
        <td><code>${s.sample_id}</code></td>
        <td>${s.speaker_id} (${s.speaker_label})</td>
        <td><span class="badge">${s.split}</span></td>
        <td>${s.label}</td>
        <td>${s.transcript_human || 'Maika ơi'}</td>
        <td><span class="badge ${badgeClass}">${s.review_status}</span></td>
        <td>${s.speaker_confirmed ? '✅ Có' : '❌ Chưa'}</td>
        <td><button class="btn btn-sm btn-primary btn-review-sample">Duyệt mẫu</button></td>
      `;
      tr.querySelector('.btn-review-sample').addEventListener('click', () => {
        openReviewModal(s);
      });
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-danger">Lỗi tải mẫu: ${e.message}</td></tr>`;
  }
}

document.querySelectorAll('.filter-bar select').forEach(s => {
  s.addEventListener('change', loadSamples);
});

document.getElementById('btn-refresh-samples')?.addEventListener('click', loadSamples);

function openReviewModal(sample) {
  currentReviewSample = sample;
  document.getElementById('review-sample-id').textContent = sample.sample_id;
  document.getElementById('review-current-badge').textContent = sample.review_status;

  const audio = document.getElementById('review-audio-player');
  audio.src = `/api/samples/${encodeURIComponent(sample.sample_id)}/audio`;
  audio.load();

  document.getElementById('rev-speaker-expected').textContent = `${sample.speaker_id} (${sample.speaker_label})`;
  document.getElementById('rev-phrase-expected').textContent = sample.transcript_human || 'Maika ơi';
  document.getElementById('rev-split').textContent = sample.split;
  document.getElementById('rev-label-expected').textContent = `${sample.label} (${sample.expected_events} event)`;

  document.getElementById('rev-speaker-confirmed').checked = !!sample.speaker_confirmed;
  document.getElementById('rev-transcript-confirmed').value = sample.transcript_human || 'Maika ơi';
  document.getElementById('rev-notes').value = sample.review_note || '';

  openModal('modal-review');
}

// Review decisions
document.getElementById('btn-decision-accept')?.addEventListener('click', async () => {
  if (!currentReviewSample) return;
  const confirmedSpeaker = document.getElementById('rev-speaker-confirmed').checked;
  const confirmedTranscript = document.getElementById('rev-transcript-confirmed').value.trim();
  const notes = document.getElementById('rev-notes').value.trim();

  if (!confirmedSpeaker) {
    showToast('Bắt buộc phải tích chọn "Tôi xác nhận đúng người nói" trước khi chấp nhận!', 'error');
    return;
  }
  if (!confirmedTranscript) {
    showToast('Bắt buộc phải nhập câu nghe được thực tế!', 'error');
    return;
  }

  await submitReviewDecision('accepted', confirmedSpeaker, confirmedTranscript, notes);
});

document.getElementById('btn-decision-needs-review')?.addEventListener('click', async () => {
  const confirmedSpeaker = document.getElementById('rev-speaker-confirmed').checked;
  const confirmedTranscript = document.getElementById('rev-transcript-confirmed').value.trim();
  const notes = document.getElementById('rev-notes').value.trim();
  await submitReviewDecision('needs_review', confirmedSpeaker, confirmedTranscript, notes);
});

document.getElementById('btn-decision-reject')?.addEventListener('click', async () => {
  const confirmedSpeaker = document.getElementById('rev-speaker-confirmed').checked;
  const confirmedTranscript = document.getElementById('rev-transcript-confirmed').value.trim();
  const notes = document.getElementById('rev-notes').value.trim();
  await submitReviewDecision('rejected', confirmedSpeaker, confirmedTranscript, notes);
});

async function submitReviewDecision(status, confirmedSpeaker, confirmedTranscript, notes) {
  try {
    await apiFetch(`/api/samples/${encodeURIComponent(currentReviewSample.sample_id)}/review`, {
      method: 'PATCH',
      body: {
        review_status: status,
        speaker_confirmed: confirmedSpeaker,
        transcript_confirmed: confirmedTranscript,
        review_note: notes,
        expected_status: currentReviewSample.review_status,
      },
    });
    showToast(`Đã cập nhật trạng thái duyệt: ${status}`, 'success');
    closeModal('modal-review');
    loadSamples();
    pollSystemStatus();
  } catch (err) {
    showToast(`Lỗi duyệt: ${err.message}`, 'error');
  }
}

// --- Wake Word Tab ---
async function loadWakeCandidates() {
  const grid = document.getElementById('candidates-grid');
  try {
    const candidates = await apiFetch('/api/wake/candidates');
    grid.innerHTML = '';
    candidates.forEach(c => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h4>${c.name}</h4>
          ${c.is_baseline ? '<span class="badge badge-success">Baseline</span>' : '<span class="badge badge-warning">Ứng viên mới</span>'}
        </div>
        <div class="small text-muted mb-2">
          Engine: <code>${c.engine}</code> · Profile: <code>${c.profile}</code> · Ngưỡng: <code>${c.threshold}</code><br>
          Config hash: <code>${c.config_hash}</code>
        </div>
        <p class="small mb-2">${c.notes || 'Không có ghi chú'}</p>
        <div class="small">
          Mẫu tham chiếu: <strong>${c.reference_sample_ids.length}</strong> mẫu
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div class="text-danger">Lỗi tải candidates: ${e.message}</div>`;
  }
}

async function populateCandidateSampleSelector() {
  const container = document.getElementById('cand-sample-selector');
  container.innerHTML = 'Đang tải mẫu...';
  try {
    const res = await apiFetch('/api/samples?review_status=accepted');
    // Exclude test split samples strictly
    const valid = res.samples.filter(s => s.split !== 'test' && s.speaker_confirmed);
    if (valid.length === 0) {
      container.innerHTML = '<div class="text-warning small">Chưa có mẫu nào đạt chuẩn (phải accepted, confirmed và không thuộc split test). Hãy duyệt mẫu trước!</div>';
      return;
    }
    container.innerHTML = valid.map(s => `
      <label class="d-block small mb-1">
        <input type="checkbox" name="cand-sample-checkbox" value="${s.sample_id}">
        <strong>${s.sample_id}</strong> (${s.speaker_id}, split: ${s.split}, câu: '${s.transcript_human}')
      </label>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="text-danger small">Lỗi: ${e.message}</div>`;
  }
}

document.getElementById('form-create-candidate')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = document.getElementById('cand-name').value.trim();
  const profile = document.getElementById('cand-profile').value;
  const threshold = parseFloat(document.getElementById('cand-threshold').value);
  const rawAliases = document.getElementById('cand-aliases').value.trim();
  const aliases = rawAliases ? rawAliases.split(',').map(s => s.trim()).filter(Boolean) : [];

  const checkedSamples = Array.from(document.querySelectorAll('input[name="cand-sample-checkbox"]:checked')).map(c => c.value);
  if (checkedSamples.length === 0) {
    showToast('Cần chọn ít nhất một mẫu tham chiếu', 'error');
    return;
  }

  try {
    const res = await apiFetch('/api/wake/candidates', {
      method: 'POST',
      body: {
        name,
        sample_ids: checkedSamples,
        profile,
        threshold,
        aliases,
        notes: `Tạo từ UI dashboard với ${checkedSamples.length} mẫu tham chiếu`,
      },
    });
    showToast(res.message, 'success');
    closeModal('modal-candidate');
    loadWakeCandidates();
  } catch (err) {
    showToast(`Lỗi tạo candidate: ${err.message}`, 'error');
  }
});

// --- Results & Evaluations Tab ---
async function loadEvaluations() {
  const container = document.getElementById('evaluations-history-list');
  try {
    const evals = await apiFetch('/api/wake/evaluations');
    if (evals.length === 0) {
      container.innerHTML = '<div class="empty-state small">Chưa có bài đánh giá nào. Bấm "Chạy bài đánh giá mới" để bắt đầu.</div>';
      return;
    }
    container.innerHTML = '';
    evals.forEach(ev => {
      const item = document.createElement('div');
      item.className = 'eval-history-item p-2 border rounded mb-2 cursor-pointer';
      item.innerHTML = `
        <strong>${ev.name}</strong><br>
        <span class="small text-muted">${ev.split} (${ev.mode}) · ${ev.sample_count} mẫu · ${ev.created_at.substring(0, 16).replace('T', ' ')}</span>
      `;
      item.addEventListener('click', () => {
        showEvaluationDetail(ev.id);
      });
      container.appendChild(item);
    });
  } catch (e) {
    container.innerHTML = `<div class="text-danger small">Lỗi tải evaluations: ${e.message}</div>`;
  }
}

let activeEvaluationId = null;

async function showEvaluationDetail(evalId) {
  activeEvaluationId = evalId;
  try {
    const ev = await apiFetch(`/api/wake/evaluations/${evalId}`);
    document.getElementById('eval-view-placeholder').style.display = 'none';
    document.getElementById('eval-active-view').style.display = 'block';

    document.getElementById('eval-view-title').textContent = ev.name;
    document.getElementById('eval-view-meta').textContent = `Split: ${ev.split} · Mode: ${ev.mode} · Mẫu hợp lệ: ${ev.sample_count} · Hash: ${ev.snapshot_hash}`;

    // Summary table
    const tbody = document.getElementById('eval-summary-tbody');
    tbody.innerHTML = '';
    const metrics = ev.results.metrics || {};
    for (const [prof, m] of Object.entries(metrics)) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${prof}</strong></td>
        <td>${m.positive_accurate} / ${m.positive_processed} (${m.positive_accuracy_pct.toFixed(1)}%)</td>
        <td>${m.child_accurate} / ${m.child_eligible}</td>
        <td>${m.adult_accurate} / ${m.adult_eligible}</td>
        <td>${m.false_reject_rate_pct.toFixed(1)}%</td>
        <td>${m.false_accept_rate_pct.toFixed(1)}%</td>
        <td>${m.positive_duplicate}</td>
        <td>${m.decode_rtf_mean.toFixed(3)}</td>
        <td>${m.positive_errors + m.negative_errors}</td>
      `;
      tbody.appendChild(tr);
    }

    // Markdown preview
    document.getElementById('eval-md-preview').textContent = ev.report_markdown;
  } catch (e) {
    showToast(`Lỗi tải chi tiết: ${e.message}`, 'error');
  }
}

document.getElementById('btn-download-eval-md')?.addEventListener('click', () => {
  if (!activeEvaluationId) return;
  window.open(`/api/wake/evaluations/${activeEvaluationId}/report.md`, '_blank');
});

async function populateEvalCandidateSelector() {
  const container = document.getElementById('eval-candidate-selector');
  container.innerHTML = 'Đang tải candidates...';
  try {
    const candidates = await apiFetch('/api/wake/candidates');
    container.innerHTML = candidates.map((c, idx) => `
      <label class="d-block small mb-1">
        <input type="checkbox" name="eval-cand-checkbox" value="${c.id}" ${idx < 2 ? 'checked' : ''}>
        <strong>${c.name}</strong> (${c.profile})
      </label>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="text-danger small">Lỗi: ${e.message}</div>`;
  }
}

document.getElementById('form-run-evaluation')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const split = document.getElementById('eval-split-select').value;
  const mode = document.getElementById('eval-mode-select').value;
  const name = document.getElementById('eval-custom-name').value.trim();
  const checked = Array.from(document.querySelectorAll('input[name="eval-cand-checkbox"]:checked')).map(c => c.value);

  if (checked.length === 0) {
    showToast('Vui lòng chọn ít nhất một candidate', 'error');
    return;
  }

  const btn = document.getElementById('btn-submit-eval');
  btn.disabled = true;
  btn.textContent = 'Đang chạy benchmark offline...';

  try {
    const res = await apiFetch('/api/wake/evaluations', {
      method: 'POST',
      body: {
        candidate_ids: checked,
        split,
        mode,
        name,
      },
    });
    showToast('Đã hoàn thành bài đánh giá!', 'success');
    closeModal('modal-run-eval');
    loadEvaluations();
    showEvaluationDetail(res.id);
  } catch (err) {
    showToast(`Lỗi đánh giá: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔬 Bắt đầu chạy đánh giá';
  }
});
