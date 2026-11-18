(function () {
  'use strict';

  const STAGES = ['planning', 'coding', 'checking'];
  const STAGE_LABEL = { planning: '需求规划', coding: '编码生成', checking: '自检与落库' };
  const POLL_INTERVAL = 1500;
  const TERMINAL = { succeeded: true, failed: true, cancelled: true };

  const backend = { online: false, step: 's3', engine: 'none' };

  const state = {
    tasks: [],
    detail: null,
    activeId: null,
    versions: [],
    parentId: null,
    width: '100',
    busy: false,
    pollTimer: null,
    pollFailures: 0,
    listLoaded: false,
  };

  const el = {
    version: document.getElementById('version-tag'),
    taskCount: document.getElementById('task-count'),
    statusWrap: document.getElementById('status-indicator'),
    statusText: document.querySelector('.status__text'),
    engineChip: document.getElementById('engine-chip'),
    helpBtn: document.getElementById('help-btn'),
    helpPanel: document.getElementById('help-panel'),
    input: document.getElementById('requirement-input'),
    examples: document.getElementById('example-list'),
    submit: document.getElementById('submit-btn'),
    cancel: document.getElementById('cancel-btn'),
    modify: document.getElementById('modify-btn'),
    modifyHint: document.getElementById('modify-hint'),
    modifyHintText: document.getElementById('modify-hint-text'),
    modifyCancel: document.getElementById('modify-cancel'),
    versionBlock: document.getElementById('version-block'),
    versionList: document.getElementById('version-list'),
    versionHint: document.getElementById('version-hint'),
    stageList: document.getElementById('stage-list'),
    stageHint: document.getElementById('stage-hint'),
    taskList: document.getElementById('task-list'),
    taskHint: document.getElementById('task-hint'),
    frame: document.getElementById('preview-frame'),
    canvas: document.getElementById('preview-canvas'),
    empty: document.getElementById('empty-state'),
    emptyTitle: document.querySelector('#empty-state h3'),
    emptyText: document.querySelector('#empty-state p'),
    errorBanner: document.getElementById('error-banner'),
    errorText: document.getElementById('error-text'),
    retry: document.getElementById('retry-btn'),
    refresh: document.getElementById('refresh-btn'),
    popout: document.getElementById('popout-btn'),
    widthGroup: document.getElementById('width-group'),
    meta: document.getElementById('preview-meta'),
    progress: document.getElementById('progress-bar'),
  };

  // ---------- 工具 ----------

  function isTerminal(status) {
    return Boolean(TERMINAL[status]);
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '--:--';
    return (
      String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
    );
  }

  function statusLabel(task) {
    if (task.status === 'succeeded') return '已生成';
    if (task.status === 'failed') return '失败';
    if (task.status === 'cancelled') return '已取消';
    if (task.status === 'running') return '生成中';
    return '排队中';
  }

  async function api(path, options) {
    const init = Object.assign(
      { headers: { 'Content-Type': 'application/json' }, cache: 'no-store' },
      options || {}
    );
    const res = await fetch(path, init);
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (err) {
      data = null;
    }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) {
        detail = detail.map(function (d) { return d.msg || JSON.stringify(d); }).join('; ');
      }
      throw new Error(detail || 'HTTP ' + res.status);
    }
    return data;
  }

  // ---------- 顶栏 ----------

  function updateStatus() {
    if (el.statusWrap) el.statusWrap.dataset.online = backend.online ? 'true' : 'false';

    const step = String(backend.step || 's3').toUpperCase();
    if (backend.online) {
      el.statusText.textContent = state.busy ? '提交中 · ' + step : '后端已连接 · ' + step;
    } else {
      el.statusText.textContent = state.busy ? '提交中 · 离线' : '离线 · ' + step;
    }

    if (el.engineChip) {
      el.engineChip.textContent =
        backend.engine && backend.engine !== 'none'
          ? '引擎 ' + String(backend.engine).toUpperCase()
          : '引擎 未接入';
    }
    if (el.version && backend.step) el.version.textContent = backend.step;
  }

  function probeHealth() {
    if (typeof window.fetch !== 'function') {
      updateStatus();
      return Promise.resolve();
    }
    return fetch('/health', { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        backend.online = true;
        backend.step = data.step || backend.step;
        backend.engine = data.engine || 'none';
      })
      .catch(function () {
        backend.online = false;
      })
      .then(updateStatus);
  }

  // ---------- 任务列表 ----------

  function renderTasks() {
    el.taskList.innerHTML = '';
    el.taskCount.textContent = String(state.tasks.length);

    if (!state.tasks.length) {
      const li = document.createElement('li');
      li.className = 'task-empty';
      li.textContent = state.listLoaded ? '还没有任务，先提交一条需求' : '加载中…';
      el.taskList.appendChild(li);
      return;
    }

    state.tasks.forEach(function (task) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'task' + (task.id === state.activeId ? ' is-active' : '');
      btn.dataset.taskId = task.id;
      btn.setAttribute('aria-pressed', task.id === state.activeId ? 'true' : 'false');

      const prompt = document.createElement('span');
      prompt.className = 'task__prompt';
      const versionIndex = state.versions.findIndex(function (item) { return item.id === task.id; });
      if (state.versions.length > 1 && versionIndex >= 0) {
        const badge = document.createElement('span');
        badge.className = 'task__version';
        badge.textContent = 'v' + (versionIndex + 1);
        prompt.appendChild(badge);
        prompt.appendChild(document.createTextNode(task.prompt));
      } else {
        prompt.textContent = task.prompt;
      }

      const meta = document.createElement('span');
      meta.className = 'task__meta';

      const st = document.createElement('span');
      st.className = 'task__state';
      st.dataset.state = task.status;
      st.textContent = statusLabel(task);

      const time = document.createElement('span');
      time.textContent = fmtTime(task.created_at);

      meta.appendChild(st);
      meta.appendChild(time);

      if (task.artifact_count) {
        const count = document.createElement('span');
        count.textContent = task.artifact_count + ' 文件';
        meta.appendChild(count);
      }

      btn.appendChild(prompt);
      btn.appendChild(meta);
      btn.addEventListener('click', function () {
        selectTask(task.id);
      });

      li.appendChild(btn);
      el.taskList.appendChild(li);
    });
  }

  async function loadTasks() {
    try {
      const data = await api('/api/tasks?limit=50');
      state.tasks = data.tasks || [];
      state.listLoaded = true;
      el.taskHint.textContent = '共 ' + (data.total || state.tasks.length) + ' 条任务';
      renderTasks();
      return state.tasks;
    } catch (err) {
      state.listLoaded = true;
      el.taskHint.textContent = '任务列表加载失败';
      renderTasks();
      return [];
    }
  }

  // ---------- 版本链 ----------

  function renderVersions() {
    if (!el.versionBlock) return;
    const versions = state.versions || [];
    el.versionBlock.hidden = versions.length <= 1;
    if (versions.length <= 1) {
      el.versionList.innerHTML = '';
      return;
    }

    el.versionHint.textContent = '共 ' + versions.length + ' 个版本';
    el.versionList.innerHTML = '';
    versions.forEach(function (version, index) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'version-chip' + (version.id === state.activeId ? ' is-active' : '');
      btn.dataset.state = version.status;
      btn.textContent = 'v' + (index + 1);
      btn.title = statusLabel(version) + ' · ' + version.prompt;
      btn.addEventListener('click', function () {
        selectTask(version.id);
      });
      li.appendChild(btn);
      el.versionList.appendChild(li);
    });
  }

  async function loadVersions(id) {
    try {
      const data = await api('/api/tasks/' + encodeURIComponent(id) + '/versions');
      if (state.activeId !== id) return;
      state.versions = data.versions || [];
    } catch (err) {
      state.versions = [];
    }
    renderVersions();
    renderTasks();
  }

  function versionIndexOf(id) {
    return state.versions.findIndex(function (item) { return item.id === id; });
  }

  // ---------- 继续修改 ----------

  function refreshSubmitLabel() {
    if (state.busy) return;
    const label = el.submit.querySelector('.primary-btn__label');
    if (label) label.textContent = state.parentId ? '生成新版本' : '生成应用';
  }

  function updateModifyState() {
    if (!el.modify) return;
    el.modify.disabled = !(state.detail && state.detail.status === 'succeeded');
  }

  function setModifyTarget(task) {
    if (!task || task.status !== 'succeeded') return;
    state.parentId = task.id;
    const index = versionIndexOf(task.id);
    el.modifyHint.hidden = false;
    el.modifyHintText.textContent =
      '将基于 ' +
      (index >= 0 ? 'v' + (index + 1) : '当前版本') +
      ' 生成新版本：' +
      task.prompt.slice(0, 40);
    el.input.focus();
    refreshSubmitLabel();
  }

  function clearModifyTarget() {
    state.parentId = null;
    if (el.modifyHint) el.modifyHint.hidden = true;
    if (el.modifyHintText) el.modifyHintText.textContent = '';
    refreshSubmitLabel();
  }

  // ---------- 阶段 ----------

  function stageDurations(task) {
    const starts = {};
    (task.events || []).forEach(function (event) {
      if (STAGES.indexOf(event.message) >= 0 && !starts[event.message]) {
        starts[event.message] = new Date(event.created_at).getTime();
      }
    });

    const end = isTerminal(task.status) ? new Date(task.updated_at).getTime() : null;
    const durations = {};
    STAGES.forEach(function (stage, index) {
      if (!starts[stage]) return;
      let next = null;
      for (let i = index + 1; i < STAGES.length; i += 1) {
        if (starts[STAGES[i]]) {
          next = starts[STAGES[i]];
          break;
        }
      }
      const finish = next || end;
      if (finish && finish >= starts[stage]) {
        durations[stage] = (finish - starts[stage]) / 1000;
      }
    });
    return durations;
  }

  function renderStages() {
    const task = state.detail;
    const current = task && task.stage ? STAGES.indexOf(task.stage) : -1;
    const durations = task ? stageDurations(task) : {};

    el.stageList.querySelectorAll('.stage').forEach(function (node) {
      const key = node.dataset.stage;
      const index = STAGES.indexOf(key);
      node.classList.remove('is-active', 'is-done', 'is-failed');

      const timeNode = node.querySelector('.stage__time');
      const value = durations[key];
      timeNode.textContent = value == null ? '' : value.toFixed(1) + 's';

      if (!task) return;

      if (task.status === 'succeeded') {
        node.classList.add('is-done');
        return;
      }
      if (task.status === 'cancelled') {
        if (index < current) node.classList.add('is-done');
        if (index === current) node.classList.add('is-failed');
        return;
      }
      if (task.status === 'failed') {
        if (index < current) node.classList.add('is-done');
        if (index === current) node.classList.add('is-failed');
        return;
      }
      if (index < current) node.classList.add('is-done');
      if (index === current) node.classList.add('is-active');
    });

    if (!task) {
      el.stageHint.textContent = '等待提交';
    } else if (task.status === 'succeeded') {
      el.stageHint.textContent = '全部阶段完成';
    } else if (task.status === 'failed') {
      el.stageHint.textContent = '生成失败';
    } else if (task.status === 'cancelled') {
      el.stageHint.textContent = '已取消';
    } else if (task.status === 'running') {
      el.stageHint.textContent = (STAGE_LABEL[task.stage] || '处理中') + ' · 进行中';
    } else {
      el.stageHint.textContent = '排队中';
    }

    const cancellable = task && !isTerminal(task.status);
    if (el.cancel) el.cancel.hidden = !cancellable;
  }

  // ---------- 预览 ----------

  function previewUrl(task) {
    if (!task || task.status !== 'succeeded' || !task.entry) return null;
    const encoded = task.entry.split('/').map(encodeURIComponent).join('/');
    return '/api/tasks/' + encodeURIComponent(task.id) + '/files/' + encoded;
  }

  function renderPreview(animate) {
    const task = state.detail;
    const url = previewUrl(task);

    if (!url) {
      el.frame.removeAttribute('src');
      el.frame.setAttribute('src', 'about:blank');
      el.empty.hidden = false;
      if (el.emptyTitle && el.emptyText) {
        if (!task) {
          el.emptyTitle.textContent = state.tasks.length ? '选择一条任务' : '还没有任务';
          el.emptyText.textContent = state.tasks.length
            ? '从左侧选择一条任务，或提交新的需求。'
            : '在需求框里描述你想生成的应用，提交后这里会显示生成结果。';
        } else if (task.status === 'failed') {
          el.emptyTitle.textContent = '生成失败';
          el.emptyText.textContent = task.error || '引擎没有返回可预览的产物。';
        } else if (task.status === 'cancelled') {
          el.emptyTitle.textContent = '任务已取消';
          el.emptyText.textContent = '已生成的产物会保留在 workspace 目录中。';
        } else {
          el.emptyTitle.textContent = '正在生成…';
          el.emptyText.textContent = '阶段卡片会随进度点亮，完成后自动加载预览。';
        }
      }
      el.meta.textContent = task ? task.id + ' · ' + statusLabel(task) : '未选择任务';
      return;
    }

    el.empty.hidden = true;
    const target = animate ? url + '?t=' + Date.now() : url;
    el.frame.setAttribute('src', target);
    el.meta.textContent = task.id + ' · ' + fmtTime(task.created_at);

    if (animate) {
      el.progress.classList.remove('is-running');
      void el.progress.offsetWidth;
      el.progress.classList.add('is-running');
    }
  }

  function showError(message) {
    el.errorBanner.hidden = false;
    el.errorText.textContent = message;
  }

  function hideError() {
    el.errorBanner.hidden = true;
  }

  function applyDetail(detail) {
    state.detail = detail;

    const row = state.tasks.find(function (t) { return t.id === detail.id; });
    if (row) {
      row.status = detail.status;
      row.stage = detail.stage;
      row.error = detail.error;
      row.artifact_count = (detail.artifacts || []).length;
      row.updated_at = detail.updated_at;
    }
    renderTasks();
    renderStages();
    renderVersions();
    updateModifyState();
    renderPreview(false);

    if (detail.status === 'failed' && detail.error) {
      showError(detail.error);
    } else {
      hideError();
    }

    if (isTerminal(detail.status)) {
      stopPolling();
      loadTasks();
      // 新版本落地后版本链会变长，必须重新拉一次
      loadVersions(detail.id);
    } else {
      startPolling();
    }
  }

  async function selectTask(id) {
    if (!id) return;
    state.activeId = id;
    clearModifyTarget();
    renderTasks();
    try {
      const detail = await api('/api/tasks/' + encodeURIComponent(id));
      if (state.activeId !== id) return;
      applyDetail(detail);
      await loadVersions(id);
    } catch (err) {
      showError('读取任务失败：' + err.message);
    }
  }

  // ---------- 轮询 ----------

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = window.setInterval(pollOnce, POLL_INTERVAL);
  }

  function stopPolling() {
    if (!state.pollTimer) return;
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  async function pollOnce() {
    if (!state.activeId) {
      stopPolling();
      return;
    }
    try {
      const detail = await api('/api/tasks/' + encodeURIComponent(state.activeId));
      if (state.activeId !== detail.id) return;
      state.pollFailures = 0;
      applyDetail(detail);
    } catch (err) {
      // 单次失败多半是网络抖动：继续轮询，连续多次失败才放弃并提示
      state.pollFailures += 1;
      if (state.pollFailures >= 4) {
        stopPolling();
        showError('轮询失败：' + err.message + '（已停止自动刷新，可点击任务重试）');
      }
    }
  }

  // ---------- 提交 / 取消 ----------

  function setBusy(busy) {
    state.busy = busy;
    el.submit.disabled = busy;
    const label = el.submit.querySelector('.primary-btn__label');
    if (label) {
      label.textContent = busy ? '提交中…' : state.parentId ? '生成新版本' : '生成应用';
    }
    updateStatus();
  }

  function optimisticTask(id, prompt) {
    const stamp = new Date().toISOString();
    return {
      id: id,
      prompt: prompt,
      status: 'pending',
      stage: null,
      parent_id: state.parentId,
      error: null,
      artifacts: [],
      events: [],
      created_at: stamp,
      updated_at: stamp,
      artifact_count: 0,
    };
  }

  async function submit(prompt) {
    if (state.busy) return;
    hideError();
    setBusy(true);
    const parentId = state.parentId;
    let accepted = false;
    try {
      const payload = { prompt: prompt };
      if (parentId) payload.parent_id = parentId;
      const data = await api('/api/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const task = optimisticTask(data.task_id, prompt);
      state.tasks.unshift(task);
      state.activeId = task.id;
      state.detail = task;
      el.input.value = '';
      renderTasks();
      renderStages();
      updateModifyState();
      renderPreview(false);
      startPolling();
      el.taskHint.textContent = parentId ? '已提交修改，等待生成新版本…' : '已提交，等待生成…';
      // 立刻把新版本插进版本链：pending 也算一个版本，用户能马上看到 v4
      loadVersions(task.id);
      accepted = true;
    } catch (err) {
      showError('提交失败：' + err.message);
    } finally {
      setBusy(false);
      // 只有提交成功才退出修改态：失败时保留目标，方便改完再试
      if (accepted) clearModifyTarget();
    }
  }

  async function cancelActive() {
    const task = state.detail;
    if (!task || isTerminal(task.status)) return;
    try {
      await api('/api/tasks/' + encodeURIComponent(task.id) + '/cancel', { method: 'POST' });
      await pollOnce();
    } catch (err) {
      showError('取消失败：' + err.message);
    }
  }

  function retry() {
    const task = state.detail;
    if (!task) return;
    el.input.value = task.prompt;
    clearModifyTarget();
    submit(task.prompt);
  }

  // ---------- 事件绑定 ----------

  function bind() {
    el.examples.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-prompt]');
      if (!btn) return;
      el.input.value = btn.dataset.prompt;
      el.input.focus();
    });

    el.submit.addEventListener('click', function () {
      const value = el.input.value.trim();
      if (!value || state.busy) return;
      submit(value);
    });

    el.input.addEventListener('keydown', function (event) {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        el.submit.click();
      }
    });

    if (el.cancel) el.cancel.addEventListener('click', cancelActive);

    if (el.modify) {
      el.modify.addEventListener('click', function () {
        setModifyTarget(state.detail);
      });
    }
    if (el.modifyCancel) el.modifyCancel.addEventListener('click', clearModifyTarget);

    el.widthGroup.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-width]');
      if (!btn) return;
      state.width = btn.dataset.width;
      el.canvas.dataset.width = state.width;
      el.widthGroup.querySelectorAll('.width-btn').forEach(function (node) {
        node.classList.toggle('is-active', node.dataset.width === state.width);
      });
    });

    el.refresh.addEventListener('click', function () {
      renderPreview(true);
    });

    el.popout.addEventListener('click', function () {
      const url = previewUrl(state.detail);
      if (!url) return;
      window.open(url, '_blank', 'noopener');
    });

    el.helpBtn.addEventListener('click', function () {
      const open = el.helpPanel.hidden;
      el.helpPanel.hidden = !open;
      el.helpBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    document.addEventListener('click', function (event) {
      if (el.helpPanel.hidden) return;
      if (el.helpPanel.contains(event.target) || el.helpBtn.contains(event.target)) return;
      el.helpPanel.hidden = true;
      el.helpBtn.setAttribute('aria-expanded', 'false');
    });

    el.retry.addEventListener('click', retry);

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !el.helpPanel.hidden) {
        el.helpPanel.hidden = true;
        el.helpBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  async function init() {
    bind();
    updateStatus();
    probeHealth();

    const tasks = await loadTasks();
    renderStages();
    renderPreview(false);

    const preferred =
      tasks.find(function (t) { return t.status === 'succeeded'; }) || tasks[0];
    if (preferred) {
      selectTask(preferred.id);
    } else {
      el.taskHint.textContent = '还没有任务，先提交一条需求';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
