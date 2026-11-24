import React, { useState, useEffect, useRef, useReducer } from 'react';
import { createRoot } from 'react-dom/client';

const N = 64;
const POLL_OPTIONS = [1000, 2000, 5000];
const TEMP_MODES = [['auto','Auto','CPU package / Tctl-Tdie'],['hottest','Hottest','Hottest CPU sensor'],['average','Average','Mean of core sensors']];
const TEMP_KIND = {package:'PKG',hottest:'MAX',core:'CORE',average:'AVG',socket:'BOARD',acpi:'ACPI',psutil:'SYS'};
const GRAPH_COLORS = Object.freeze({
  cpu: '#C5A0FF',           // soft lavender — CPU utilization
  temp: '#FFBA99',          // warm peach — CPU temperature
  ram: '#81BDFF',           // arctic blue — memory
  gpu: '#BE9EFF',           // violet — graphics
  power: '#F3CF85',         // champagne — package power
  io: '#80E6B7',            // mint — storage activity
  net: '#75BBFF',           // sky blue — network download
  fan: '#7DE0DD',           // aqua — cooling fans
});
const THEME_OPTIONS = [
  {id:'onyx', group:'Dark', mode:'dark', title:'Midnight Blue', subtitle:'Blue · Dark'},
  {id:'amoled', group:'Dark', mode:'dark', title:'AMOLED Black', subtitle:'Black · Dark'},
  {id:'forest', group:'Dark', mode:'dark', title:'Forest', subtitle:'Green · Dark'},
  {id:'steel', group:'Dark', mode:'dark', title:'Titanium Teal', subtitle:'Teal · Dark'},
  {id:'glass', group:'Dark', mode:'dark', title:'Prism Glass', subtitle:'Violet · Translucent', glass:true},
  {id:'crimson', group:'Dark', mode:'dark', title:'Crimson Noir', subtitle:'Red · Translucent', glass:true},
  {id:'daylight', group:'Light', mode:'light', title:'Daylight', subtitle:'Blue · Light'},
  {id:'latte', group:'Light', mode:'light', title:'Latte', subtitle:'Warm · Light'},
  {id:'tokyo', group:'Artistic', mode:'dark', title:'Neo Tokyo', subtitle:'Blue · Translucent', glass:true},
  {id:'starlight', group:'Artistic', mode:'dark', title:'Starlight', subtitle:'Pink · Translucent', glass:true},
  {id:'sakura', group:'Artistic', mode:'light', title:'Sakura', subtitle:'Pastel · Translucent', glass:true},
];
const THEME_GROUPS = ['Dark', 'Light', 'Artistic'];
const themeMode = id => (THEME_OPTIONS.find(t => t.id === id) || THEME_OPTIONS[0]).mode;

function readWall() { try { return localStorage.getItem('pulse.wall') || ''; } catch { return ''; } }
function saveWall(url) { try { if (url) localStorage.setItem('pulse.wall', url); else localStorage.removeItem('pulse.wall'); return true; } catch { return false; } }
// Downscale the picked image so it fits comfortably in WebView storage.
function loadWallpaper(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(new Error('Could not read the file.'));
    fr.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('That file is not a usable image.'));
      img.onload = () => {
        const k = Math.min(1, 1920 / Math.max(img.width, img.height)), cv = document.createElement('canvas');
        cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
        resolve(cv.toDataURL('image/jpeg', 0.82));
      };
      img.src = fr.result;
    };
    fr.readAsDataURL(file);
  });
}
function readPrefs() {
  try { const v = JSON.parse(localStorage.getItem("pulse.prefs") || "{}"); return { interval: POLL_OPTIONS.includes(v.interval) ? v.interval : 1000, motion: v.motion !== false, theme: THEME_OPTIONS.some(t=>t.id===v.theme)?v.theme:'onyx', tempMode: TEMP_MODES.some(m=>m[0]===v.tempMode)?v.tempMode:'auto' }; }
  catch { return { interval: 1000, motion: true, theme:'onyx', tempMode:'auto' }; }
}
const TESTS = [
  ['info', 'System overview', 'OS, CPU, volumes and free space'],
  ['battery', 'Battery health', 'Capacity, cycles and condition'],
  ['ram', 'Memory', 'Speed, bandwidth and integrity'],
  ['disk', 'SSD / storage', 'SMART, sequential and random I/O'],
  ['cpu', 'CPU thermals', 'Controlled load and throttling'],
  ['gpu', 'Graphics', 'GPU model, driver and sensors'],
  ['network', 'Connectivity', 'Link, latency and DNS'],
  ['processes', 'Processes', 'Top CPU and memory consumers'],
];
const tempColor = t => t == null ? GRAPH_COLORS.temp : t >= 85 ? '#F87171' : t >= 70 ? '#FBBF24' : '#FFA876';
const scoreColor = v => v == null ? '#7b8494' : v >= 80 ? '#34D399' : v >= 60 ? '#FBBF24' : '#F87171';
const fmtKB = v => v >= 1024 ? (v / 1024).toFixed(1) + ' MB/s' : v.toFixed(0) + ' KB/s';
const number = (v, digits=0) => v == null || !Number.isFinite(v) ? '—' : Number(v).toFixed(digits);
const iconPaths = {
  layout:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  activity:'M3 12h4l3-8 4 16 3-8h4',
  chip:'M5 5h14v14H5z M9 9h6v6H9z M9 2v3 M15 2v3 M9 19v3 M15 19v3 M2 9h3 M2 15h3 M19 9h3 M19 15h3',
  thermometer:'M14 14.76V5a3 3 0 0 0-6 0v9.76a5 5 0 1 0 6 0z M11 10v8',
  database:'M3 6c0-2 18-2 18 0s-18 2-18 0 M3 6v12c0 3 18 3 18 0V6 M3 12c0 3 18 3 18 0',
  fan:'M12 12c1.6-2.6 2.8-6.4.5-8A3 3 0 0 0 8 6c.7 2.4 2.3 4 4 6z M12 12c3 .2 6.7 1.1 7.4 3.8a3 3 0 0 1-4.8 3.4c-1.7-1.7-2.4-4-2.6-7.2z M12 12c-1.5 2.6-4.6 5.2-7 4.1a3 3 0 0 1 1-5.8c2.4-.7 4.5 0 6 1.7z M12 10.5v3',
  zap:'M13 2 4 13h7l-1 9 10-12h-7l0-8z',
  screen:'M3 4h18v14H3z M8 22h8 M12 18v4',
  check:'M20 6 9 17l-5-5',
  chevron:'M9 18l6-6-6-6',
  arrow:'M7 17 17 7 M9 7h8v8',
  info:'M12 17v-5 M12 8h.01 M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20',
  search:'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16 M17 17l5 5',
  layers:'M12 3 2 9l10 6 10-6z M2 14l10 6 10-6',
  refresh:'M20 11a8 8 0 1 0-2 6 M20 4v7h-7',
  download:'M12 3v13 M7 11l5 5 5-5 M4 19h16',
  shield:'M12 2 4 5v7c0 5 4 8 8 10 4-2 8-5 8-10V5z M9 12l2 2 4-4',
  harddrive:'M5 6h14l3 10v3H2v-3z M6 17h.01 M10 17h.01',
  wifi:'M2 8c5-5 15-5 20 0 M5 12c4-4 10-4 14 0 M8 16c2-2 6-2 8 0 M12 20h.01',
  clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20 M12 6v6l4 2',
  settings:'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M12 2v2 M12 20v2 M4.93 4.93l1.41 1.41 M17.66 17.66l1.41 1.41 M2 12h2 M20 12h2 M4.93 19.07l1.41-1.41 M17.66 6.34l1.41-1.41',
  lock:'M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4',
  x:'M18 6 6 18 M6 6l12 12',
  minus:'M5 12h14',
  tool:'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z',
  power:'M12 2v10 M18.4 6.6a9 9 0 1 1-12.77.04',
  trash:'M3 6h18 M19 6l-1 14H6L5 6 M8 6V4h8v2 M10 11v6 M14 11v6',
  play:'M6 4l14 8-14 8z',
  list:'M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01',
  sliders:'M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6',
  rocket:'M5 15c-1.5 1.3-2 5-2 5s3.7-.5 5-2 M12 15l-3-3a22 22 0 0 1 2-4 12.9 12.9 0 0 1 11-6c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2z M9 12H4s.5-3 2-4c1.6-1.1 5 0 5 0 M12 15v5s3-.5 4-2c1.1-1.6 0-5 0-5',
};
function Icon({ name, size=18, className='' }) {
  return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={iconPaths[name] || iconPaths.layout} /></svg>;
}
function useApi() {
  const [api, setApi] = useState(window.pywebview?.api?.info ? window.pywebview.api : null);
  useEffect(() => {
    const ready = () => setApi(window.pywebview.api);
    window.addEventListener('pywebviewready', ready);
    return () => window.removeEventListener('pywebviewready', ready);
  }, []);
  return api;
}
function useInterval(fn, ms, on=true) {
  const cb = useRef(fn); cb.current = fn;
  useEffect(() => { if (!on) return; const id = setInterval(() => cb.current(), ms); return () => clearInterval(id); }, [ms, on]);
}
/* In-app confirmation dialog (replaces the browser's "127.0.0.1 says" popup). */
let confirmHandler = null;
function askConfirm(message, opts = {}) {
  return new Promise(resolve => {
    if (!confirmHandler) return resolve(window.confirm(String(message)));
    confirmHandler({ message: String(message), ...opts, resolve });
  });
}
function ConfirmHost() {
  const [q, setQ] = useState(null);
  const cur = useRef(null), okRef = useRef(null), cancelRef = useRef(null);
  const done = v => { const o = cur.current; if (!o) return; cur.current = null; setQ(null); o.resolve(v); };
  useEffect(() => {
    confirmHandler = o => { if (cur.current) cur.current.resolve(false); cur.current = o; setQ(o); };
    return () => { confirmHandler = null; };
  }, []);
  useEffect(() => {
    if (!q) return;
    const key = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); done(false); }
      else if (e.key === 'Enter' && document.activeElement !== cancelRef.current) { e.preventDefault(); e.stopImmediatePropagation(); done(true); }
    };
    window.addEventListener('keydown', key, true);
    const t = setTimeout(() => { const el = q.danger ? cancelRef.current : okRef.current; if (el) el.focus(); }, 0);
    return () => { window.removeEventListener('keydown', key, true); clearTimeout(t); };
  }, [q]);
  if (!q) return null;
  const parts = q.message.split(/\n\n+/);
  const title = q.title || parts[0], paras = q.title ? parts : parts.slice(1);
  return <div className="settings-backdrop confirm-backdrop" onMouseDown={() => done(false)}>
    <section role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" className="settings-dialog confirm-dialog" onMouseDown={e => e.stopPropagation()}>
      <h3 id="confirm-title">{title}</h3>
      {q.code && <code className="confirm-code">{q.code}</code>}
      {paras.map((p, i) => <p key={i}>{p}</p>)}
      <div className="confirm-actions">
        <button ref={cancelRef} className="btn secondary" onClick={() => done(false)}>{q.cancel || 'Cancel'}</button>
        <button ref={okRef} className={'btn pri' + (q.danger ? ' danger' : '')} onClick={() => done(true)}>{q.ok || 'OK'}</button>
      </div>
    </section>
  </div>;
}
function Spark({ data, max, color='#f5f5f5' }) {
  const ref = useRef(null);
  useEffect(() => {
    const cv = ref.current, w = cv?.clientWidth, h = cv?.clientHeight, r = window.devicePixelRatio || 1;
    if (!cv || !w || !h) return;
    cv.width = Math.round(w * r); cv.height = Math.round(h * r);
    const c = cv.getContext('2d');
    c.setTransform(r, 0, 0, r, 0, 0);
    c.clearRect(0, 0, w, h);
    c.strokeStyle = getComputedStyle(cv).getPropertyValue('--chart-grid').trim() || 'rgba(128,128,128,.14)'; c.lineWidth=1;
    for (let i=1;i<3;i++) { const y=h*i/3; c.beginPath(); c.moveTo(0,y); c.lineTo(w,y); c.stroke(); }
    if (data.length < 2 || data.every(v => !Number.isFinite(v))) return;
    const values = data.filter(v => Number.isFinite(v));
    const peak = max || Math.max(1, ...values) * 1.25;
    const X = i => w - (data.length-1-i) * w/Math.max(1, data.length-1);
    const Y = v => h-3 - Math.min(peak,Math.max(0,v)) / peak*(h-7);
    // Each measured segment is isolated so missing readings never make fake lines.
    const segments=[]; let segment=[];
    data.forEach((v,i) => {
      if (Number.isFinite(v)) segment.push([X(i), Y(v)]);
      else if (segment.length) { segments.push(segment); segment=[]; }
    });
    if (segment.length) segments.push(segment);
    const fill = c.createLinearGradient(0,0,0,h);
    fill.addColorStop(0,color+'26'); fill.addColorStop(1,color+'00');
    segments.forEach(points => {
      if (points.length < 2) return;
      c.beginPath(); c.moveTo(points[0][0],points[0][1]);
      for(let i=1;i<points.length;i++) c.lineTo(points[i][0],points[i][1]);
      c.lineTo(points[points.length-1][0],h); c.lineTo(points[0][0],h); c.closePath();
      c.fillStyle=fill; c.fill();
      c.beginPath(); c.moveTo(points[0][0],points[0][1]);
      for(let i=1;i<points.length;i++) c.lineTo(points[i][0],points[i][1]);
      c.strokeStyle=color; c.lineWidth=1.85; c.lineJoin='round'; c.lineCap='round';
      c.stroke();
    });
  }, [data, max, color]);
  return <canvas className="sp" ref={ref} />;
}
function Metric({ name, icon, value, unit, sub, points, max, color, extra, tip }) {
  return <div className="metric card" style={{'--mc': color || '#9aa4b2'}} title={tip || undefined}>
    <div className="metric-head"><span className="metric-icon"><Icon name={icon}/></span><span className="metric-name">{name}</span>{extra && <span className="metric-extra">{extra}</span>}</div>
    <div className="metric-value">{value}<span>{unit || ''}</span></div>
    <div className="metric-sub">{sub || 'Live telemetry'}</div>
    <Spark data={points || []} max={max} color={color || '#c0c5ca'}/>
  </div>;
}
function DriverInstall({ api, pawnio }) {
  const [busy,setBusy] = useState(false);
  const [result,setResult] = useState(null);
  if (pawnio?.installed !== false) return null;
  const install = async () => {
    if (!api || busy) return;
    const confirmText = 'Install official PawnIO 2.2.0 hardware-access driver?\n\n'
      + 'This is a Windows kernel driver required for many AMD Ryzen temperature/power sensors. '
      + 'It changes the computer system, requires Administrator permission, and is not a separate monitoring app. '
      + 'Pulse will use a SHA-256 verified official installer. You can uninstall PawnIO through Windows Settings.\n\n'
      + 'Install only if you trust the publisher and are allowed to install drivers on this PC.';
    if (!(await askConfirm(confirmText, { ok: 'Install driver' }))) return;
    setBusy(true); setResult(null);
    try {
      const response = await api.install_hardware_access();
      setResult({ok:!!response.ok, text: response.message || response.error || 'Installation ended.'});
    } catch(e) { setResult({ok:false,text:String(e)}); }
    finally { setBusy(false); }
  };
  return <div className="driver-banner">
    <div className="driver-symbol"><Icon name="chip" size={20}/></div>
    <div className="driver-copy"><strong>AMD Ryzen sensor access</strong>
      <p>CPU die temperature and package power need hardware register access. Official PawnIO 2.2.0 is not installed.</p>
      <small>One-time optional Windows driver installation. Consent required; restart Pulse afterwards. Readings still depend on board/firmware support.</small>
      {result && <p className={'driver-result '+(result.ok?'ok':'error')}>{result.text}</p>}
    </div>
    <button className="btn driver-btn" disabled={!api || busy} onClick={install}>
      <Icon name="download" size={16}/>{busy ? 'Installing...' : 'Install hardware access'}
    </button>
  </div>;
}
function Dashboard({ api, info, active, refreshMs }) {
  const [d, setD] = useState(null);
  const [hist, setHist] = useState({ cpu:[], temp:[], ram:[], gpu:[], power:[], io:[], net:[], fan:[] });
  const [updated,setUpdated] = useState('');
  const inFlight=useRef(false), mounted=useRef(true);
  useEffect(()=>{ mounted.current=true; return ()=>{ mounted.current=false; }; },[]);
  const tick = async () => {
    if (!api || inFlight.current) return;
    inFlight.current=true;
    try {
      const x = await api.live(); if (!mounted.current) return; setD(x); setUpdated(new Date().toLocaleTimeString());
      setHist(p => {
        const maxSamples=Math.max(13,Math.ceil(60000/refreshMs)+1);
        const add=(key,v) => [...p[key], Number.isFinite(v) ? v : null].slice(-maxSamples);
        return {cpu:add('cpu',x.cpu),temp:add('temp',x.temp),ram:add('ram',x.ram),
          gpu:add('gpu',x.gpu_load),power:add('power',x.cpu_power),io:add('io',x.disk_r+x.disk_w),net:add('net',x.net_d+x.net_u),fan:add('fan',x.fan_rpm)};
      });
    } catch(e) { if (mounted.current) { setD(null); setUpdated('Disconnected · retrying'); } }
    finally { inFlight.current=false; }
  };
  useEffect(() => { if (active) tick(); }, [api, active, refreshMs]);
  useInterval(tick, refreshMs, !!api && active);
  const v=(a,unit='') => a==null ? 'Not available' : number(a)+unit;
  return <div className="dashboard">
    <div className="heading"><div><h1>Overview</h1></div><span className={'live-indicator'+(!d?' pending':'')}><i/> {d?'Live':'Connecting'} <small>{updated || ''}</small></span></div>
    <div className="hero card">
      <div className="hero-info"><div className="hero-top"><span className="hero-os"><Icon name="screen" size={14}/>{info?.os || 'Windows'}</span></div>
      <h2>{info?.host || 'This PC'}</h2><p>{info?.cpu || 'Reading processor…'}</p>
      <div className="hero-chips"><span><Icon name="chip" size={14}/>{info?.cores || '—'}</span><span><Icon name="database" size={14}/>{info?.ram || '—'} memory</span><span><Icon name="clock" size={14}/>{info?.uptime || '—'} uptime</span></div></div>
      <div className="hero-status"><div className={'status-pill '+(!d?'wait':d?.sensor_ok ? (d?.sensor_warnings?.length ? 'warn' : 'ok') : 'bad')}><i/>{!d?'Waiting':d?.sensor_ok ? (d?.sensor_warnings?.length ? 'Partial sensors' : 'Sensors connected') : 'Limited sensors'}</div><div className="status-caption">{!d?'Awaiting hardware readings':d?.sensor_ok ? (d?.sensor_warnings?.length ? 'Some sensors not exposed' : 'Hardware monitoring active') : 'Sensor access limited'}<small>{d?.sensor_ok ? `${d.sensor_count} live sensors detected` : !d?'No live values reported yet':'Other live metrics remain available'}</small></div></div>
    </div>
    <DriverInstall api={api} pawnio={d?.pawnio}/>
    {!d?.sensor_ok && d?.sensor_error && <div className="notice"><Icon name="info" size={18}/><span><b>Embedded sensor access unavailable.</b> {d.sensor_error}. CPU, RAM and network usage still work.</span></div>}
    {!!d?.sensor_ok && !!d?.sensor_warnings?.length && <div className="sensor-alert">
      <Icon name="info" size={18}/><div><strong>Sensor coverage is partial</strong><p>{d.sensor_warnings.join(' ')}</p>
      <small>Open Hardware sensors to inspect raw readings and save a sensor report. Administrator mode cannot guarantee unsupported measurements.</small></div>
    </div>}
    <div className="section-row"><div><h2>Live metrics</h2></div><span className="section-meta">every {refreshMs / 1000} s</span></div>
    <div className="metric-grid">
      <Metric name="Processor" icon="chip" value={number(d?.cpu)} unit="%" sub={d?.freq ? `${number(d.freq)} MHz clock speed`:'Processor load'} points={hist.cpu} max={100} color={GRAPH_COLORS.cpu}/>
      <Metric name="CPU temperature" icon="thermometer" value={number(d?.temp)} unit={d?.temp==null?'':'°C'} sub={d?.temp==null?'CPU temperature unavailable':(d?.temp_src || 'CPU die sensor')} points={hist.temp} max={110} color={tempColor(d?.temp)} extra={d?.temp==null?undefined:(TEMP_KIND[d.temp_kind]||'CPU')} tip={d?.temp==null?undefined:`${d.temp_label||'CPU temperature'}\nHottest sensor: ${number(d.temp_hottest,1)}°C · Core average: ${number(d.temp_avg,1)}°C\nSame value is used by the CPU test and highlighted in Hardware Sensors.`}/>
      <Metric name="Memory" icon="database" value={number(d?.ram)} unit="%" sub={d? `${d.ram_used} / ${d.ram_total}`:'RAM allocation'} points={hist.ram} max={100} color={GRAPH_COLORS.ram}/>
      <Metric name="Graphics" icon="activity" value={number(d?.gpu_load)} unit={d?.gpu_load==null?'':'%'} sub={d?.gpu_temp==null?'No GPU temperature reading':`GPU temperature ${number(d.gpu_temp)}°C`} points={hist.gpu} max={100} color={GRAPH_COLORS.gpu}/>
      <Metric name="CPU power" icon="zap" value={number(d?.cpu_power,1)} unit={d?.cpu_power==null?'':' W'} sub={d?.cpu_power==null?'No valid package power sensor':(d?.cpu_power_src || 'LHM measured power')} points={hist.power} color={GRAPH_COLORS.power} extra="LHM"/>
      <Metric name="Storage" icon="harddrive" value={d ? number(d.disk_r+d.disk_w,1) : '—'} unit={d?' MB/s':''} sub={d ? `R ${number(d.disk_r,1)} · W ${number(d.disk_w,1)} MB/s` : 'Disk throughput'} points={hist.io} color={GRAPH_COLORS.io}/>
      <Metric name="Network" icon="wifi" value={d ? fmtKB(d.net_d) : '—'} sub={d ? `Upload ${fmtKB(d.net_u)}` : 'Network throughput'} points={hist.net} color={GRAPH_COLORS.net}/>
      <Metric name="Fans" icon="fan" value={number(d?.fan_rpm)} unit={d?.fan_rpm==null?'':' RPM'} sub={d?.fan_count ? `${d.fan_count} fan sensor${d.fan_count>1?'s':''} detected`:'Fan telemetry not available'} points={hist.fan} color={GRAPH_COLORS.fan} extra="LHM"/>
    </div>
    <div className="bottom-grid"><div className="card detail-card"><div className="card-title"><Icon name="layers"/> Hardware details</div><div className="detail-line"><span>Operating system</span><strong>{info?.os || '—'}</strong></div><div className="detail-line"><span>Processor</span><strong>{info?.cpu || '—'}</strong></div><div className="detail-line"><span>Physical / logical cores</span><strong>{info?.cores || '—'}</strong></div><div className="detail-line"><span>System memory</span><strong>{info?.ram || '—'}</strong></div></div>
    <div className="card detail-card"><div className="card-title"><Icon name="shield"/> Monitor status</div><div className="detail-line"><span>Telemetry source</span><strong>{d?.sensor_ok ? 'Embedded LibreHardwareMonitor' : 'System counters (fallback)'}</strong></div><div className="detail-line"><span>CPU temperature source</span><strong>{d?.temp==null?'—':`${d.temp_label||'CPU'} (${number(d.temp,1)}°C)`}</strong></div><div className="detail-line"><span>Hottest · average core</span><strong>{d?.temp==null?'—':`${number(d.temp_hottest,1)}°C · ${number(d.temp_avg,1)}°C`}</strong></div><div className="detail-line"><span>Detected sensors</span><strong>{d?.sensor_count ?? '—'}</strong></div><div className="detail-line"><span>Privilege level</span><strong>{info?.admin ? 'Administrator' : 'Standard user'}</strong></div><div className="detail-line"><span>PawnIO driver</span><strong>{d?.pawnio?.installed === true ? `Installed ${d.pawnio.version}` : d?.pawnio?.installed === false ? 'Not installed' : 'Checking...'}</strong></div><div className="detail-line"><span>Hardware interface</span><strong>pythonnet / .NET Framework</strong></div></div></div>
  </div>;
}
const SENSOR_FILTERS = ['All', 'cpu', 'gpu', 'memory', 'storage', 'motherboard', 'battery', 'network', 'other'];
function Sensors({ api, active, refreshMs }) {
  const [snap, setSnap] = useState(null), [search, setSearch] = useState(''), [group,setGroup] = useState('All');
  const [exportResult, setExportResult] = useState('');
  const exportDebug = async () => {
    if (!api) return;
    try {
      const result = await api.export_sensor_debug();
      setExportResult(result.ok ? `Saved: ${result.path}` : `Export failed: ${result.error}`);
    } catch(e) { setExportResult(`Export failed: ${e}`); }
  };
  const refresh=async () => { if (!api) return; try {setSnap(await api.sensors());} catch(e) {setSnap({ok:false,error:String(e),sensors:[]});} };
  useEffect(() => { if (active) refresh(); }, [api,active]);
  useInterval(refresh,Math.max(1500,refreshMs),!!api && active);
  const all=snap?.sensors || [];
  const rows=all.filter(s => (group==='All' || s.category===group) && [s.name,s.device,s.type,s.category].join(' ').toLowerCase().includes(search.toLowerCase()));
  return <div className="sensors-page">
    <div className="heading"><div><h1>Hardware sensors</h1><p className="sub">Live readings from LibreHardwareMonitor.</p></div><div className="sensor-actions"><button className="btn secondary" onClick={refresh}><Icon name="refresh" size={15}/> Refresh</button><button className="btn" disabled={!api} onClick={exportDebug}><Icon name="download" size={15}/> Save Sensor Report</button></div></div>
    <div className="sensor-head card"><div className={'sensor-icon '+(snap?.ok?'good':'')}><Icon name="activity" size={24}/></div><div><h2>{snap?.ok?'LibreHardwareMonitor connected':'Sensor backend unavailable'}</h2><p>{snap?.ok ? 'Real data from the local hardware sensor API' : (snap?.error || 'Initializing sensor library...')}</p></div><div className="sensor-total"><strong>{all.length}</strong><span>active sensors</span></div></div>
    <DriverInstall api={api} pawnio={snap?.pawnio}/>
    {snap?.cpu_temp!=null && <div className="card temp-source"><div className="temp-source-value"><Icon name="thermometer" size={20}/><strong>{number(snap.cpu_temp,1)}</strong><span>°C</span></div><div className="temp-source-copy"><b>CPU temperature used by Pulse</b><p>{snap.cpu_temp_label} · {snap.cpu_temp_src}</p><small>Hottest sensor {number(snap.cpu_temp_hottest,1)}°C · core average {number(snap.cpu_temp_average,1)}°C · mode {snap.cpu_temp_mode}. The highlighted row below is the one in use. Other monitoring tools may read a different sensor (package vs. per-core vs. motherboard socket) or sample at a different moment, so small differences are normal. Change the source in Settings.</small></div></div>}
    {!!snap?.warnings?.length && <div className="sensor-alert"><Icon name="info" size={18}/><div><strong>Some CPU readings are unavailable</strong><p>{snap.warnings.join(' ')}</p><small>Raw sensor values remain visible below. A zero CPU temperature is not a trustworthy reading.</small></div></div>}
    {!!exportResult && <div className="export-result"><Icon name="check" size={15}/>{exportResult}<button className="btn" onClick={()=>api.open_folder()}>Open reports</button></div>}
    <div className="sensor-toolbar"><div className="search"><Icon name="search" size={17}/><input placeholder="Find sensor, component, category..." value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Filter sensor category" value={group} onChange={e=>setGroup(e.target.value)}>{SENSOR_FILTERS.map(f=><option key={f} value={f}>{f==='All'?'All components':f[0].toUpperCase()+f.slice(1)}</option>)}</select></div>
    <div className="card sensor-table"><div className="sensor-table-head"><span>Device / sensor</span><span>Type</span><span>Current value</span></div>{rows.length ? rows.map((s,i)=><div className={'sensor-row'+(snap?.cpu_temp_sensor_id && s.id===snap.cpu_temp_sensor_id?' in-use':'')} key={`${s.id}-${i}`}><div><b>{s.name}{snap?.cpu_temp_sensor_id && s.id===snap.cpu_temp_sensor_id && <em className="in-use-tag">Used for CPU temp</em>}</b><small>{s.device} · {s.category}</small></div><span className="sensor-kind">{s.type}</span><div className={'sensor-value'+(s.issue?' invalid':'')}>{number(s.value, s.type==='Temperature'?1:2)} <span>{s.unit}</span>{s.issue && <small className="raw-issue">Raw / unreliable</small>}</div></div>) : <div className="empty">{snap?.ok?'No sensors match this filter.':'Sensors are not available. Run as administrator and check that the official DLLs were bundled.'}</div>}</div>
    <p className="disclaimer">Availability depends on firmware, hardware and system permissions. Missing readings are not estimated.</p>
  </div>;
}

const init = { running: false, sections: [], log: [], lastCR: false, stage: 'Ready for analysis', prog: 0, completed:0, total:0, current:null, done: null };
function reduce(s, ev) {
  switch (ev.t) {
    case 'reset': return { ...init, running: true, stage: 'Starting…' };
    case 'stop': return { ...s, running: false };
    case 'section': return { ...s, sections: [...s.sections, { title: ev.title, items: [] }] };
    case 'item': {
      const secs = s.sections.slice(), last = secs[secs.length - 1];
      if (!last) return s;
      secs[secs.length - 1] = { ...last, items: [...last.items, ev] };
      return { ...s, sections: secs };
    }
    case 'log': {
      const log = s.log.slice();
      if (s.lastCR && log.length) log[log.length - 1] = ev.text; else log.push(ev.text);
      return { ...s, log: log.slice(-400), lastCR: ev.cr };
    }
    case 'stage': {
      const t = TESTS.find(x => x[0] === ev.name);
      return { ...s, stage: `Running ${t ? t[1] : ev.name}  (${ev.i + 1} / ${ev.total})`, current:ev.name, total:ev.total };
    }
    case 'test_end': return { ...s, prog:(100 * ev.completed / ev.total), completed:ev.completed };
    case 'done': return { ...s, done: ev, running:false, current:null,
      prog:ev.assessment?.completion ?? s.prog, stage: `${ev.cancelled ? 'Stopped' : 'Completed'} in ${ev.secs} s` };
    default: return s;
  }
}

// GPU load generator: WebGL fragment shader in a Worker (OffscreenCanvas), duty-cycled to the target %.
const GPU_WORKER = `
let level=0, gl, prog, timer, running=false;
function init(){
  const c=new OffscreenCanvas(1024,1024); gl=c.getContext('webgl2')||c.getContext('webgl'); if(!gl) return false;
  const vs='attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
  const fs='precision highp float;uniform float t;void main(){float a=gl_FragCoord.x*.001+t,b=gl_FragCoord.y*.001;for(int i=0;i<900;i++){a=sin(a*1.01+b)*cos(b-a*.5)+a*.999;b=cos(a+b*1.01)*sin(a*b+.1)+b*.999;}gl_FragColor=vec4(fract(a),fract(b),a*b,1.);}';
  const mk=(ty,src)=>{const s=gl.createShader(ty);gl.shaderSource(s,src);gl.compileShader(s);return s;};
  prog=gl.createProgram();gl.attachShader(prog,mk(gl.VERTEX_SHADER,vs));gl.attachShader(prog,mk(gl.FRAGMENT_SHADER,fs));gl.linkProgram(prog);gl.useProgram(prog);
  const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
  const l=gl.getAttribLocation(prog,'p');gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,2,gl.FLOAT,false,0,0);
  gl.viewport(0,0,1024,1024);return true;
}
const px=new Uint8Array(4);
function tick(){
  if(!running) return;
  const W=100, busy=W*level/100, s0=performance.now();
  do{ gl.uniform1f(gl.getUniformLocation(prog,'t'),s0/1000); gl.drawArrays(gl.TRIANGLES,0,3); gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,px); }
  while(performance.now()-s0<busy);
  const rest=W-(performance.now()-s0);
  timer=setTimeout(tick, rest>0?rest:0);
}
onmessage=e=>{ level=e.data; clearTimeout(timer);
  if(level>0){ if(!gl && !init()){ postMessage('nogl'); return; } if(!running){running=true;} tick(); } else running=false; };
`;
let gpuWorker = null;
function gpuBurn(level) {
  try {
    if (level > 0) {
      if (!gpuWorker) gpuWorker = new Worker(URL.createObjectURL(new Blob([GPU_WORKER], { type: 'text/javascript' })));
      gpuWorker.postMessage(level);
    } else if (gpuWorker) { gpuWorker.postMessage(0); gpuWorker.terminate(); gpuWorker = null; }
  } catch (e) { /* WebGL/Worker unavailable: backend reports the load as not reached */ }
}

function Diagnostics({ api, toast }) {
  const [sel, setSel] = useState(() => new Set(TESTS.map(t => t[0])));
  const [o, setO] = useState({ quick: true, duration: 25, load: 25, gpu_load: 25, disk_size: 256, disk_path: '' });
  const [cancelling, setCancelling] = useState(false);
  const [s, dispatch] = useReducer(reduce, init);
  const [showLog, setShowLog] = useState(false);
  const logRef = useRef(null);
  const set = (k, v) => setO(p => ({ ...p, [k]: v }));
  const toggle = id => setSel(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  useEffect(() => { if (showLog && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [s.log.length, showLog]);
  useInterval(async () => {
    if (!api) return;
    try {
      const r = await api.poll();
      (r.events || []).forEach(ev => { if (ev.t === 'gpu_burn') gpuBurn(ev.on ? ev.level : 0); dispatch(ev); });
      if (!r.running) gpuBurn(0);
      if (!r.running && !(r.events || []).some(x=>x.t==='done')) { gpuBurn(0); dispatch({t:'stop'}); }
    } catch(e) { toast('Diagnostics connection issue: ' + String(e)); }
  }, 350, !!api && s.running);
  async function run(tests = [...sel]) {
    if (!api) return toast('Backend not ready yet');
    if (!tests.length) return toast('Select at least one test');
    setCancelling(false); dispatch({ t: 'reset' });
    try {
      const r = await api.start({ tests, ...o, disk_path: o.disk_path.trim() || null });
      if (!r.ok) { dispatch({t:'stop'}); toast(r.error || 'Unable to start tests'); }
    } catch(e) { dispatch({t:'stop'}); toast(String(e)); }
  }
  const runAll = () => { const all = TESTS.map(t=>t[0]); setSel(new Set(all)); run(all); };
  const save = async () => {
    if (!api) return;
    try { const r = await api.save_report(); toast(r.ok ? 'Saved report to ' + r.dir : r.error); }
    catch (e) { toast('Could not save report: '+e); }
  };
  const browse = async () => { if (!api) return; const p = await api.pick_folder(); if (p) set('disk_path', p); };
  const c = {PASS:0, WARN:0, FAIL:0, SKIP:0, INFO:0};
  s.sections.forEach(x => x.items.forEach(i => { if (i.status in c) c[i.status]++; }));
  const assessment=s.done?.assessment;
  const active=s.running;
  const progress=Math.max(0,Math.min(100,Math.round(s.prog)));
  const selectedTotal=sel.size;
  return <div className="diagnostics-v3">
    <div className="heading diag-heading"><div><h1>Diagnostics</h1><p className="sub">Real hardware checks, no simulated results.</p></div><div className="diag-actions-head"><span className="diag-privacy"><Icon name="lock" size={14}/> On-device analysis</span><button className="btn" disabled={active} onClick={runAll}><Icon name="check" size={15}/> Run all 8 tests</button></div></div>
    <div className="diagnostics-hero">
      <section className="diag-intro card"><div className="diag-intro-core"><span className="diag-glyph"><Icon name="activity" size={26}/></span><div><h2>System health check</h2><p>Tests CPU thermals, memory, storage, graphics, network and processes, then scores the evidence out of 100.</p></div></div><div className="diag-spec-strip"><span><Icon name="shield" size={15}/> 8 test categories</span><span><Icon name="database" size={15}/> Measured results only</span><span><Icon name="download" size={15}/> JSON + HTML report</span></div></section>
      <section className="diag-score-hero card" aria-label="Diagnostic score"><div className="diag-score-head">Overall score <span>{assessment ? assessment.score_label : (active ? 'In progress' : 'Not yet tested')}</span></div>
      <div className="diag-score-dial" style={{'--score':(assessment?.score ?? 0)+'%','--score-color':scoreColor(assessment?.score)}}><div><strong>{assessment?.score ?? '—'}</strong><small>/ 100</small></div></div>
      <div className="diag-score-foot"><span>Evidence coverage <b>{assessment ? assessment.coverage+'%' : '—'}</b></span><span>Tests completed <b>{assessment ? assessment.completed+'/'+assessment.total : (active ? s.completed+'/'+s.total : '—')}</b></span></div></section>
    </div>
    <div className="diag-layout">
      <aside className="diag-sidebar">
        <section className="card diag-config"><div className="diag-panel-top"><div><div className="eyebrow">Configuration</div><h2>Test selection</h2></div><span className="diag-selected">{selectedTotal} / 8</span></div>
        <div className="diag-bulk"><button disabled={active} onClick={()=>setSel(new Set(TESTS.map(t=>t[0])))}>Select all</button><span>/</span><button disabled={active} onClick={()=>setSel(new Set())}>Clear selection</button></div>
        <div className="diag-test-list">{TESTS.map(([id, n, d],i) => (
           <label className={'diag-test-choice'+(sel.has(id)?' selected':'')} key={id}>
             <span className="diag-test-index">{String(i+1).padStart(2,'0')}</span>
             <span className="diag-test-copy"><strong>{n}</strong><small>{d}</small></span>
             <input aria-label={'Run '+n} type="checkbox" checked={sel.has(id)} disabled={active} onChange={()=>toggle(id)}/>
           </label>))}</div></section>
        <section className="card diag-config diag-options"><div className="diag-panel-top"><div><div className="eyebrow">Parameters</div><h2>Test controls</h2></div></div>
          <label className="diag-quick"><span><b>Quick scan</b><small>Shorter checks to reduce test duration</small></span><input type="checkbox" checked={o.quick} disabled={active} onChange={e=>set('quick',e.target.checked)}/></label>
          <div className="diag-option-row"><span>CPU test duration</span><b>{o.quick ? Math.min(20,o.duration) : o.duration}s</b></div>
          <input aria-label="CPU test duration" type="range" min="10" max="120" step="5" value={o.duration} disabled={active} onChange={e=>set('duration',+e.target.value)}/>
          <div className="diag-option-row"><span>CPU test load</span><b>{o.load}%</b></div>
          <input aria-label="CPU test load" type="range" min="5" max="100" step="5" value={o.load} disabled={active} onChange={e=>set('load',+e.target.value)}/>
          <div className="diag-option-row"><span>GPU test load</span><b>{o.gpu_load ? o.gpu_load+'%' : 'Off'}</b></div>
          <input aria-label="GPU test load" type="range" min="0" max="100" step="5" value={o.gpu_load} disabled={active} onChange={e=>set('gpu_load',+e.target.value)}/>
          <p className="diag-safety-note">Monitor temperatures during CPU and GPU testing. Stop if the system becomes unstable. The scan cannot guarantee hardware health.</p>
          <div className="diag-option-row"><span>Storage test size</span><b>{o.quick ? Math.min(o.disk_size,256) : o.disk_size} MB</b></div>
          <select aria-label="Disk test size" value={o.disk_size} disabled={active} onChange={e=>set('disk_size',+e.target.value)}>{[128,256,512,1024].map(v=><option key={v} value={v}>{v} MB</option>)}</select>
          <div className="diag-option-row"><span>Storage test folder</span></div>
          <div className="inline"><input type="text" placeholder="Default: home folder" value={o.disk_path} disabled={active} onChange={e=>set('disk_path',e.target.value)}/><button aria-label="Choose storage test folder" className="btn" disabled={active} onClick={browse}>…</button></div>
          <button className="btn pri diag-run" disabled={active||!api||!sel.size} onClick={()=>run()}><Icon name="activity" size={17}/> {active ? 'Diagnostics running…' : `Run ${selectedTotal} selected tests`}</button>
          {active&&<button className="btn diag-cancel" disabled={cancelling} onClick={()=>{api.cancel();setCancelling(true);toast('Will stop before next test');}}>{cancelling?'Stopping after current test…':'Stop after current test'}</button>}
        </section>
      </aside>
      <section className="diag-feed">
        <div className="card diag-progress-card"><div className="diag-progress-top"><div><div className="eyebrow">Live analysis</div><h2>{s.stage}</h2></div><span className="diag-progress-number">{progress}<small>% complete</small></span></div>
          <div className="diag-progress-rail" role="progressbar" aria-valuenow={progress} aria-valuemin="0" aria-valuemax="100" aria-label="Diagnostic completion"><i style={{width:progress+'%'}}/></div>
          <div className="diag-summary-counters"><span><b>{c.PASS}</b> Passed</span><span><b>{c.WARN}</b> Warnings</span><span><b>{c.FAIL}</b> Failures</span><span><b>{c.SKIP}</b> Skipped</span></div>
        </div>
        {assessment&&<section className="card diag-assessment-detail"><div className="diag-panel-top"><div><div className="eyebrow">Assessment report</div><h2>{s.done?.cancelled?'Partial results — scan stopped':s.done?.verdict}</h2></div><span className="diag-final-score">{assessment.score===null?'Not assessed':assessment.score+'/100'}</span></div>
           <p>Score is calculated only from measured PASS, WARN and FAIL checks. Unsupported or informational checks do not count as passes. {assessment.coverage<100?'Partial evidence: interpret this score with the coverage shown above.':''}</p>
           <div className="diag-score-categories">{assessment.categories.map(x=><div className="diag-score-category" key={x.id}><span>{x.title}</span><span className="diag-category-track"><i style={{width:(x.score??0)+'%'}}/></span><b>{x.score==null?'Not scored':x.score+'/100'}</b></div>)}</div>
           {s.done.problems.length>0&&<details className="diag-issues"><summary>{s.done.problems.length} findings need review</summary><ul>{s.done.problems.map((x,i)=><li key={i}><b>{x.section} / {x.name}</b> — {x.value} {x.note}</li>)}</ul></details>}
           <div className="diag-actions"><button className="btn" onClick={save}><Icon name="download" size={15}/> Save JSON + HTML report</button><button className="btn secondary" onClick={()=>api.open_folder()}><Icon name="arrow" size={15}/> Open reports folder</button></div>
        </section>}
        {!s.sections.length && <div className="card diag-empty-state"><div className="diag-empty-icon"><Icon name="shield" size={29}/></div><h3>No results yet</h3><p>Pick tests on the left, or choose <b>Run all 8 tests</b>.</p><div><span>01 Select</span><i/><span>02 Analyse</span><i/><span>03 Review</span></div></div>}
        {!!s.sections.length && <div className="diag-results-head"><h2>Detailed findings</h2><span>{s.sections.length} sections · {c.PASS+c.WARN+c.FAIL+c.INFO+c.SKIP} readings</span></div>}
        <div className="diag-sections">{s.sections.map((sec,i)=><details className="card diag-result-section" open key={i}><summary><span className="diag-sec-index">{String(i+1).padStart(2,'0')}</span><strong>{sec.title}</strong><small>{sec.items.length} readings</small><Icon name="chevron" size={15}/></summary><div className="diag-result-rows">{sec.items.map((it,j)=><div className="diag-result-row" key={j}><span className={'diag-state '+it.status}>{it.status}</span><span className="diag-result-label">{it.name}</span><span className="diag-result-value">{it.value}{it.note&&<small>{it.note}</small>}</span></div>)}</div></details>)}</div>
        <div className="card diag-console"><button className="diag-console-title" onClick={()=>setShowLog(v=>!v)} aria-expanded={showLog}><span><Icon name="activity" size={16}/> Console output <small>{s.log.length} lines</small></span><Icon name="chevron" size={16}/></button>{showLog&&<div className="diag-console-content" ref={logRef}>{s.log.length?s.log.map((l,i)=><div key={i}>{l}</div>):'No console output yet.'}</div>}</div>
      </section>
    </div>
  </div>;
}


/* ---------- Optimizer: explicit, logged, reversible fixes ---------- */
const OPT_PLANS = [['saver','Power saver','Longest battery'],['balanced','Balanced','Windows default'],['performance','High performance','Faster response'],['ultimate','Ultimate','Maximum speed']];
function Switch({ checked, disabled, onChange, label }) {
  return <label className={'toggle'+(disabled?' disabled':'')} aria-label={label}><input type="checkbox" checked={!!checked} disabled={disabled} onChange={e=>onChange(e.target.checked)}/><span/></label>;
}
function Optimizer({ api, toast, active }) {
  const [st, setSt] = useState(null), [busy, setBusy] = useState(''), [startup, setStartup] = useState(null), [removed, setRemoved] = useState([]), [showRemoved, setShowRemoved] = useState(false);
  const [job, setJob] = useState(null), [sizes, setSizes] = useState(null), [loaded, setLoaded] = useState(false), [net, setNet] = useState(null);
  const logRef = useRef(null);
  const loadNet = async () => { if (!api || !api.opt_net_state) return; try { setNet(await api.opt_net_state()); } catch (e) {} };
  const reload = async () => { if (!api) return; loadNet(); try { setSt(await api.opt_status()); } catch (e) { toast('Optimizer: ' + e); } };
  useInterval(loadNet, 15000, !!api && !!active && !net?.busy);
  const loadStartup = async () => { if (!api) return; try { setStartup(await api.opt_startup()); } catch (e) { setStartup({ok:false,items:[],error:String(e)}); } };
  const loadRemoved = async () => { if (!api || !api.opt_removed_startup) return; try { const r = await api.opt_removed_startup(); setRemoved(r.items || []); } catch (e) {} };
  useEffect(() => { if (active && api && !loaded) { setLoaded(true); reload(); loadStartup(); loadRemoved(); api.opt_cleanup_sizes().then(setSizes).catch(()=>{}); } }, [active, api, loaded]);
  useInterval(async () => {
    try {
      const j = await api.opt_poll(); setJob(j);
      if (!j.running && j.result) { toast(j.result.ok ? (j.result.message || 'Done') : (j.result.error || 'Failed')); reload(); api.opt_cleanup_sizes().then(setSizes).catch(()=>{}); }
    } catch (e) {}
  }, 700, !!api && !!job?.running);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [job?.lines?.length]);
  const supported = st?.supported !== false, admin = !!st?.admin;
  const toggle = async (t, value) => {
    if (t.risk === 'caution' && !(await askConfirm(`${value ? 'Enable' : 'Disable'} ${t.title}?\n\n${t.desc}\n\nYou can switch it back at any time.`, { ok: value ? 'Enable' : 'Disable' }))) return;
    setBusy(t.id);
    try { const r = await api.opt_set_toggle(t.id, value); toast(r.ok ? r.message : r.error); await reload(); }
    catch (e) { toast(String(e)); } finally { setBusy(''); }
  };
  const recommended = async () => {
    const todo = (st?.toggles || []).filter(t => t.recommended !== null && t.state !== null && t.state !== t.recommended);
    if (!todo.length) return toast('Everything recommended is already set.');
    if (!(await askConfirm('Apply these recommended settings?\n\n' + todo.map(t => `• ${t.title}: turn ${t.recommended ? 'on' : 'off'}`).join('\n') + '\n\nEach change is logged and can be reversed here.', { ok: 'Apply' }))) return;
    setBusy('recommended');
    try { const r = await api.opt_apply_recommended(); toast(r.message || r.error); await reload(); } catch (e) { toast(String(e)); } finally { setBusy(''); }
  };
  const plan = async key => { setBusy('plan'); try { const r = await api.opt_set_plan(key); toast(r.ok ? r.message : r.error); await reload(); } catch (e) { toast(String(e)); } finally { setBusy(''); } };
  const run = async a => {
    if (a.risk === 'caution' && !(await askConfirm(`Run "${a.title}"?\n\n${a.desc}`, { ok: 'Run' }))) return;
    if (a.long && !(await askConfirm(`"${a.title}" can take a long time. Keep Pulse open until it finishes. Start now?`, { ok: 'Start' }))) return;
    try { const r = await api.opt_run(a.id); if (!r.ok) return toast(r.error); setJob({ running:true, action:a.id, lines:[], progress:null, result:null }); }
    catch (e) { toast(String(e)); }
  };
  const startupToggle = async (it, value) => {
    try { const r = await api.opt_set_startup(it.id, value); toast(r.ok ? r.message : r.error); await loadStartup(); } catch (e) { toast(String(e)); }
  };
  const startupRemove = async it => {
    if (!(await askConfirm(`Remove "${it.name}" from startup?\n\nThe app is NOT uninstalled. Only its auto-start entry is deleted, and a backup is kept so you can restore it from "Removed startup apps".`, { code: it.command, ok: 'Remove' }))) return;
    try { const r = await api.opt_remove_startup(it.id); toast(r.ok ? r.message : r.error); await loadStartup(); await loadRemoved(); reload(); } catch (e) { toast(String(e)); }
  };
  const startupRestore = async b => {
    try { const r = await api.opt_restore_startup(b.bid); toast(r.ok ? r.message : r.error); await loadStartup(); await loadRemoved(); reload(); } catch (e) { toast(String(e)); }
  };
  const startupForget = async b => {
    if (!(await askConfirm(`Delete the backup of "${b.name}" permanently?\n\nIt can no longer be restored.`, { ok: 'Delete', danger: true }))) return;
    try { const r = await api.opt_forget_startup(b.bid); toast(r.ok ? r.message : r.error); await loadRemoved(); } catch (e) { toast(String(e)); }
  };
  const toggles = st?.toggles || [];
  const optimal = toggles.filter(t => t.optimal === true).length, attention = toggles.filter(t => t.optimal === false).length;
  const groups = [...new Set((st?.actions || []).map(a => a.group))];
  const activePlan = (st?.plans || []).find(p => p.active);
  const stateLabel = t => t.state === null ? 'Unknown' : t.state ? 'ON' : 'OFF';
  const jobTitle = (st?.actions || []).find(a => a.id === job?.action)?.title;
  const warp = net?.warp, dns = net?.dns, warpOn = !!warp?.connected;
  const netInfo = id => {
    if (!net || !net.supported) return {};
    if (id === 'warp_connect') return !warp.installed ? { block: 'Install the WARP app from 1.1.1.1 first', badge: ['NOT INSTALLED', 'warn'] } : warpOn ? { block: 'WARP is already connected', badge: ['CONNECTED', 'ok'] } : {};
    if (id === 'warp_disconnect') return !warp.installed ? { block: 'WARP is not installed' } : !warpOn ? { block: 'WARP is not connected' } : {};
    const want = { dns_google: 'google', dns_cloudflare: 'cloudflare', dns_auto: 'auto' }[id];
    if (want) {
      if (dns?.provider === want) return { block: 'Already in use', badge: ['ACTIVE', 'ok'] };
      if (warpOn) return { block: 'Disconnect WARP first: it manages DNS while connected' };
    }
    return {};
  };
  return <div className="optimizer-page">
    <div className="heading"><div><h1>Optimizer</h1><p className="sub">Sleep, startup and cleanup fixes. Every change is logged and reversible.</p></div>
      <div className="diag-actions-head"><span className="diag-privacy"><Icon name="shield" size={14}/> No registry cleaners · no bloat lists</span><button className="btn pri" disabled={!supported||!admin||busy==='recommended'} onClick={recommended}><Icon name="check" size={15}/> Apply recommended</button></div></div>
    {!supported && <div className="notice"><Icon name="info" size={18}/><span><b>Windows only.</b> Optimizer actions change Windows settings and are disabled on this system.</span></div>}
    {supported && !admin && <div className="sensor-alert"><Icon name="lock" size={18}/><div><strong>Administrator required</strong><p>Most power and repair fixes change system settings. Restart Pulse as Administrator to enable them; cleanup of your own temp files, DNS flush and the battery report work without it.</p></div></div>}
    <div className="opt-summary">
      <div className="card opt-stat"><span className="opt-stat-icon ok"><Icon name="check" size={18}/></span><div><strong>{optimal}</strong><small>Settings already optimal</small></div></div>
      <div className="card opt-stat"><span className="opt-stat-icon warn"><Icon name="info" size={18}/></span><div><strong>{attention}</strong><small>Could be improved</small></div></div>
      <div className="card opt-stat"><span className="opt-stat-icon info"><Icon name="zap" size={18}/></span><div><strong>{activePlan?.name || '—'}</strong><small>Active power plan</small></div></div>
      <div className="card opt-stat"><span className="opt-stat-icon violet"><Icon name="harddrive" size={18}/></span><div><strong>{st?.hiberfil || 'None'}</strong><small>Hibernation file</small></div></div>
    </div>
    <div className="section-row"><div><h2>Power plan</h2><p>Controls how aggressively Windows trades battery life for speed</p></div></div>
    <div className="plan-grid">{OPT_PLANS.map(([key,name,sub]) => { const on = !!activePlan && (activePlan.guid === ({balanced:'381b4222-f694-41f0-9685-ff5bb260df2e',performance:'8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c',saver:'a1841308-3541-4fab-bc81-f71556f20b4a'})[key] || (key==='ultimate' && /ultimate/i.test(activePlan.name))); return <button key={key} className={'card plan-card'+(on?' on':'')} disabled={!supported||busy==='plan'} onClick={()=>plan(key)}><Icon name={key==='saver'?'layers':key==='balanced'?'activity':key==='performance'?'zap':'rocket'} size={20}/><strong>{name}</strong><small>{sub}</small>{on && <em>Active</em>}</button>; })}</div>
    <div className="section-row"><div><h2>Power &amp; performance settings</h2><p>Switch off the features that most often cause sleep, wake and shutdown problems</p></div></div>
    <div className="opt-grid">{toggles.map(t => <div className={'card opt-card'+(t.optimal===false?' attention':t.optimal===true?' optimal':'')} key={t.id}>
      <div className="opt-card-top"><div><strong>{t.title}</strong><span className={'opt-state '+(t.state===null?'unknown':t.state?'on':'off')}>{stateLabel(t)}</span></div><Switch label={t.title} checked={t.state} disabled={!supported||!admin||t.state===null||busy===t.id} onChange={v=>toggle(t,v)}/></div>
      <p>{t.desc}</p>
      <div className="opt-tags">{t.recommended!==null && <span className={t.optimal?'tag ok':'tag warn'}>{t.optimal ? 'Optimal' : `RECOMMENDED: ${t.recommended?'ON':'OFF'}`}</span>}{t.risk==='caution' && <span className="tag caution">Asks first</span>}{t.reboot && <span className="tag">Restart</span>}</div>
    </div>)}{!toggles.length && <div className="card empty">Loading settings…</div>}</div>
    {groups.map(g => <div key={g}><div className="section-row"><div><h2>{g === 'Power & startup' ? 'Sleep & hibernation fixes' : g}</h2></div></div>
      {g==='Network' && net && net.supported && <div className="net-now"><span className="net-chip"><small>DNS NOW</small><b>{dns?.label || 'Unknown'}</b>{dns?.detail && <em>{dns.detail}</em>}</span><span className="net-chip"><small>WARP</small><b className={warp?.installed ? (warpOn ? 'on' : '') : 'dim'}>{!warp?.installed ? 'Not installed' : warp.connected === null ? 'Unknown' : warpOn ? 'Connected' : 'Off'}</b></span></div>}
      <div className="opt-grid">{(st.actions).filter(a=>a.group===g).map(a => { const ni = netInfo(a.id); return <div className={'card opt-card action'+(ni.badge && ni.badge[1]==='ok' ? ' optimal' : '')} key={a.id}>
        <div className="opt-card-top"><strong>{a.title}</strong><button className="btn" title={ni.block||''} disabled={!supported||(a.needs_admin&&!admin)||!!job?.running||!!ni.block} onClick={()=>run(a)}><Icon name="play" size={13}/> Run</button></div>
        <p>{a.desc}{a.id==='clean_user_temp'&&sizes?` Currently ${sizes.user_temp}.`:''}{a.id==='clean_win_temp'&&sizes?` Currently ${sizes.win_temp}.`:''}</p>
        <div className="opt-tags">{ni.badge && <span className={'tag '+ni.badge[1]}>{ni.badge[0]}</span>}{a.needs_admin && <span className="tag">Admin</span>}{a.long && <span className="tag">Long task</span>}{a.risk==='caution' && <span className="tag caution">Asks first</span>}{a.reboot && <span className="tag">Restart</span>}</div>{ni.block && !ni.badge && <p className="net-why">{ni.block}.</p>}</div>; })}</div></div>)}
    {(job?.running || job?.result || job?.lines?.length>0) && <div className="card opt-console"><div className="opt-console-head"><div><div className="eyebrow">Task output</div><h2>{jobTitle || 'Task'}</h2></div><span className={'opt-state '+(job.running?'on':job.result?.ok?'on':'off')}>{job.running?'Running':job.result?.ok?'DONE':'Failed'}</span></div>
      {job.running && <div className="diag-progress-rail"><i style={{width:(job.progress??8)+'%'}} className={job.progress==null?'indeterminate':''}/></div>}
      {job.result && <p className={'opt-result '+(job.result.ok?'ok':'bad')}>{job.result.ok?job.result.message:job.result.error}{job.result.reboot?' Restart Windows to finish.':''}</p>}
      <div className="opt-console-body" ref={logRef}>{(job.lines||[]).length?job.lines.map((l,i)=><div key={i}>{l}</div>):'Waiting for output…'}</div></div>}
    <div className="section-row"><div><h2>Startup apps</h2><p>Fewer startup apps means faster sign-in. Disabling only stops auto-start; nothing is uninstalled.</p></div><button className="btn secondary" onClick={loadStartup}><Icon name="refresh" size={14}/> Refresh</button></div>
    <div className="card startup-list">{!startup ? <div className="empty">Loading startup apps…</div> : !startup.items.length ? <div className="empty">{startup.error || 'No startup apps found.'}</div> :
      startup.items.map(it => <div className="startup-row" key={it.id}><div><b>{it.name}</b><small title={it.command}>{it.command}</small></div><span className="tag">{it.source}</span><button className="startup-remove" disabled={!!it.protected || (it.needs_admin && !admin)} title={it.protected ? 'Part of Windows (' + it.protected + '). Use the switch to disable it.' : it.needs_admin && !admin ? 'Run Pulse as Administrator to remove all-users entries' : 'Remove from startup (backup kept)'} aria-label={'Remove '+it.name+' from startup'} onClick={()=>startupRemove(it)}><Icon name={it.protected ? 'lock' : 'trash'} size={14}/><span>{it.protected ? 'System' : 'Remove'}</span></button><Switch label={'Start '+it.name+' at sign-in'} checked={it.enabled} disabled={it.needs_admin && !admin} onChange={v=>startupToggle(it,v)}/></div>)}</div>
    <div className="removed-box"><button className="removed-toggle" onClick={()=>setShowRemoved(v=>!v)} aria-expanded={showRemoved}><Icon name="chevron" size={13} className={showRemoved?'open':''}/> Removed startup apps ({removed.length})</button>
      {showRemoved && <div className="card startup-list">{!removed.length ? <div className="empty">Nothing removed yet.</div> : removed.map(b => <div className="startup-row" key={b.bid}><div><b>{b.kind==='file' ? b.name.replace(/\.[^.]+$/,'') : b.name}</b><small title={b.command}>{b.command}</small></div><span className="tag">{b.source}</span><button className="startup-restore" onClick={()=>startupRestore(b)}><Icon name="refresh" size={13}/> Restore</button><button className="startup-remove" title="Delete backup permanently" aria-label={'Delete backup of '+b.name} onClick={()=>startupForget(b)}><Icon name="trash" size={14}/></button></div>)}</div>}</div>
    <div className="section-row"><div><h2>Change history</h2><p>Everything Pulse changed on this PC</p></div><button className="btn secondary" onClick={()=>api.opt_open_log_folder()}><Icon name="arrow" size={14}/> Open folder</button></div>
    <div className="card history-list">{(st?.log||[]).length ? st.log.map((l,i)=><div className="history-row" key={i}><span className={'dot '+(l.ok?'ok':'bad')}/><div><b>{l.title || l.id}</b><small>{l.type}{l.before!==undefined?` · ${l.before===null?'?':l.before?'on':'off'} → ${l.after===null?'?':l.after?'on':'off'}`:''}{l.message?` · ${l.message}`:''}</small></div><time>{(l.time||'').replace('T',' ')}</time></div>) : <div className="empty">No changes yet.</div>}</div>
    <p className="disclaimer">Changes use documented Windows interfaces (powercfg, registry policy values, SFC/DISM). Create a restore point before system repairs if the PC holds critical data.</p>
  </div>;
}

/* ---------- desktop application shell ---------- */

function WindowControls({ api }) {
  const action = async (name) => {
    try {
      if (!api) return;
      if (name === 'minimize' && api.window_minimize) await api.window_minimize();
      if (name === 'close' && api.window_close) await api.window_close();
    } catch {}
  };
  return <div className="win-controls no-drag" role="group" aria-label="Window controls">
    <button className="win-btn" title="Minimize" aria-label="Minimize" onClick={() => action('minimize')}><Icon name="minus" size={16}/></button>
    <button className="win-btn close" title="Close" aria-label="Close" onClick={() => action('close')}><Icon name="x" size={15}/></button>
  </div>;
}

function SettingsPanel({prefs, setPrefs, wall, setWall, toast, close}) {
  const choose=(key, value)=>{
    if(key==='theme'&&value!==prefs.theme){ const a=document.getElementById('app'); if(a) a.setAttribute('data-switching','1'); }
    setPrefs(p=>({...p,[key]:value}));
  };
  const fileRef=useRef(null);
  const current=THEME_OPTIONS.find(t=>t.id===prefs.theme)||THEME_OPTIONS[0];
  const pick=async e=>{
    const f=e.target.files&&e.target.files[0]; e.target.value=''; if(!f) return;
    try { const url=await loadWallpaper(f); if(!saveWall(url)) toast('Image is too large for local storage. Try a smaller one.'); else { setWall(url); toast('Wallpaper applied.'); } }
    catch(err){ toast(String(err.message||err)); }
  };
  return <div className="settings-backdrop" onMouseDown={close}>
    <section role="dialog" aria-modal="true" aria-label="Display settings" className="settings-dialog" onMouseDown={e=>e.stopPropagation()}>
      <header className="settings-heading"><div><h2>Settings</h2><p>Appearance, refresh rate and performance options</p></div><button className="settings-close" aria-label="Close settings" onClick={close}><Icon name="x" size={18}/></button></header>
      <div className="settings-theme-area"><div className="settings-description"><strong>Theme</strong><small>Choose a colour scheme. Only the appearance changes; readings and results are unaffected.</small></div>
        {THEME_GROUPS.map(g=><div className="theme-group" key={g}><div className="theme-group-title">{g}</div>
          <div className="theme-grid" role="group" aria-label={g+' themes'}>{THEME_OPTIONS.filter(t=>t.group===g).map(t=><button key={t.id} aria-pressed={prefs.theme===t.id} className={'theme-card '+t.id+(prefs.theme===t.id?' active':'')} onClick={()=>choose('theme',t.id)}><span className="theme-swatch"><i/><i/><i/><b className="theme-check" aria-hidden="true"><Icon name="check" size={11}/></b></span><strong>{t.title}</strong><small>{t.subtitle}</small></button>)}</div></div>)}
      </div>
      <div className="settings-group"><div className="settings-description"><strong>Wallpaper</strong><small>{current.glass ? 'Displayed behind the translucent panels of '+current.title+'. The image is stored only on this computer.' : 'Available with translucent themes: Prism Glass, Crimson Noir, Neo Tokyo, Starlight and Sakura.'}</small></div>
        <div className="wall-actions"><input ref={fileRef} type="file" accept="image/*" hidden onChange={pick}/><button className="btn" onClick={()=>fileRef.current&&fileRef.current.click()}>Choose image</button>{wall && <button className="btn" onClick={()=>{saveWall(''); setWall('');}}>Remove</button>}</div>
      </div>
      <div className="settings-group"><div className="settings-description"><strong>Refresh interval</strong><small>How often the Overview reads hardware sensors.</small></div>
        <div className="interval-options">{POLL_OPTIONS.map(ms=><button key={ms} className={prefs.interval===ms?'selected':''} onClick={()=>choose('interval',ms)}>{ms/1000}s</button>)}</div>
      </div>
      <div className="settings-group"><div className="settings-description"><strong>CPU temperature source</strong><small>Select the reading that matches the tool you compare against.</small></div>
        <div className="interval-options temp-modes">{TEMP_MODES.map(([id,name,hint])=><button key={id} title={hint} className={prefs.tempMode===id?'selected':''} onClick={()=>choose('tempMode',id)}>{name}</button>)}</div>
      </div>
      <div className="settings-group"><div className="settings-description"><strong>Interface animations</strong><small>Hover and panel transitions. Charts always show live samples.</small></div>
        <label className="toggle" aria-label="Enable interface animations"><input type="checkbox" checked={prefs.motion} onChange={e=>choose('motion', e.target.checked)}/><span/></label>
      </div>
      <footer className="settings-footer"><Icon name="lock" size={13}/> Preferences are stored locally on this computer.</footer>
    </section>
  </div>;
}
function App() {
  const api=useApi(), [tab,setTab]=useState('dashboard'), [info,setInfo]=useState(null), [msg,setMsg]=useState('');
  const [settingsOpen,setSettingsOpen]=useState(false), [prefs,setPrefs]=useState(readPrefs), [wall,setWall]=useState(readWall);
  const timeout=useRef(null);
  const toast=t=>{setMsg(t);clearTimeout(timeout.current);timeout.current=setTimeout(()=>setMsg(''),4000);};
  useEffect(()=>{if(api)api.info().then(setInfo).catch(()=>{});},[api]);
  useEffect(()=>{if(api&&api.set_temp_mode)api.set_temp_mode(prefs.tempMode).catch(()=>{});},[api,prefs.tempMode]);
  useEffect(()=>{try{localStorage.setItem('pulse.prefs', JSON.stringify(prefs));}catch(_){}},[prefs]);
  useEffect(()=>{const a=document.getElementById('app'); if(!a||!a.hasAttribute('data-switching')) return;
    let r2=0; const r1=requestAnimationFrame(()=>{r2=requestAnimationFrame(()=>{ setTimeout(()=>a.removeAttribute('data-switching'),60); });});
    return()=>{cancelAnimationFrame(r1);cancelAnimationFrame(r2);};},[prefs.theme]);
  useEffect(()=>{const key=e=>{if(e.key==='Escape')setSettingsOpen(false);}; window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[]);
  const links=[['dashboard','layout','Overview'],['sensors','activity','Hardware Sensors'],['diagnostics','shield','Diagnostics'],['optimizer','tool','Optimizer']];
  return <div id="app" data-theme={prefs.theme} data-mode={themeMode(prefs.theme)} data-wall={wall && (THEME_OPTIONS.find(t=>t.id===prefs.theme)||{}).glass ? 'on' : 'off'} style={wall ? {'--wall':`url(${wall})`} : undefined} className={prefs.motion?'':'reduce-motion'}>
    <aside className="side"><div className="brand"><div><strong>Pulse</strong><small>Diagnostics</small></div></div>
      <nav className="nav" aria-label="Workspace navigation">{links.map(([id,ic,label])=><button key={id} className={tab===id?'on':''} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}><Icon name={ic} size={18}/><span>{label}</span></button>)}</nav>
      <div className="spacer"/><div className="side-bottom"><div className={'online-dot'+(!api?' offline':'')}/><div><strong>{api?'Engine online':'Connecting…'}</strong><small>{api? (info?.admin ? 'Administrator' : 'Standard user') : 'Waiting for backend'}{info?.version ? ' · v'+info.version : ''}</small></div></div>
    </aside>
    <main><div className="topbar drag-region"><div className="top-left pywebview-drag-region"><span className="top-title">{links.find(x=>x[0]===tab)?.[2]}</span></div><div className="top-right no-drag"><span className="local-tag"><Icon name="lock" size={12}/> Local only</span><button className="settings-button" title="Settings" aria-label="Open Settings" onClick={()=>setSettingsOpen(true)}><Icon name="settings" size={19}/></button><WindowControls api={api}/></div></div>
      <div className="scroll-area"><div className={'page '+(tab==='dashboard'?'on':'')}><Dashboard api={api} info={info} active={tab==='dashboard'} refreshMs={prefs.interval}/></div>
      <div className={'page '+(tab==='sensors'?'on':'')}><Sensors api={api} active={tab==='sensors'} refreshMs={prefs.interval}/></div>
      <div className={'page '+(tab==='diagnostics'?'on':'')}><Diagnostics api={api} toast={toast}/></div>
      <div className={'page '+(tab==='optimizer'?'on':'')}><Optimizer api={api} toast={toast} active={tab==='optimizer'}/></div></div>
    </main><div className={'toast'+(msg?' on':'')}>{msg}</div>
    {settingsOpen&&<SettingsPanel prefs={prefs} setPrefs={setPrefs} wall={wall} setWall={setWall} toast={toast} close={()=>setSettingsOpen(false)}/>}
    <ConfirmHost/>
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
