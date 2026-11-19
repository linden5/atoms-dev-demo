(function () {
  'use strict';

  const STAGES = ['planning', 'coding', 'checking'];
  const STAGE_LABEL = { planning: '需求规划', coding: '编码生成', checking: '自检与落库' };
  const POLL_INTERVAL = 1500;
  const TERMINAL = { succeeded: true, failed: true, cancelled: true };

  const backend = { online: false, step: 's6', engine: 'none' };

  const state = {
    sessions: [],
    totalSessions: 0,
    sessionId: null,
    messages: [],
    tasksById: {},
    activeId: null,
    width: '100',
    busy: false,
    pollTimer: null,
    pollTaskId: null,
    pollFailures: 0,
    loaded: false,
    scrollToBottom: false,
    history: { items: [], total: 0, limit: 20 },
  };

  const el = {
    version: document.getElementById('version-tag'),
    taskCount: document.getElementById('task-count'),
    statusWrap: document.getElementById('status-indicator'),
    statusText: document.querySelector('.status__text'),
    engineChip: document.getElementById('engine-chip'),
    helpBtn: document.getElementById('help-btn'),
    helpPanel: document.getElementById('help-panel'),
    agentState: document.getElementById('agent-state'),
    agentStateText: document.getElementById('agent-state-text'),
    newSession: document.getElementById('new-session-btn'),
    historyBtn: document.getElementById('history-btn'),
    historyPanel: document.getElementById('history-panel'),
    historyList: document.getElementById('history-list'),
    historyCount: document.getElementById('history-count'),
    historyMore: document.getElementById('history-more'),
    cancel: document.getElementById('cancel-btn'),
    sessionList: document.getElementById('session-list'),
    chatLog: document.getElementById('chat-log'),
    input: document.getElementById('requirement-input'),
    examples: document.getElementById('example-list'),
    submit: document.getElementById('submit-btn'),
    frame: document.getElementById('preview-frame'),
    canvas: document.getElementById('preview-canvas'),
    loading: document.getElementById('preview-loading'),
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
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  // 会话列表用：当天只显示时间，更早补上"月-日"
  function fmtStamp(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '--';
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const time = fmtTime(iso);
    return sameDay ? time : d.getMonth() + 1 + '-' + d.getDate() + ' ' + time;
  }

  function statusLabel(task) {
    if (task.status === 'succeeded') return '已生成';
    if (task.status === 'failed') return '失败';
    if (task.status === 'cancelled') return '已取消';
    if (task.status === 'running') return '生成中';
    return '排队中';
  }

  function shorten(text, max) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return value.length > max ? value.slice(0, max) + '…' : value;
  }

  function node(tag, className, text) {
    const target = document.createElement(tag);
    if (className) target.className = className;
    if (text != null) target.textContent = text;
    return target;
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

    const step = String(backend.step || 's6').toUpperCase();
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

  // ---------- 会话列表 ----------

  function renderSessions() {
    if (!el.sessionList) return;
    el.sessionList.innerHTML = '';
    el.taskCount.textContent = String(state.totalSessions || state.sessions.length);
    el.sessionList.hidden = state.sessions.length === 0;

    state.sessions.forEach(function (session) {
      const btn = node('button', 'thread' + (session.id === state.sessionId ? ' is-active' : ''));
      btn.type = 'button';
      btn.dataset.sessionId = session.id;
      btn.title =
        (session.title || '新会话') +
        ' · ' + session.message_count + ' 条消息 · ' + fmtStamp(session.updated_at);
      btn.setAttribute('aria-pressed', session.id === state.sessionId ? 'true' : 'false');
      btn.appendChild(node('span', 'thread__dot'));
      btn.appendChild(node('span', 'thread__text', session.title ? shorten(session.title, 12) : '新会话'));
      btn.appendChild(node('span', 'thread__time', fmtStamp(session.updated_at)));
      el.sessionList.appendChild(btn);
    });
  }

  async function loadSessions() {
    try {
      const data = await api('/api/sessions?limit=50');
      state.sessions = data.sessions || [];
      state.totalSessions = data.total || state.sessions.length;
      state.loaded = true;
      renderSessions();
      return state.sessions;
    } catch (err) {
      state.loaded = true;
      renderSessions();
      return [];
    }
  }

  async function createSession() {
    try {
      const data = await api('/api/sessions', { method: 'POST', body: '{}' });
      state.sessionId = data.session_id;
      state.messages = [];
      state.tasksById = {};
      state.activeId = null;
      state.scrollToBottom = true;
      stopPolling();
      renderChat();
      syncPreview(false);
      await loadSessions();
      refreshHistoryIfOpen();
      if (el.input) el.input.focus();
    } catch (err) {
      showError('新建会话失败：' + err.message);
    }
  }

  // ---------- 历史会话面板 ----------

  async function loadHistory() {
    try {
      const data = await api('/api/sessions?limit=' + state.history.limit + '&offset=0');
      state.history.items = data.sessions || [];
      state.history.total = data.total || state.history.items.length;
    } catch (err) {
      state.history.items = [];
      state.history.total = 0;
      showError('读取历史会话失败：' + err.message);
    }
    renderHistory();
  }

  function renderHistory() {
    if (!el.historyList) return;
    el.historyList.innerHTML = '';

    if (el.historyCount) {
      const shown = state.history.items.length;
      el.historyCount.textContent =
        state.history.total + ' 个会话' + (shown < state.history.total ? '（显示 ' + shown + '）' : '');
    }

    if (!state.history.items.length) {
      el.historyList.appendChild(node('li', 'history__empty', '还没有会话，发一句需求就开始了'));
    }

    state.history.items.forEach(function (session) {
      const li = document.createElement('li');
      const btn = node(
        'button',
        'history__item' + (session.id === state.sessionId ? ' is-active' : '')
      );
      btn.type = 'button';
      btn.dataset.sessionId = session.id;
      btn.title = session.title || '新会话';
      btn.appendChild(node('span', 'history__name', session.title || '新会话'));

      const meta = node('span', 'history__meta');
      meta.appendChild(node('span', null, fmtStamp(session.updated_at)));
      meta.appendChild(node('span', null, session.message_count + ' 条消息'));
      btn.appendChild(meta);

      li.appendChild(btn);
      el.historyList.appendChild(li);
    });

    if (el.historyMore) {
      el.historyMore.hidden = state.history.items.length >= state.history.total;
    }
  }

  async function openHistory() {
    if (!el.historyPanel) return;
    el.historyPanel.hidden = false;
    if (el.historyBtn) el.historyBtn.setAttribute('aria-expanded', 'true');
    await loadHistory();
  }

  function closeHistory() {
    if (!el.historyPanel || el.historyPanel.hidden) return;
    el.historyPanel.hidden = true;
    if (el.historyBtn) el.historyBtn.setAttribute('aria-expanded', 'false');
  }

  async function toggleHistory() {
    if (!el.historyPanel) return;
    if (el.historyPanel.hidden) await openHistory();
    else closeHistory();
  }

  function refreshHistoryIfOpen() {
    if (el.historyPanel && !el.historyPanel.hidden) renderHistory();
  }

  // ---------- 会话详情 ----------

  function latestTaskId() {
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      if (state.messages[i].task_id) return state.messages[i].task_id;
    }
    return null;
  }

  function latestSucceededId() {
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      const taskId = state.messages[i].task_id;
      const task = taskId ? state.tasksById[taskId] : null;
      if (task && task.status === 'succeeded' && task.entry) return taskId;
    }
    return null;
  }

  async function selectSession(id, preferredTaskId) {
    if (!id) return;
    state.sessionId = id;
    renderSessions();
    try {
      const data = await api('/api/sessions/' + encodeURIComponent(id));
      if (state.sessionId !== id) return;

      state.messages = data.messages || [];
      state.tasksById = {};
      (data.tasks || []).forEach(function (task) {
        state.tasksById[task.id] = task;
      });

      const preferred =
        preferredTaskId && state.tasksById[preferredTaskId] ? preferredTaskId : null;
      if (preferred) {
        state.activeId = preferred;
      } else if (!state.activeId || !state.tasksById[state.activeId]) {
        state.activeId = latestSucceededId() || latestTaskId();
      }

      state.scrollToBottom = true;
      renderChat();
      syncPreview(false);
      resumePolling();
      refreshHistoryIfOpen();
    } catch (err) {
      showError('读取会话失败：' + err.message);
    }
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

  function stageStates(task) {
    const durations = task ? stageDurations(task) : {};
    const current = task && task.stage ? STAGES.indexOf(task.stage) : -1;

    return STAGES.map(function (key, index) {
      let value = 'idle';
      if (task) {
        if (task.status === 'succeeded') {
          value = 'done';
        } else if (task.status === 'failed' || task.status === 'cancelled') {
          if (index < current) value = 'done';
          else if (index === current) value = 'failed';
        } else if (index < current) {
          value = 'done';
        } else if (index === current) {
          value = 'active';
        }
      }
      return { key: key, state: value, duration: durations[key] };
    });
  }

  function completedSteps(task) {
    return stageStates(task).filter(function (item) {
      return item.state === 'done';
    }).length;
  }

  function progressLabel(task) {
    const done = completedSteps(task);
    if (task.status === 'succeeded') return '已处理 ' + done + ' 步';
    if (task.status === 'failed') return '已处理 ' + done + ' 步 · 失败';
    if (task.status === 'cancelled') return '已处理 ' + done + ' 步 · 已取消';
    if (task.status === 'running') return '正在' + (STAGE_LABEL[task.stage] || '生成') + '…';
    return '排队中…';
  }

  function currentTask() {
    return state.activeId ? state.tasksById[state.activeId] : null;
  }

  function updateAgentState() {
    if (!el.agentState || !el.agentStateText) return;
    const task = currentTask();
    let key = 'idle';
    let text = '已就绪';

    if (task) {
      if (task.status === 'succeeded') {
        key = 'succeeded';
        text = '已完成 · ' + completedSteps(task) + '/' + STAGES.length + ' 步';
      } else if (task.status === 'failed') {
        key = 'failed';
        text = '生成失败';
      } else if (task.status === 'cancelled') {
        text = '已取消';
      } else if (task.status === 'running') {
        key = 'running';
        text = (STAGE_LABEL[task.stage] || '生成') + ' 进行中';
      } else {
        key = 'running';
        text = '排队中';
      }
    } else if (!state.loaded) {
      text = '加载中…';
    } else if (!state.messages.length) {
      text = '等待你的需求';
    }

    el.agentState.dataset.state = key;
    el.agentStateText.textContent = text;
    if (el.cancel) el.cancel.hidden = !(task && !isTerminal(task.status));
  }

  // ---------- 消息流 ----------

  function versionIndexOf(taskId) {
    const artifacts = state.messages.filter(function (message) {
      return message.kind === 'artifact';
    });
    return artifacts.findIndex(function (message) {
      return message.task_id === taskId;
    }) + 1;
  }

  function userBubble(message) {
    const wrap = node('div', 'msg msg--user');
    wrap.appendChild(node('div', 'msg__bubble', message.content));
    return wrap;
  }

  function questionMessage(message) {
    const wrap = node('div', 'msg msg--agent');
    const head = node('div', 'msg__head');
    head.appendChild(node('span', 'agent__avatar agent__avatar--sm', 'A'));
    head.appendChild(node('span', 'msg__label', '需要补充信息'));
    wrap.appendChild(head);

    const body = node('div', 'msg__text');
    String(message.content || '')
      .split('\n')
      .forEach(function (line, index) {
        if (index) body.appendChild(document.createElement('br'));
        body.appendChild(document.createTextNode(line));
      });
    wrap.appendChild(body);
    return wrap;
  }

  function artifactCard(message, task) {
    const box = node('div', 'artifact');
    box.dataset.state = task.status;
    box.dataset.taskId = task.id;

    const index = versionIndexOf(task.id);
    const files = (task.artifacts || []).length;

    const head = node('div', 'artifact__head');
    head.appendChild(node('span', 'artifact__badge', '版本 ' + (index > 0 ? index : 1)));
    head.appendChild(
      node('span', 'artifact__state', statusLabel(task) + (files ? ' · ' + files + ' 个文件' : ''))
    );
    box.appendChild(head);

    if (task.status === 'failed') {
      box.appendChild(node('p', 'artifact__error', task.error || '引擎没有返回可预览的产物。'));
      const foot = node('div', 'artifact__foot');
      const retry = node('button', 'link-btn', '重试');
      retry.type = 'button';
      retry.dataset.action = 'retry';
      foot.appendChild(retry);
      box.appendChild(foot);
      return box;
    }

    box.appendChild(
      node(
        'p',
        'artifact__title',
        task.status === 'cancelled'
          ? '任务已取消，产物目录仍保留在 workspace。'
          : shorten(message.content, 88)
      )
    );

    const foot = node('div', 'artifact__foot');
    foot.appendChild(
      node('span', 'artifact__hint', task.id === state.activeId ? '正在预览' : '点击切换预览')
    );

    if (task.status === 'succeeded') {
      const open = node('button', 'link-btn', '新窗口打开');
      open.type = 'button';
      open.dataset.action = 'open';
      foot.appendChild(open);
    }

    box.appendChild(foot);
    return box;
  }

  function artifactMessage(message) {
    const task = message.task_id ? state.tasksById[message.task_id] : null;
    const wrap = node('div', 'msg msg--agent');

    const head = node('div', 'msg__head');
    head.appendChild(node('span', 'agent__avatar agent__avatar--sm', 'A'));

    if (!task) {
      head.appendChild(node('span', 'msg__label', '产物缺失'));
      wrap.appendChild(head);
      wrap.appendChild(node('div', 'artifact__error', '这条消息引用的任务已不存在。'));
      return wrap;
    }

    if (!isTerminal(task.status)) head.appendChild(node('span', 'spinner'));
    head.appendChild(node('span', 'msg__label', progressLabel(task)));
    wrap.appendChild(head);

    const steps = node('ol', 'steps');
    stageStates(task).forEach(function (info) {
      const li = node('li', 'step' + (info.state === 'idle' ? '' : ' is-' + info.state));
      li.appendChild(node('span', 'step__dot'));
      li.appendChild(node('span', 'step__name', STAGE_LABEL[info.key]));
      li.appendChild(
        node('span', 'step__time', info.duration == null ? '' : info.duration.toFixed(1) + 's')
      );
      steps.appendChild(li);
    });
    wrap.appendChild(steps);

    wrap.appendChild(artifactCard(message, task));
    return wrap;
  }

  function renderChat() {
    if (!el.chatLog) return;

    const keepBottom =
      state.scrollToBottom ||
      el.chatLog.scrollHeight === 0 ||
      el.chatLog.scrollHeight - el.chatLog.scrollTop - el.chatLog.clientHeight < 48;
    state.scrollToBottom = false;

    el.chatLog.innerHTML = '';
    updateAgentState();

    if (!state.messages.length) {
      const empty = node('div', 'chat-empty');
      empty.appendChild(node('strong', null, '描述你想做的东西'));
      empty.appendChild(document.createElement('br'));
      empty.appendChild(
        document.createTextNode(
          'Agent 会先确认关键信息，再拆解需求、生成页面；之后可以一路用自然语言改。'
        )
      );
      el.chatLog.appendChild(empty);
      return;
    }

    state.messages.forEach(function (message) {
      if (message.role === 'user') {
        el.chatLog.appendChild(userBubble(message));
      } else if (message.kind === 'question') {
        el.chatLog.appendChild(questionMessage(message));
      } else {
        el.chatLog.appendChild(artifactMessage(message));
      }
    });

    if (keepBottom) el.chatLog.scrollTop = el.chatLog.scrollHeight;
  }

  // ---------- 预览 ----------

  function previewUrl(task) {
    if (!task || task.status !== 'succeeded' || !task.entry) return null;
    const encoded = task.entry.split('/').map(encodeURIComponent).join('/');
    return '/api/tasks/' + encodeURIComponent(task.id) + '/files/' + encoded;
  }

  const LOADING_TIMEOUT = 15000;
  let loadingTimer = null;

  function showLoading() {
    if (!el.loading) return;
    el.loading.hidden = false;
    window.clearTimeout(loadingTimer);
    loadingTimer = window.setTimeout(function () {
      hideLoading();
      showError('预览加载超时，可点击「刷新」或换个版本重试');
    }, LOADING_TIMEOUT);
  }

  function hideLoading() {
    if (el.loading) el.loading.hidden = true;
    window.clearTimeout(loadingTimer);
    loadingTimer = null;
  }

  function syncPreview(animate) {
    state.detail = currentTask();
    renderPreview(animate === true);
    updateAgentState();
  }

  function renderPreview(animate) {
    const task = state.detail;
    const url = previewUrl(task);

    if (!url) {
      hideLoading();
      el.frame.removeAttribute('src');
      el.frame.setAttribute('src', 'about:blank');
      el.empty.hidden = false;
      if (el.emptyTitle && el.emptyText) {
        if (!task) {
          el.emptyTitle.textContent = state.messages.length ? '还没有可预览的版本' : '还没有任务';
          el.emptyText.textContent = state.messages.length
            ? 'Agent 还在确认需求，或产物尚未生成完成。'
            : '在下方描述你想生成的应用，提交后这里会显示生成结果。';
        } else if (task.status === 'failed') {
          el.emptyTitle.textContent = '生成失败';
          el.emptyText.textContent = task.error || '引擎没有返回可预览的产物。';
        } else if (task.status === 'cancelled') {
          el.emptyTitle.textContent = '任务已取消';
          el.emptyText.textContent = '已生成的产物会保留在 workspace 目录中。';
        } else {
          el.emptyTitle.textContent = '正在生成…';
          el.emptyText.textContent = '阶段会随进度点亮，完成后自动加载预览。';
        }
      }
      el.meta.textContent = task ? task.id + ' · ' + statusLabel(task) : '未选择版本';
      return;
    }

    el.empty.hidden = true;
    if (animate || el.frame.getAttribute('src') !== url) {
      showLoading();
      el.frame.setAttribute('src', animate ? url + '?t=' + Date.now() : url);
    }
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

  // ---------- 轮询 ----------

  function startPolling(taskId) {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    state.pollTaskId = taskId;
    state.pollFailures = 0;
    state.pollTimer = window.setInterval(pollOnce, POLL_INTERVAL);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    state.pollTaskId = null;
  }

  function resumePolling() {
    const running = Object.keys(state.tasksById)
      .map(function (id) { return state.tasksById[id]; })
      .filter(function (task) { return !isTerminal(task.status); });
    if (running.length) {
      startPolling(running[running.length - 1].id);
    } else {
      stopPolling();
    }
  }

  async function pollOnce() {
    const taskId = state.pollTaskId;
    if (!taskId) {
      stopPolling();
      return;
    }
    try {
      const detail = await api('/api/tasks/' + encodeURIComponent(taskId));
      if (state.pollTaskId !== taskId) return;

      state.pollFailures = 0;
      state.tasksById[detail.id] = detail;

      if (isTerminal(detail.status)) {
        stopPolling();
        if (!state.activeId || !state.tasksById[state.activeId]) {
          state.activeId = detail.id;
        }
        renderChat();
        syncPreview(true);
        loadSessions();
      } else {
        renderChat();
        syncPreview(false);
      }
    } catch (err) {
      // 单次失败多半是网络抖动：连续多次失败才放弃并提示
      state.pollFailures += 1;
      if (state.pollFailures >= 4) {
        stopPolling();
        showError('轮询失败：' + err.message + '（已停止自动刷新）');
      }
    }
  }

  // ---------- 发送 / 取消 ----------

  function setBusy(busy) {
    state.busy = busy;
    if (el.submit) el.submit.disabled = busy;
    const label = el.submit && el.submit.querySelector('.primary-btn__label');
    if (label) label.textContent = busy ? '发送中…' : '发送';
    updateStatus();
  }

  async function send(content) {
    const text = String(content || '').trim();
    if (!text || state.busy) return;

    if (!state.sessionId) await createSession();
    if (!state.sessionId) return;

    hideError();
    setBusy(true);
    try {
      const result = await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/messages', {
        method: 'POST',
        body: JSON.stringify({ content: text }),
      });
      if (el.input) el.input.value = '';
      await selectSession(state.sessionId, result.task_id || null);
      if (result.task_id) {
        state.activeId = result.task_id;
        syncPreview(false);
        startPolling(result.task_id);
      }
      loadSessions();
    } catch (err) {
      showError('发送失败：' + err.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancelActive() {
    const task = currentTask();
    if (!task || isTerminal(task.status)) return;
    try {
      await api('/api/tasks/' + encodeURIComponent(task.id) + '/cancel', { method: 'POST' });
      await pollOnce();
    } catch (err) {
      showError('取消失败：' + err.message);
    }
  }

  function lastUserContent() {
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      if (state.messages[i].role === 'user') return state.messages[i].content;
    }
    return '';
  }

  function retryLast() {
    const text = lastUserContent();
    if (text) {
      if (el.input) el.input.value = text;
      send(text);
    }
  }

  function openPreview() {
    const url = previewUrl(currentTask());
    if (url) window.open(url, '_blank', 'noopener');
  }

  // ---------- 事件绑定 ----------

  function bind() {
    if (el.sessionList) {
      el.sessionList.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-session-id]');
        if (!btn) return;
        if (btn.dataset.sessionId === state.sessionId) return;
        selectSession(btn.dataset.sessionId);
      });
    }

    if (el.newSession) el.newSession.addEventListener('click', createSession);

    if (el.historyBtn) el.historyBtn.addEventListener('click', toggleHistory);

    if (el.historyMore) {
      el.historyMore.addEventListener('click', async function () {
        state.history.limit = Math.min(state.history.limit + 20, 200);
        await loadHistory();
      });
    }

    if (el.historyList) {
      el.historyList.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-session-id]');
        if (!btn) return;
        closeHistory();
        if (btn.dataset.sessionId === state.sessionId) return;
        selectSession(btn.dataset.sessionId);
      });
    }

    if (el.chatLog) {
      el.chatLog.addEventListener('click', function (event) {
        const actionBtn = event.target.closest('[data-action]');
        if (actionBtn) {
          if (actionBtn.dataset.action === 'retry') retryLast();
          else if (actionBtn.dataset.action === 'open') openPreview();
          return;
        }
        const card = event.target.closest('[data-task-id]');
        if (!card) return;
        state.activeId = card.dataset.taskId;
        renderChat();
        syncPreview(true);
      });
    }

    el.examples.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-prompt]');
      if (!btn) return;
      el.input.value = btn.dataset.prompt;
      el.input.focus();
    });

    el.submit.addEventListener('click', function () {
      send(el.input.value);
    });

    el.input.addEventListener('keydown', function (event) {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        el.submit.click();
      }
    });

    if (el.cancel) el.cancel.addEventListener('click', cancelActive);

    el.widthGroup.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-width]');
      if (!btn) return;
      state.width = btn.dataset.width;
      el.canvas.dataset.width = state.width;
      el.widthGroup.querySelectorAll('.width-btn').forEach(function (item) {
        item.classList.toggle('is-active', item.dataset.width === state.width);
      });
    });

    el.refresh.addEventListener('click', function () {
      renderPreview(true);
    });

    el.frame.addEventListener('load', hideLoading);

    el.popout.addEventListener('click', openPreview);

    el.helpBtn.addEventListener('click', function () {
      const open = el.helpPanel.hidden;
      el.helpPanel.hidden = !open;
      el.helpBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    document.addEventListener('click', function (event) {
      if (!el.helpPanel.hidden) {
        const insideHelp =
          el.helpPanel.contains(event.target) || el.helpBtn.contains(event.target);
        if (!insideHelp) {
          el.helpPanel.hidden = true;
          el.helpBtn.setAttribute('aria-expanded', 'false');
        }
      }

      if (el.historyPanel && !el.historyPanel.hidden) {
        const insideHistory =
          el.historyPanel.contains(event.target) ||
          (el.historyBtn && el.historyBtn.contains(event.target));
        if (!insideHistory) closeHistory();
      }
    });

    el.retry.addEventListener('click', retryLast);

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      if (!el.helpPanel.hidden) {
        el.helpPanel.hidden = true;
        el.helpBtn.setAttribute('aria-expanded', 'false');
      }
      closeHistory();
    });
  }

  // ---------- 启动 ----------

  async function init() {
    bind();
    updateStatus();
    probeHealth();

    const sessions = await loadSessions();
    if (sessions.length) {
      await selectSession(sessions[0].id);
    } else {
      await createSession();
    }
    renderChat();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
