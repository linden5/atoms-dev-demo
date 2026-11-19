(function () {
  'use strict';

  const STAGES = ['planning', 'coding', 'checking'];
  const STAGE_LABEL = { planning: '需求规划', coding: '编码生成', checking: '自检与落库' };
  const POLL_INTERVAL = 1500;
  const TERMINAL = { succeeded: true, failed: true, cancelled: true };

  const backend = { online: false, step: 's8', engine: 'none' };

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
    view: 'home',
    scrollToBottom: false,
    history: { items: [], total: 0, limit: 20, tab: 'versions', query: '' },
    // 发布：publish 是当前应用（会话）的公开地址，task_id 指向它绑定的版本
    publish: null,
    publishBusy: false,
    publishError: '',
    publishNotice: '',
  };

  const el = {
    version: document.getElementById('version-tag'),
    brand: document.getElementById('brand-home-btn'),
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
    historyTabs: document.querySelector('.history__tabs'),
    historyVersions: document.getElementById('history-versions'),
    historyMessages: document.getElementById('history-messages'),
    historySessions: document.getElementById('history-sessions'),
    historySearch: document.getElementById('history-search'),
    historyMessageList: document.getElementById('history-message-list'),
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
    previewNote: document.getElementById('preview-note'),
    publishBtn: document.getElementById('publish-btn'),
    publishPanel: document.getElementById('publish-panel'),
    publishSlug: document.getElementById('publish-slug'),
    publishStatus: document.getElementById('publish-status'),
    publishPrimary: document.getElementById('publish-primary'),
    publishView: document.getElementById('publish-view'),
    publishCopy: document.getElementById('publish-copy'),
    publishUnpublish: document.getElementById('publish-unpublish'),
    shareBtn: document.getElementById('share-btn'),
    sharePanel: document.getElementById('share-panel'),
    shareStatus: document.getElementById('share-status'),
    shareCopy: document.getElementById('share-copy'),
    shareZip: document.getElementById('share-zip'),
    homeView: document.getElementById('home-view'),
    workspaceView: document.getElementById('workspace-view'),
    homeBack: document.getElementById('home-back-btn'),
    homeNavChat: document.getElementById('home-nav-chat'),
    homeNavHistory: document.getElementById('home-nav-history'),
    homeNavCount: document.getElementById('home-nav-count'),
    homePaneChat: document.getElementById('home-pane-chat'),
    homePaneHistory: document.getElementById('home-pane-history'),
    homeInput: document.getElementById('home-input'),
    homeSubmit: document.getElementById('home-submit-btn'),
    homeExamples: document.getElementById('home-example-list'),
    homeError: document.getElementById('home-error'),
    homeTaskList: document.getElementById('home-task-list'),
    homeHistoryCount: document.getElementById('home-history-count'),
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

    const step = String(backend.step || 's8').toUpperCase();
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
      state.publish = null;
      stopPolling();
      renderChat();
      syncPreview(false);
      renderPublish();
      await loadSessions();
      refreshHistoryIfOpen();
      if (state.view === 'home') {
        if (el.homeInput) el.homeInput.focus();
      } else if (el.input) {
        el.input.focus();
      }
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

    if (el.historyCount && state.history.tab === 'sessions') {
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

  // ---------- 历史：版本 / 消息 / 会话 ----------
  //
  // 三个标签页对应三种"回到过去"的方式（对齐 help 文档的 History）：
  // - 版本：切回某个版本预览（Restore，不删除后续版本）；
  // - 消息：搜索并跳转到某条消息，只滚动，不改变项目状态；
  // - 会话：切换到另一个应用/会话（原来的历史会话列表）。

  function setHistoryTab(tab) {
    state.history.tab = tab;
    if (el.historyTabs) {
      Array.prototype.forEach.call(el.historyTabs.querySelectorAll('[data-tab]'), function (btn) {
        const active = btn.dataset.tab === tab;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }
    if (el.historyVersions) el.historyVersions.hidden = tab !== 'versions';
    if (el.historyMessages) el.historyMessages.hidden = tab !== 'messages';
    if (el.historySessions) el.historySessions.hidden = tab !== 'sessions';
    renderHistoryAll();
  }

  function versionList() {
    const items = [];
    state.messages.forEach(function (message) {
      if (message.kind !== 'artifact' || !message.task_id) return;
      const task = state.tasksById[message.task_id];
      if (!task) return;
      items.push({ message: message, task: task, index: items.length + 1 });
    });
    return items;
  }

  function renderVersions() {
    if (!el.historyVersions) return;
    el.historyVersions.innerHTML = '';

    const items = versionList().filter(function (item) {
      return item.task.status === 'succeeded';
    });
    if (!items.length) {
      el.historyVersions.appendChild(
        node('p', 'history__empty', '还没有可用版本，先生成一个。')
      );
      return;
    }

    const list = node('ul', 'history__list');
    items.forEach(function (item) {
      const li = document.createElement('li');
      const btn = node(
        'button',
        'history__item' + (item.task.id === state.activeId ? ' is-active' : '')
      );
      btn.type = 'button';
      btn.dataset.taskId = item.task.id;
      btn.title = item.task.prompt || '';
      const name = item.task.version_title || shorten(item.message.content, 24);
      btn.appendChild(node('span', 'history__name', '版本 ' + item.index + '：' + name));

      const meta = node('div', 'history__meta');
      meta.appendChild(node('span', null, fmtStamp(item.task.created_at)));
      meta.appendChild(node('span', null, (item.task.artifacts || []).length + ' 个文件'));
      meta.appendChild(
        node('span', null, item.task.id === state.activeId ? '正在预览' : 'Restore')
      );
      btn.appendChild(meta);
      li.appendChild(btn);
      list.appendChild(li);
    });
    el.historyVersions.appendChild(list);
  }

  function messageKindLabel(message) {
    if (message.role === 'user') return '你的需求';
    if (message.kind === 'artifact') return '生成结果';
    if (message.kind === 'question') return '追问';
    return 'Agent';
  }

  function renderMessageHistory() {
    if (!el.historyMessageList) return;
    const query = String(state.history.query || '').trim().toLowerCase();
    el.historyMessageList.innerHTML = '';

    const items = state.messages.filter(function (message) {
      if (!query) return true;
      return String(message.content || '').toLowerCase().indexOf(query) >= 0;
    });
    if (!items.length) {
      el.historyMessageList.appendChild(
        node('li', 'history__empty', query ? '没有匹配的消息' : '还没有消息')
      );
      return;
    }

    items.forEach(function (message) {
      const li = document.createElement('li');
      const btn = node('button', 'history__item');
      btn.type = 'button';
      btn.dataset.messageId = String(message.id);
      const who = message.role === 'user' ? '你' : 'Agent';
      btn.appendChild(node('span', 'history__name', who + '：' + shorten(message.content, 30)));

      const meta = node('div', 'history__meta');
      meta.appendChild(node('span', null, fmtStamp(message.created_at)));
      meta.appendChild(node('span', null, messageKindLabel(message)));
      meta.appendChild(node('span', null, '跳转'));
      btn.appendChild(meta);
      li.appendChild(btn);
      el.historyMessageList.appendChild(li);
    });
  }

  function renderHistoryAll() {
    renderVersions();
    renderMessageHistory();
    if (el.historyCount && state.history.tab !== 'sessions') {
      const versions = versionList().filter(function (item) {
        return item.task.status === 'succeeded';
      }).length;
      el.historyCount.textContent = versions
        ? versions + ' 个版本 · ' + state.messages.length + ' 条消息'
        : '';
    }
  }

  function jumpToMessage(messageId) {
    closeHistory();
    const target = el.chatLog
      ? el.chatLog.querySelector('[data-message-id="' + messageId + '"]')
      : null;
    if (!target) return;
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    target.classList.add('is-flash');
    window.setTimeout(function () {
      target.classList.remove('is-flash');
    }, 1400);
  }

  function restoreVersion(taskId) {
    if (!state.tasksById[taskId]) return;
    state.activeId = taskId;
    closeHistory();
    renderChat();
    syncPreview(true);
    refreshPublish();
  }

  async function openHistory() {
    if (!el.historyPanel) return;
    el.historyPanel.hidden = false;
    if (el.historyBtn) el.historyBtn.setAttribute('aria-expanded', 'true');
    setHistoryTab(state.history.tab || 'versions');
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
    if (el.historyPanel && !el.historyPanel.hidden) {
      renderHistory();
      renderHistoryAll();
    }
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
      refreshPublish();
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

  // 真实引擎一次要 30–90 秒，只写"正在生成…"会让人怀疑卡住，所以带上秒数
  function elapsedSeconds(task) {
    const began = Date.parse(task.created_at || '');
    if (Number.isNaN(began)) return null;
    const ended = isTerminal(task.status) ? Date.parse(task.updated_at || '') : Date.now();
    if (Number.isNaN(ended)) return null;
    return Math.max(0, Math.round((ended - began) / 1000));
  }

  // 调用级事件（引擎上报的重试 / 修正提示）不是阶段名，过滤条件用固定前缀识别
  const CALL_NOTE_PREFIX = '调用';

  function latestCallNote(task) {
    if (!task || isTerminal(task.status)) return '';
    const notes = (task.events || []).filter(function (event) {
      return STAGES.indexOf(event.message) < 0 && (event.message || '').indexOf(CALL_NOTE_PREFIX) === 0;
    });
    if (!notes.length) return '';
    const text = notes[notes.length - 1].message;
    return text.length > 60 ? text.slice(0, 60) + '…' : text;
  }

  function progressLabel(task) {
    const done = completedSteps(task);
    const elapsed = elapsedSeconds(task);
    const clock = elapsed == null ? '' : ' · ' + elapsed + 's';
    if (task.status === 'succeeded') return '已处理 ' + done + ' 步' + clock;
    if (task.status === 'failed') return '已处理 ' + done + ' 步 · 失败' + clock;
    if (task.status === 'cancelled') return '已处理 ' + done + ' 步 · 已取消' + clock;
    if (task.status === 'running') {
      const note = latestCallNote(task);
      return '正在' + (STAGE_LABEL[task.stage] || '生成') + '…' + clock + (note ? ' · ' + note : '');
    }
    return '排队中…' + clock;
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
    // 供 History → 消息 跳转定位
    wrap.dataset.messageId = String(message.id);
    wrap.appendChild(node('div', 'msg__bubble', message.content));
    return wrap;
  }

  function questionMessage(message) {
    const wrap = node('div', 'msg msg--agent');
    wrap.dataset.messageId = String(message.id);
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
      // 分层建议：先排除"页面没刷新/旧任务"，再谈模型本身，避免用户一上来就反复重试
      box.appendChild(
        node(
          'p',
          'artifact__hint',
          '可以先刷新页面或新开窗口确认不是显示问题；同一需求建议只重试一次，仍失败就把需求写得更具体（要哪些控件、什么数据）。'
        )
      );
      const foot = node('div', 'artifact__foot');
      const retry = node('button', 'link-btn', '重试');
      retry.type = 'button';
      retry.dataset.action = 'retry';
      foot.appendChild(retry);
      box.appendChild(foot);
      return box;
    }

    // 有版本名就显示版本名（来自引擎 meta.json），否则回落到用户这一轮的原话
    const heading =
      task.status === 'cancelled'
        ? '任务已取消，产物目录仍保留在 workspace。'
        : task.version_title || shorten(message.content, 88);
    box.appendChild(node('p', 'artifact__title', heading));

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
    wrap.dataset.messageId = String(message.id);

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
    if (el.homeError) {
      el.homeError.textContent = message;
      el.homeError.hidden = false;
    }
  }

  function hideError() {
    el.errorBanner.hidden = true;
    if (el.homeError) el.homeError.hidden = true;
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
        refreshPublish();
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
    if (el.homeSubmit) el.homeSubmit.disabled = busy;
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
      if (el.homeInput) el.homeInput.value = '';
      await selectSession(state.sessionId, result.task_id || null);
      if (result.task_id) {
        state.activeId = result.task_id;
        syncPreview(false);
        startPolling(result.task_id);
      }
      loadSessions();
      showView('workspace');
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
    // 只把上次的需求填回输入框，不自动发送：
    // 后端每收到一条消息就追加一条用户消息，自动重发会让气泡越攒越多。
    const text = lastUserContent();
    if (!text || !el.input) return;
    el.input.value = text;
    el.input.focus();
    el.input.setSelectionRange(text.length, text.length);
  }

  function openPreview() {
    const url = previewUrl(currentTask());
    if (url) window.open(url, '_blank', 'noopener');
  }

  // ---------- 发布 / 分享 ----------
  //
  // 心智模型（对齐 help 文档）：预览是工作副本，`/p/{slug}` 才是访客能打开的版本。
  // 同一个会话 = 同一个应用，所以"发布"绑在会话上，Update 只是把地址指向新版本。

  function publishedUrl(slug) {
    return slug ? window.location.origin + '/p/' + slug : '';
  }

  function setPublishStatus(text, tone) {
    if (!el.publishStatus) return;
    el.publishStatus.textContent = text;
    if (tone) el.publishStatus.dataset.tone = tone;
    else el.publishStatus.removeAttribute('data-tone');
  }

  async function refreshPublish() {
    const task = currentTask();
    if (!task) {
      state.publish = null;
      renderPublish();
      return;
    }
    try {
      const data = await api('/api/tasks/' + encodeURIComponent(task.id) + '/publish');
      state.publish = (data && data.publish) || null;
    } catch (err) {
      state.publish = null;
    }
    renderPublish();
  }

  function renderPublish() {
    const task = currentTask();
    const pub = state.publish;
    const ready = Boolean(task && task.status === 'succeeded');
    const upToDate = Boolean(pub && task && pub.task_id === task.id);

    if (el.publishBtn) {
      el.publishBtn.textContent = pub ? (upToDate ? 'Up to date' : 'Update') : 'Publish';
      el.publishBtn.disabled = state.publishBusy;
    }
    if (el.publishPrimary) {
      el.publishPrimary.textContent = state.publishBusy
        ? (pub ? 'Updating…' : 'Publishing…')
        : (pub ? (upToDate ? 'Up to date' : 'Update') : 'Publish');
      el.publishPrimary.disabled = state.publishBusy || !ready || upToDate;
    }
    if (el.publishSlug && pub) el.publishSlug.value = pub.slug;
    if (el.publishView) el.publishView.hidden = !pub;
    if (el.publishCopy) el.publishCopy.hidden = !pub;
    if (el.publishUnpublish) el.publishUnpublish.hidden = !pub;

    if (state.publishNotice) {
      setPublishStatus(state.publishNotice, 'ok');
      state.publishNotice = '';
    } else if (state.publishError) {
      setPublishStatus(state.publishError, 'err');
    } else if (!task) {
      setPublishStatus('先生成或选择一个版本。');
    } else if (!ready) {
      setPublishStatus('当前版本还没生成完成。');
    } else if (!pub) {
      setPublishStatus('填一个地址，点 Publish 得到公开链接。');
    } else if (upToDate) {
      setPublishStatus('Your app is live! · ' + publishedUrl(pub.slug), 'ok');
    } else {
      setPublishStatus('线上仍是 /p/' + pub.slug + ' 的旧版本，点 Update 推送当前版本。', 'warn');
    }

    if (el.shareStatus) {
      el.shareStatus.textContent = pub ? publishedUrl(pub.slug) : '还没有发布，先点 Publish。';
      if (pub) el.shareStatus.dataset.tone = 'ok';
      else el.shareStatus.removeAttribute('data-tone');
    }
    if (el.shareCopy) el.shareCopy.disabled = !pub;
    if (el.shareZip) el.shareZip.disabled = !ready;

    renderPreviewNote();
  }

  function renderPreviewNote() {
    if (!el.previewNote) return;
    const task = currentTask();
    const pub = state.publish;
    if (pub && task && pub.task_id === task.id) {
      el.previewNote.textContent = '已发布：' + publishedUrl(pub.slug) + ' · 访客看到的就是这一版';
      el.previewNote.dataset.tone = 'ok';
    } else if (pub) {
      el.previewNote.textContent = '有未发布的改动：线上仍是 /p/' + pub.slug + ' 的旧版本';
      el.previewNote.removeAttribute('data-tone');
    } else {
      el.previewNote.textContent = '预览是工作副本，访客看到的是已发布版本。';
      el.previewNote.removeAttribute('data-tone');
    }
  }

  async function publishNow() {
    const task = currentTask();
    if (!task || task.status !== 'succeeded' || state.publishBusy) return;
    state.publishBusy = true;
    state.publishError = '';
    renderPublish();
    try {
      const body = { slug: (el.publishSlug && el.publishSlug.value.trim()) || '' };
      if (task.prompt) body.title = task.prompt.slice(0, 60);
      const data = await api('/api/tasks/' + encodeURIComponent(task.id) + '/publish', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      state.publish = data;
      if (el.publishSlug) el.publishSlug.value = data.slug;
      state.publishNotice = data.updated ? '已更新到当前版本' : '发布成功：' + publishedUrl(data.slug);
    } catch (err) {
      state.publishError = '发布失败：' + err.message;
    } finally {
      state.publishBusy = false;
      renderPublish();
    }
  }

  async function unpublish() {
    const pub = state.publish;
    if (!pub) return;
    try {
      await api('/api/publishes/' + encodeURIComponent(pub.slug), { method: 'DELETE' });
      state.publish = null;
      state.publishError = '';
      state.publishNotice = '已取消发布';
      renderPublish();
    } catch (err) {
      state.publishError = '取消发布失败：' + err.message;
      renderPublish();
    }
  }

  function copyText(text) {
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return false; }
      );
    }
    return Promise.resolve(false);
  }

  function copyPublishedLink() {
    const pub = state.publish;
    if (!pub) return;
    copyText(publishedUrl(pub.slug)).then(function (ok) {
      state.publishNotice = ok ? '链接已复制' : '复制失败，请手动选中地址';
      renderPublish();
    });
  }

  function openPublished() {
    const pub = state.publish;
    if (pub) window.open(publishedUrl(pub.slug), '_blank', 'noopener');
  }

  function exportZip() {
    const task = currentTask();
    if (task) window.location.href = '/api/tasks/' + encodeURIComponent(task.id) + '/zip';
  }

  function closePanels() {
    [el.publishPanel, el.sharePanel, el.helpPanel].forEach(function (panel) {
      if (panel) panel.hidden = true;
    });
    [el.publishBtn, el.shareBtn, el.helpBtn].forEach(function (btn) {
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function togglePanel(panel, btn) {
    if (!panel) return;
    const open = panel.hidden;
    closePanels();
    panel.hidden = !open;
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open && panel === el.publishPanel) refreshPublish();
  }

  // ---------- 首页视图 ----------

  function showView(view) {
    state.view = view;
    if (el.homeView) el.homeView.hidden = view !== 'home';
    if (el.workspaceView) el.workspaceView.hidden = view !== 'workspace';
    if (el.homeBack) el.homeBack.hidden = view === 'home';
    if (view === 'home') renderHomeTasks();
  }

  function setHomePane(pane) {
    const chat = pane === 'chat';
    if (el.homeNavChat) {
      el.homeNavChat.classList.toggle('is-active', chat);
      el.homeNavChat.setAttribute('aria-pressed', chat ? 'true' : 'false');
    }
    if (el.homeNavHistory) {
      el.homeNavHistory.classList.toggle('is-active', !chat);
      el.homeNavHistory.setAttribute('aria-pressed', chat ? 'false' : 'true');
    }
    if (el.homePaneChat) el.homePaneChat.hidden = !chat;
    if (el.homePaneHistory) el.homePaneHistory.hidden = chat;
    if (!chat) renderHomeTasks();
  }

  function renderHomeTasks() {
    if (!el.homeTaskList) return;
    el.homeTaskList.innerHTML = '';
    const sessions = state.sessions;
    const total = state.totalSessions || sessions.length;

    if (el.homeHistoryCount) {
      el.homeHistoryCount.textContent = total ? total + ' 个会话' : '';
    }
    if (el.homeNavCount) {
      el.homeNavCount.textContent = String(total);
      el.homeNavCount.hidden = !total;
    }

    if (!sessions.length) {
      el.homeTaskList.appendChild(
        node('li', 'history__empty', '还没有任务，回「对话」发一句需求就开始了')
      );
      return;
    }

    sessions.forEach(function (session) {
      const li = document.createElement('li');
      const btn = node(
        'button',
        'home__task' + (session.id === state.sessionId ? ' is-active' : '')
      );
      btn.type = 'button';
      btn.dataset.sessionId = session.id;
      btn.title = session.title || '新会话';
      btn.appendChild(node('span', 'home__task-name', session.title || '新会话'));

      const meta = node('span', 'history__meta');
      meta.appendChild(node('span', null, fmtStamp(session.updated_at)));
      meta.appendChild(node('span', null, session.message_count + ' 条消息'));
      meta.appendChild(node('span', null, '打开工作台'));
      btn.appendChild(meta);
      li.appendChild(btn);
      el.homeTaskList.appendChild(li);
    });
  }

  function openSessionFromHome(sessionId) {
    showView('workspace');
    if (!sessionId || sessionId === state.sessionId) return;
    selectSession(sessionId);
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

    if (el.historyTabs) {
      el.historyTabs.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-tab]');
        if (btn) setHistoryTab(btn.dataset.tab);
      });
    }

    if (el.historyVersions) {
      el.historyVersions.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-task-id]');
        if (btn) restoreVersion(btn.dataset.taskId);
      });
    }

    if (el.historyMessageList) {
      el.historyMessageList.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-message-id]');
        if (btn) jumpToMessage(btn.dataset.messageId);
      });
    }

    if (el.historySearch) {
      el.historySearch.addEventListener('input', function () {
        state.history.query = el.historySearch.value;
        renderMessageHistory();
      });
    }

    if (el.homeNavChat) {
      el.homeNavChat.addEventListener('click', function () {
        setHomePane('chat');
      });
    }

    if (el.homeNavHistory) {
      el.homeNavHistory.addEventListener('click', function () {
        setHomePane('history');
      });
    }

    if (el.homeTaskList) {
      el.homeTaskList.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-session-id]');
        if (btn) openSessionFromHome(btn.dataset.sessionId);
      });
    }

    if (el.homeExamples) {
      el.homeExamples.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-prompt]');
        if (!btn) return;
        el.homeInput.value = btn.dataset.prompt;
        el.homeInput.focus();
      });
    }

    if (el.homeSubmit) {
      el.homeSubmit.addEventListener('click', function () {
        send(el.homeInput.value);
      });
    }

    if (el.homeInput) {
      el.homeInput.addEventListener('keydown', function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
          event.preventDefault();
          el.homeSubmit.click();
        }
      });
    }

    if (el.homeBack) el.homeBack.addEventListener('click', function () {
      showView('home');
    });

    if (el.brand) el.brand.addEventListener('click', function () {
      showView('home');
    });

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
        refreshPublish();
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

    if (el.helpBtn) {
      el.helpBtn.addEventListener('click', function () {
        togglePanel(el.helpPanel, el.helpBtn);
      });
    }

    if (el.publishBtn) {
      el.publishBtn.addEventListener('click', function () {
        togglePanel(el.publishPanel, el.publishBtn);
      });
    }

    if (el.shareBtn) {
      el.shareBtn.addEventListener('click', function () {
        togglePanel(el.sharePanel, el.shareBtn);
        renderPublish();
      });
    }

    if (el.publishPrimary) el.publishPrimary.addEventListener('click', publishNow);
    if (el.publishView) el.publishView.addEventListener('click', openPublished);
    if (el.publishCopy) el.publishCopy.addEventListener('click', copyPublishedLink);
    if (el.publishUnpublish) el.publishUnpublish.addEventListener('click', unpublish);
    if (el.shareCopy) el.shareCopy.addEventListener('click', copyPublishedLink);
    if (el.shareZip) el.shareZip.addEventListener('click', exportZip);

    if (el.publishSlug) {
      el.publishSlug.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          publishNow();
        }
      });
      el.publishSlug.addEventListener('input', function () {
        state.publishError = '';
      });
    }

    document.addEventListener('click', function (event) {
      const inPanel =
        (el.helpPanel && el.helpPanel.contains(event.target)) ||
        (el.publishPanel && el.publishPanel.contains(event.target)) ||
        (el.sharePanel && el.sharePanel.contains(event.target));
      const onTrigger =
        (el.helpBtn && el.helpBtn.contains(event.target)) ||
        (el.publishBtn && el.publishBtn.contains(event.target)) ||
        (el.shareBtn && el.shareBtn.contains(event.target));
      if (!inPanel && !onTrigger) closePanels();

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
      closePanels();
      closeHistory();
    });
  }

  // ---------- 启动 ----------

  async function init() {
    bind();
    updateStatus();
    probeHealth();
    showView('home');
    await loadSessions();
    renderHomeTasks();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
