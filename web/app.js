(function () {
  'use strict';

  const STAGES = ['planning', 'coding', 'checking'];
  const STAGE_LABEL = { planning: '需求规划', coding: '编码生成', checking: '自检与落库' };

  const ARTIFACT_POMODORO = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>番茄钟</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(800px 500px at 50% 0%,#1b2430,#0b0f14 70%);color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  .wrap{width:min(340px,86vw);padding:26px;border:1px solid #232d3a;border-radius:16px;background:rgba(20,26,34,.86);text-align:center;box-shadow:0 30px 60px -30px #000}
  .mode{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#9ba7b4}
  .time{font-size:62px;line-height:1.1;font-variant-numeric:tabular-nums;letter-spacing:2px;margin:10px 0 4px;background:linear-gradient(135deg,#e6edf3,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}
  .hint{font-size:12px;color:#6b7683;margin-bottom:18px}
  .row{display:flex;gap:8px}
  button{flex:1;padding:10px;border-radius:9px;border:1px solid #2f3c4d;background:#1b2430;color:#e6edf3;font-size:13px;cursor:pointer;transition:transform .15s,border-color .15s}
  button:hover{transform:translateY(-1px);border-color:#4b5b70}
  button.primary{border:0;background:linear-gradient(135deg,#6366f1,#22d3ee);color:#080d13;font-weight:600}
  .dots{display:flex;gap:6px;justify-content:center;margin-top:16px}
  .dot{width:6px;height:6px;border-radius:50%;background:#2f3c4d}
  .dot.on{background:#22c55e}
</style></head>
<body><div class="wrap">
  <div class="mode" id="mode">专注</div>
  <div class="time" id="clock">25:00</div>
  <div class="hint" id="hint">点击开始，进入 25 分钟专注</div>
  <div class="row"><button class="primary" id="toggle">开始</button><button id="reset">重置</button></div>
  <div class="dots" id="dots"></div>
</div>
<script>
  var FOCUS=25*60, BREAK=5*60, total=FOCUS, left=FOCUS, timer=null, rounds=0;
  var clock=document.getElementById('clock'), mode=document.getElementById('mode');
  var hint=document.getElementById('hint'), toggle=document.getElementById('toggle');
  var dots=document.getElementById('dots');
  function paint(){
    var m=Math.floor(left/60), s=left%60;
    clock.textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
    var html='';
    for(var i=0;i<4;i++){ html+='<span class="dot'+(i<rounds?' on':'')+'"></span>'; }
    dots.innerHTML=html;
  }
  function tick(){
    if(left<=0){
      clearInterval(timer); timer=null; toggle.textContent='开始';
      if(total===FOCUS){ rounds++; mode.textContent='休息'; total=BREAK; left=BREAK; hint.textContent='完成一轮专注，休息 5 分钟'; }
      else { mode.textContent='专注'; total=FOCUS; left=FOCUS; hint.textContent='休息结束，可以继续下一轮'; }
      paint(); return;
    }
    left--; paint();
  }
  toggle.addEventListener('click',function(){
    if(timer){ clearInterval(timer); timer=null; toggle.textContent='继续'; hint.textContent='已暂停'; return; }
    timer=setInterval(tick,1000); toggle.textContent='暂停'; hint.textContent= mode.textContent==='专注'?'专注进行中，保持节奏':'休息中，放松一下';
  });
  document.getElementById('reset').addEventListener('click',function(){
    clearInterval(timer); timer=null; total=FOCUS; left=FOCUS; toggle.textContent='开始';
    mode.textContent='专注'; hint.textContent='点击开始，进入 25 分钟专注'; paint();
  });
  paint();
<\/script></body></html>`;

  const ARTIFACT_TODO = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>待办清单</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;padding:28px 18px;background:#0b0f14;color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;justify-content:center}
  .app{width:min(420px,100%)}
  h1{margin:0 0 4px;font-size:18px;font-weight:600}
  .sub{margin:0 0 18px;font-size:12px;color:#6b7683}
  .composer{display:flex;gap:8px;margin-bottom:14px}
  input{flex:1;padding:10px 12px;border-radius:9px;border:1px solid #232d3a;background:#111823;color:#e6edf3;font-size:13px}
  input:focus{outline:none;border-color:#4f46e5}
  .add{padding:10px 14px;border:0;border-radius:9px;background:linear-gradient(135deg,#6366f1,#22d3ee);color:#080d13;font-weight:600;font-size:13px;cursor:pointer}
  ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
  li{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #232d3a;border-radius:10px;background:#141a22;transition:border-color .15s,transform .15s}
  li:hover{transform:translateY(-1px);border-color:#2f3c4d}
  li.done .text{text-decoration:line-through;color:#6b7683}
  .box{width:16px;height:16px;border-radius:5px;border:1px solid #3b4a5e;display:grid;place-items:center;cursor:pointer;font-size:10px;color:#080d13}
  li.done .box{background:#22c55e;border-color:#22c55e}
  .text{flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .del{border:0;background:transparent;color:#6b7683;cursor:pointer;font-size:14px;line-height:1}
  .del:hover{color:#ef4444}
  .foot{margin-top:14px;font-size:12px;color:#9ba7b4;display:flex;justify-content:space-between}
  .empty{color:#6b7683;font-size:12.5px;text-align:center;padding:18px 0}
</style></head>
<body><div class="app">
  <h1>待办清单</h1>
  <p class="sub">本地状态，刷新即重置（沙箱内不使用存储）</p>
  <div class="composer">
    <input id="field" placeholder="添加一条待办，回车确认" maxlength="60">
    <button class="add" id="add">添加</button>
  </div>
  <ul id="list"></ul>
  <div class="foot"><span id="left">剩余 0 项</span><span id="total">共 0 项</span></div>
</div>
<script>
  var items = [
    {id:1,text:'确认需求边界',done:true},
    {id:2,text:'搭建设计系统',done:false},
    {id:3,text:'接入生成引擎',done:false}
  ];
  var seq = 4;
  var list=document.getElementById('list'), field=document.getElementById('field');
  function render(){
    list.innerHTML='';
    if(!items.length){ var p=document.createElement('div'); p.className='empty'; p.textContent='还没有待办，先加一条'; list.appendChild(p); }
    items.forEach(function(it){
      var li=document.createElement('li');
      if(it.done) li.className='done';
      var box=document.createElement('span'); box.className='box'; box.textContent= it.done?'✓':'';
      box.addEventListener('click',function(){ it.done=!it.done; render(); });
      var text=document.createElement('span'); text.className='text'; text.textContent=it.text;
      var del=document.createElement('button'); del.className='del'; del.textContent='×';
      del.addEventListener('click',function(){ items=items.filter(function(x){return x.id!==it.id;}); render(); });
      li.appendChild(box); li.appendChild(text); li.appendChild(del); list.appendChild(li);
    });
    var remain=items.filter(function(x){return !x.done;}).length;
    document.getElementById('left').textContent='剩余 '+remain+' 项';
    document.getElementById('total').textContent='共 '+items.length+' 项';
  }
  function add(){
    var v=field.value.trim(); if(!v) return;
    items.push({id:seq++,text:v,done:false}); field.value=''; render();
  }
  document.getElementById('add').addEventListener('click',add);
  field.addEventListener('keydown',function(e){ if(e.key==='Enter') add(); });
  render();
<\/script></body></html>`;

  function buildGeneratedApp(prompt) {
    const safe = String(prompt).replace(/[<>&]/g, function (ch) {
      return ch === '<' ? '&lt;' : ch === '>' ? '&gt;' : '&amp;';
    });
    return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>生成结果</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(900px 520px at 20% 0%,#161d27,#0b0f14 65%);color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  .card{width:min(520px,100%);padding:24px;border:1px solid #232d3a;border-radius:16px;background:rgba(20,26,34,.9);box-shadow:0 30px 70px -40px #000}
  .badge{display:inline-block;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#22d3ee;border:1px solid rgba(34,211,238,.3);border-radius:999px;padding:2px 8px}
  h1{margin:12px 0 8px;font-size:19px;font-weight:600;line-height:1.4}
  .req{margin:0 0 18px;padding:10px 12px;border-left:2px solid #6366f1;background:#0e141b;border-radius:6px;color:#9ba7b4;font-size:12.5px;line-height:1.6}
  .counter{display:flex;align-items:center;gap:12px}
  .num{font-size:38px;font-variant-numeric:tabular-nums;background:linear-gradient(135deg,#e6edf3,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}
  button{padding:9px 16px;border-radius:9px;border:1px solid #2f3c4d;background:#1b2430;color:#e6edf3;font-size:13px;cursor:pointer;transition:transform .15s,border-color .15s}
  button:hover{transform:translateY(-1px);border-color:#4b5b70}
  button.primary{border:0;background:linear-gradient(135deg,#6366f1,#22d3ee);color:#080d13;font-weight:600}
  .foot{margin-top:16px;font-size:11.5px;color:#6b7683;font-family:ui-monospace,monospace}
</style></head>
<body><div class="card">
  <span class="badge">generated</span>
  <h1>演示应用已生成</h1>
  <p class="req">${safe}</p>
  <div class="counter">
    <span class="num" id="n">0</span>
    <button class="primary" id="inc">+1</button>
    <button id="reset">重置</button>
  </div>
  <p class="foot">S1 静态版本：内容为前端模拟产物，S3 起由引擎真实生成。</p>
</div>
<script>
  var n=0, el=document.getElementById('n');
  document.getElementById('inc').addEventListener('click',function(){ n++; el.textContent=n; });
  document.getElementById('reset').addEventListener('click',function(){ n=0; el.textContent=n; });
<\/script></body></html>`;
  }

  const DEMO_TASKS = [
    {
      id: 'demo-pomodoro',
      prompt: '做一个番茄钟，25 分钟专注 + 5 分钟休息，深色主题',
      status: 'succeeded',
      stage: 'checking',
      artifact: ARTIFACT_POMODORO,
      createdAt: '2026-11-18T09:12:00Z',
      parentId: null,
      durations: { planning: 0.8, coding: 6.4, checking: 1.1 },
    },
    {
      id: 'demo-todo',
      prompt: '做一个待办清单，可以添加、勾选和删除，显示剩余数量',
      status: 'succeeded',
      stage: 'checking',
      artifact: ARTIFACT_TODO,
      createdAt: '2026-11-18T09:31:00Z',
      parentId: null,
      durations: { planning: 0.6, coding: 5.2, checking: 0.9 },
    },
    {
      id: 'demo-failed',
      prompt: '做一个极简记账面板，记录收支并显示结余',
      status: 'failed',
      stage: 'coding',
      artifact: null,
      error: '生成超时（600s）：引擎未在限定时间内返回产物，已保留半成品目录',
      createdAt: '2026-11-18T09:58:00Z',
      parentId: null,
      durations: { planning: 0.7, coding: 10.0, checking: null },
    },
  ];

  const state = {
    tasks: DEMO_TASKS.slice(),
    activeId: null,
    width: '100',
    busy: false,
    timers: [],
  };

  // /health 探测结果：决定顶栏状态灯与「引擎」chip 文案
  const backend = {
    online: false,
    step: 's2',
    engine: 'none',
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
    stageList: document.getElementById('stage-list'),
    stageHint: document.getElementById('stage-hint'),
    taskList: document.getElementById('task-list'),
    taskHint: document.getElementById('task-hint'),
    frame: document.getElementById('preview-frame'),
    canvas: document.getElementById('preview-canvas'),
    empty: document.getElementById('empty-state'),
    errorBanner: document.getElementById('error-banner'),
    errorText: document.getElementById('error-text'),
    retry: document.getElementById('retry-btn'),
    refresh: document.getElementById('refresh-btn'),
    popout: document.getElementById('popout-btn'),
    widthGroup: document.getElementById('width-group'),
    meta: document.getElementById('preview-meta'),
    progress: document.getElementById('progress-bar'),
  };

  function fmtTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '--:--';
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return hh + ':' + mm;
  }

  function activeTask() {
    return state.tasks.find(function (t) {
      return t.id === state.activeId;
    });
  }

  function statusLabel(task) {
    if (task.status === 'succeeded') return '已生成';
    if (task.status === 'failed') return '失败';
    if (task.status === 'running') return '生成中';
    return '排队中';
  }

  function renderTasks() {
    el.taskList.innerHTML = '';
    el.taskCount.textContent = String(state.tasks.length);

    state.tasks.forEach(function (task) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'task' + (task.id === state.activeId ? ' is-active' : '');
      btn.dataset.taskId = task.id;
      btn.setAttribute('aria-pressed', task.id === state.activeId ? 'true' : 'false');

      const prompt = document.createElement('span');
      prompt.className = 'task__prompt';
      prompt.textContent = task.prompt;

      const meta = document.createElement('span');
      meta.className = 'task__meta';

      const st = document.createElement('span');
      st.className = 'task__state';
      st.dataset.state = task.status;
      st.textContent = statusLabel(task);

      const time = document.createElement('span');
      time.textContent = fmtTime(task.createdAt);

      const id = document.createElement('span');
      id.textContent = task.id.slice(-8);

      meta.appendChild(st);
      meta.appendChild(time);
      meta.appendChild(id);

      btn.appendChild(prompt);
      btn.appendChild(meta);
      btn.addEventListener('click', function () {
        selectTask(task.id);
      });

      li.appendChild(btn);
      el.taskList.appendChild(li);
    });
  }

  function renderStages() {
    const task = activeTask();
    const nodes = el.stageList.querySelectorAll('.stage');

    nodes.forEach(function (node) {
      const key = node.dataset.stage;
      node.classList.remove('is-active', 'is-done', 'is-failed');
      const timeNode = node.querySelector('.stage__time');
      timeNode.textContent = '';

      if (!task) return;

      const idx = STAGES.indexOf(key);
      const curIdx = task.stage ? STAGES.indexOf(task.stage) : -1;
      const durations = task.durations || {};
      const value = durations[key];
      timeNode.textContent = value == null ? '' : value.toFixed(1) + 's';

      if (task.status === 'succeeded') {
        node.classList.add('is-done');
        return;
      }

      if (task.status === 'failed') {
        if (idx < curIdx) node.classList.add('is-done');
        if (idx === curIdx) node.classList.add('is-failed');
        return;
      }

      if (task.status === 'running' || task.status === 'pending') {
        if (idx < curIdx) node.classList.add('is-done');
        if (idx === curIdx) node.classList.add('is-active');
      }
    });

    if (!task) {
      el.stageHint.textContent = '等待提交';
    } else if (task.status === 'succeeded') {
      el.stageHint.textContent = '全部阶段完成';
    } else if (task.status === 'failed') {
      el.stageHint.textContent = '生成失败';
    } else if (task.status === 'running') {
      el.stageHint.textContent = STAGE_LABEL[task.stage] + ' · 进行中';
    } else {
      el.stageHint.textContent = '排队中';
    }
  }

  function renderPreview(animate) {
    const task = activeTask();

    if (!task) {
      el.frame.removeAttribute('srcdoc');
      el.empty.hidden = false;
      el.errorBanner.hidden = true;
      el.meta.textContent = '未选择任务';
      return;
    }

    if (task.status === 'failed') {
      el.frame.removeAttribute('srcdoc');
      el.empty.hidden = false;
      el.errorBanner.hidden = false;
      el.errorText.textContent = task.error || '生成失败';
      el.meta.textContent = task.id + ' · 失败';
      return;
    }

    el.errorBanner.hidden = true;

    if (task.status !== 'succeeded' || !task.artifact) {
      el.frame.removeAttribute('srcdoc');
      el.empty.hidden = false;
      el.meta.textContent = task.id + ' · ' + statusLabel(task);
      return;
    }

    el.empty.hidden = true;
    el.frame.srcdoc = task.artifact;
    el.meta.textContent = task.id + ' · ' + fmtTime(task.createdAt);

    if (animate) {
      el.progress.classList.remove('is-running');
      void el.progress.offsetWidth;
      el.progress.classList.add('is-running');
    }
  }

  function selectTask(id) {
    if (state.activeId === id) return;
    state.activeId = id;
    renderTasks();
    renderStages();
    renderPreview(true);
  }

  // 顶栏：状态灯 + 版本/引擎 chip，统一由 /health 结果驱动
  function updateStatus() {
    if (el.statusWrap) {
      el.statusWrap.dataset.online = backend.online ? 'true' : 'false';
    }

    const step = (backend.step || 's2').toUpperCase();
    if (backend.online) {
      el.statusText.textContent = state.busy ? '模拟生成中 · ' + step : 'FastAPI 已接管 · ' + step;
    } else {
      el.statusText.textContent = state.busy ? '离线演示 · 模拟生成中' : '离线演示 · ' + step;
    }

    if (el.engineChip) {
      el.engineChip.textContent =
        backend.engine && backend.engine !== 'none'
          ? '引擎 ' + String(backend.engine).toUpperCase()
          : '引擎 未接入';
    }

    if (el.version && backend.step) {
      el.version.textContent = backend.step;
    }
  }

  // 探测 /health：证明页面确实由 FastAPI 提供（S1 的 http.server 会 404）
  function probeHealth() {
    if (typeof window.fetch !== 'function') {
      updateStatus();
      return;
    }

    const controller = typeof window.AbortController === 'function' ? new AbortController() : null;
    const timer = window.setTimeout(function () {
      if (controller) controller.abort();
    }, 2500);

    fetch('/health', {
      cache: 'no-store',
      signal: controller ? controller.signal : undefined,
    })
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
      .then(function () {
        window.clearTimeout(timer);
        updateStatus();
      });
  }

  function setBusy(busy) {
    state.busy = busy;
    el.submit.disabled = busy;
    el.submit.querySelector('.primary-btn__label').textContent = busy ? '生成中…' : '生成应用';
    updateStatus();
  }

  function schedule(fn, delay) {
    const handle = window.setTimeout(fn, delay);
    state.timers.push(handle);
    return handle;
  }

  function createTask(prompt) {
    const now = new Date().toISOString();
    const task = {
      id: 'sim-' + Math.random().toString(36).slice(2, 10),
      prompt: prompt,
      status: 'pending',
      stage: null,
      artifact: null,
      createdAt: now,
      parentId: state.activeId || null,
      durations: {},
    };
    state.tasks.unshift(task);
    state.activeId = task.id;
    renderTasks();
    renderStages();
    renderPreview(false);
    return task;
  }

  function advance(task, stage, ms) {
    task.durations[stage] = ms / 1000;
    task.status = 'running';
    task.stage = stage;
    if (state.activeId === task.id) {
      renderTasks();
      renderStages();
    }
  }

  function simulateGeneration(prompt) {
    if (state.busy) return;
    setBusy(true);

    const task = createTask(prompt);
    let seq = 0;
    const plan = [
      [700, 'planning'],
      [1200, 'coding'],
      [900, 'checking'],
      [600, null],
    ];

    plan.forEach(function (step) {
      seq += step[0];
      schedule(function () {
        if (step[1]) {
          advance(task, step[1], step[0]);
          return;
        }
        task.status = 'succeeded';
        task.stage = 'checking';
        task.artifact = buildGeneratedApp(prompt);
        setBusy(false);
        if (state.activeId === task.id) {
          renderTasks();
          renderStages();
          renderPreview(true);
        }
      }, seq);
    });
  }

  function retry() {
    const task = activeTask();
    if (!task) return;
    el.input.value = task.prompt;
    simulateGeneration(task.prompt);
  }

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
      simulateGeneration(value);
    });

    el.input.addEventListener('keydown', function (event) {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        el.submit.click();
      }
    });

    el.widthGroup.addEventListener('click', function (event) {
      const btn = event.target.closest('[data-width]');
      if (!btn) return;
      state.width = btn.dataset.width;
      el.canvas.dataset.width = state.width;
      Array.prototype.forEach.call(el.widthGroup.querySelectorAll('.width-btn'), function (node) {
        node.classList.toggle('is-active', node.dataset.width === state.width);
      });
    });

    el.refresh.addEventListener('click', function () {
      renderPreview(true);
    });

    el.popout.addEventListener('click', function () {
      const task = activeTask();
      if (!task || !task.artifact) return;
      const blob = new Blob([task.artifact], { type: 'text/html' });
      window.open(URL.createObjectURL(blob), '_blank', 'noopener');
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

  function init() {
    bind();
    updateStatus();
    probeHealth();
    state.activeId = DEMO_TASKS[0].id;
    renderTasks();
    renderStages();
    renderPreview(false);
    el.taskHint.textContent = '共 ' + state.tasks.length + ' 条演示数据';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
