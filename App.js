// App.js — 대시보드 + 운영 모니터 + LED 카운트 + 500ms 폴링 + 중복요청 가드
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import axios from 'axios';

const API_BASE = process.env.REACT_APP_API || `http://${window.location.hostname}:5000`;
const POLL_MS = 500;
const api = axios.create({ baseURL: API_BASE, withCredentials: true, headers: { 'Cache-Control': 'no-cache' } });

const LS_KEY = 'ledCounts_v2';
const toDeviceLabel = (n='') => n.replace(/^chair/, 'device');
const isLedOnEvent = (m='') => typeof m === 'string' && (m.includes('LED/BUZZER 점등') || m.includes('LED 점등'));
const loadCounts = () => { try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch { return {}; } };
const saveCounts = (o) => { try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch {} };

export default function App() {
  const [username, setUsername]   = useState('');
  const [password, setPassword]   = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);

  const [devices, setDevices] = useState([]);
  const [selectedName, setSelectedName] = useState(null);
  const [view, setView] = useState('dashboard');

  const [ledCounts, setLedCounts] = useState(() => loadCounts());
  const [lastReportsMap, setLastReportsMap] = useState({});
  const [lastUpdatedMap, setLastUpdatedMap] = useState({});

  const inFlight = useRef(false);
  const pollTimer = useRef(null);

  const fetchStatus = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    api.get('/api/status', { params: { t: Date.now() } })
      .then(res => {
        const list = Array.isArray(res.data) ? res.data : [];
        setDevices(list);
        if (selectedName && !list.some(d => d.name === selectedName)) setSelectedName(null);

        const nc = { ...ledCounts }, nr = { ...lastReportsMap }, nu = { ...lastUpdatedMap };
        for (const d of list) {
          const msg = d.last_report || ''; const upd = d.last_updated || ''; const prev = nu[d.name];
          if (msg && isLedOnEvent(msg) && upd && upd !== prev) nc[d.name] = (nc[d.name] || 0) + 1;
          nr[d.name] = msg; nu[d.name] = upd;
        }
        if (JSON.stringify(nc) !== JSON.stringify(ledCounts)) { setLedCounts(nc); saveCounts(nc); }
        if (JSON.stringify(nr) !== JSON.stringify(lastReportsMap)) setLastReportsMap(nr);
        if (JSON.stringify(nu) !== JSON.stringify(lastUpdatedMap)) setLastUpdatedMap(nu);
      })
      .catch(e => console.error('상태 갱신 실패:', e?.message || e))
      .finally(() => { inFlight.current = false; });
  }, [selectedName, ledCounts, lastReportsMap, lastUpdatedMap]);

  useEffect(() => {
    if (!isLoggedIn) return;
    fetchStatus();
    pollTimer.current = setInterval(fetchStatus, POLL_MS);
    const onFocus = () => fetchStatus();
    const onVisibility = () => { if (!document.hidden) fetchStatus(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [isLoggedIn, fetchStatus]);

  const handleLogin = () => api.post('/api/login', { username, password }).then(()=>{alert('로그인 성공');setIsLoggedIn(true);}).catch(e=>alert('로그인 실패: '+(e.response?.data?.error||e.message)));
  const handleLogout= () => { setSelectedName(null); api.post('/api/logout',{}).then(()=>{setIsLoggedIn(false);setUsername('');setPassword('');alert('로그아웃 되었습니다');}).catch(e=>alert('로그아웃 실패: '+(e.response?.data?.error||e.message))); };
  const handleRegister= () => api.post('/api/register',{username,password}).then(()=>{alert('회원가입 완료. 로그인 해주세요.');setIsRegistering(false);}).catch(e=>{const s=e.response?.status;const m=e.response?.data?.error||e.message;if(s===409)alert('이미 존재하는 사용자입니다.');else if(s===400)alert('아이디/비밀번호를 모두 입력하세요.');else alert(`회원가입 실패: ${m}`);});

  const getSignalLabel = (r) => { const v = parseInt(r,10); if (Number.isNaN(v)) return 'N/A'; if (v >= -50) return '좋음'; if (v >= -70) return '보통'; return '나쁨'; };
  const selectedDevice = useMemo(()=> (selectedName ? (devices.find(d=>d.name===selectedName)||null):null),[selectedName,devices]);

  // ★ 프록시 라우트 사용 (에이전트로 전달)
  const togglePower = (deviceName, on) => {
    const action = on ? 'wake' : 'sleep';
    api.post(`/api/agent/${deviceName}/${action}`, {})
      .then(res => { alert(`${toDeviceLabel(deviceName)}: ${on?'코드 실행':'코드 중지'} 요청 완료`); fetchStatus(); })
      .catch(err => alert('모드 변경 실패: ' + (err.response?.data?.error || err.message)));
  };

  const MonitorView = () => (
    <div style={{border:'1px solid #00e0ff',borderRadius:12,padding:20,background:'#101820',color:'#00e0ff',boxShadow:'0 0 20px #00e0ff88',fontFamily:'Consolas, monospace'}}>
      <h3>운영 모니터</h3>
      <p style={{marginTop:0,color:'#9be7ff'}}>LED 점등 누적 카운트 및 최근 보고 메시지</p>
      <div style={{display:'flex',flexDirection:'column',gap:12}}>
        {devices.map(d=>(
          <div key={d.name} style={{border:'1px solid #0dd',borderRadius:8,padding:12,background:'#0b141a'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <strong style={{fontSize:18}}>{toDeviceLabel(d.name)}</strong>
              <div>
                <span style={{marginRight:12}}>누적: <b>{ledCounts[d.name]||0}</b> 회</span>
                <button onClick={()=>{const next={...ledCounts,[d.name]:0};setLedCounts(next);saveCounts(next);}} style={{background:'#333',color:'#fff',border:'none',padding:'6px 10px',borderRadius:6}}>이 장치 카운트 리셋</button>
              </div>
            </div>
            <div style={{marginTop:8,color:'#bdeaff'}}>최근 보고: {d.last_report||'없음'}</div>
            <div style={{marginTop:4,color:'#8bd3ff'}}>업데이트 시간: {d.last_updated||'N/A'}</div>
          </div>
        ))}
      </div>
      <div style={{marginTop:16,display:'flex',gap:8}}>
        <button onClick={()=>setView('dashboard')} style={{background:'#333',color:'#fff',border:'none',padding:'8px 12px',borderRadius:6}}>대시보드로</button>
        <button onClick={()=>{setLedCounts({});saveCounts({});}} style={{background:'#550000',color:'#fff',border:'none',padding:'8px 12px',borderRadius:6}}>전체 카운트 초기화</button>
      </div>
    </div>
  );

  const renderDeviceDetail = (device) => (
    <div style={{border:'1px solid #00e0ff',borderRadius:12,padding:20,width:360,background:'#101820',color:'#00e0ff',margin:'0 auto',boxShadow:'0 0 20px #00e0ff88',fontFamily:'Consolas, monospace'}}>
      <h3>{toDeviceLabel(device.name)}</h3>
      <p>상태: {device.status}</p>
      <p>보고: {device.last_report}</p>
      <p>업데이트 시간: {device.last_updated}</p>
      <p>신호 강도: {device.signal_strength} ({getSignalLabel(device.signal_strength)})</p>
      <p>측정 거리: {device.distance ?? 'N/A'}</p>
      <p>LED 점등 누적: <strong>{ledCounts[device.name] || 0} 회</strong></p>
      <div style={{display:'flex',justifyContent:'space-between',marginTop:14}}>
        <button onClick={()=>togglePower(device.name,true)}  disabled={device.power} style={{background:'#00ffcc',color:'#000',padding:'8px 12px',border:'none',borderRadius:8}}>코드 실행</button>
        <button onClick={()=>togglePower(device.name,false)} disabled={!device.power} style={{background:'#ff0066',color:'#fff',padding:'8px 12px',border:'none',borderRadius:8}}>코드 중지</button>
      </div>
      <div style={{marginTop:16}}>
        <button onClick={()=>setSelectedName(null)} style={{background:'#333',color:'#fff',padding:'6px 10px',border:'none',borderRadius:6}}>뒤로가기</button>
      </div>
    </div>
  );

  return (
    <div style={{backgroundColor:'#0d0d0d',color:'#fff',minHeight:'100vh',padding:40,fontFamily:'Consolas, monospace'}}>
      <div style={{maxWidth:980,margin:'0 auto',backgroundColor:'#1a1a1a',padding:30,borderRadius:12,boxShadow:'0 0 20px #00e0ff55'}}>
        {!isLoggedIn ? (
          <div style={{textAlign:'center'}}>
            <h2 style={{color:'#00e0ff'}}>{isRegistering?'📝 회원가입':'🔐 장치 대시보드 로그인'}</h2>
            <input type="text" placeholder="아이디" value={username} onChange={e=>setUsername(e.target.value)} style={{marginBottom:10,padding:5,width:'90%'}}/><br/>
            <input type="password" placeholder="비밀번호" value={password} onChange={e=>setPassword(e.target.value)} style={{marginBottom:10,padding:5,width:'90%'}}/><br/>
            {isRegistering ? (
              <>
                <button onClick={handleRegister}>회원가입</button>
                <p><button onClick={()=>setIsRegistering(false)}>로그인 화면으로</button></p>
              </>
            ) : (
              <>
                <button onClick={handleLogin}>로그인</button>
                <p><button onClick={()=>setIsRegistering(true)}>회원가입</button></p>
              </>
            )}
          </div>
        ) : (
          <>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <h2 style={{color:'#00e0ff',margin:0}}>🔌 장치 대시보드</h2>
              <div style={{display:'flex',gap:8}}>
                <button onClick={()=>setView(view==='dashboard'?'monitor':'dashboard')} style={{backgroundColor:'#0d5',color:'#000',padding:'8px 12px',borderRadius:6,border:'none'}}>{view==='dashboard'?'운영 모니터 열기':'대시보드로'}</button>
                <button onClick={handleLogout} style={{backgroundColor:'#444',color:'#fff',padding:'8px 12px',borderRadius:6,border:'none'}}>로그아웃</button>
              </div>
            </div>

            {view==='monitor' ? (
              <div style={{marginTop:20}}><MonitorView/></div>
            ) : selectedDevice ? (
              <div style={{marginTop:20}}>{renderDeviceDetail(selectedDevice)}</div>
            ) : (
              <div style={{display:'flex',flexWrap:'wrap',gap:20,justifyContent:'center',marginTop:20}}>
                {devices.map(device=>(
                  <div key={device.name} onClick={()=>setSelectedName(device.name)} style={{border:'2px solid #00e0ff',cursor:'pointer',borderRadius:8,width:120,height:110,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',fontSize:20,backgroundColor:'#222',color:'#00e0ff',boxShadow:'inset 0 0 10px #00e0ff33'}}>
                    <div>{toDeviceLabel(device.name)}</div>
                    <div style={{fontSize:12,marginTop:6,color:'#9be7ff'}}>LED {ledCounts[device.name]||0}회</div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
