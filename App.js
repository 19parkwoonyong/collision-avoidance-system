// App.js — 장치 대시보드 (여러 기기 카드 + 상세 + 로그인/회원가입)
// - 선택 상태를 '이름(string)'으로만 관리 -> 뒤로가기 안정 동작
// - 실시간 폴링 주기 1.5s로 단축, 창 포커스 시 즉시 갱신

import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

// ★ Flask 서버 주소: 현재 페이지 호스트 기반 자동 설정
const API_BASE = process.env.REACT_APP_API || `http://${window.location.hostname}:5000`;

const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
});

function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [devices, setDevices] = useState([]);
  const [isRegistering, setIsRegistering] = useState(false);
  // ✅ 선택 상태를 이름으로만 들고 있음
  const [selectedName, setSelectedName] = useState(null);

  // ---- 상태 갱신 ----
  const fetchStatus = useCallback(() => {
    api.get('/api/status')
      .then(res => {
        const list = Array.isArray(res.data) ? res.data : [];
        setDevices(list);

        // 선택된 장치가 목록에서 사라졌다면 선택 해제
        if (selectedName) {
          const stillExists = list.some(d => d.name === selectedName);
          if (!stillExists) setSelectedName(null);
        }
      })
      .catch(err => console.error('상태 갱신 실패:', err?.message || err));
  }, [selectedName]);

  // ---- 주기 갱신 (1.5초) + 창 포커스 즉시 갱신 ----
  useEffect(() => {
    if (!isLoggedIn) return;

    fetchStatus();
    const id = setInterval(fetchStatus, 1500);

    const onFocus = () => fetchStatus();
    window.addEventListener('focus', onFocus);

    return () => {
      clearInterval(id);
      window.removeEventListener('focus', onFocus);
    };
  }, [isLoggedIn, fetchStatus]);

  // ---- 모드 전환 (라즈베리파이 코드 실행/중지) ----
  const togglePower = (deviceName, newState) => {
    api.post('/api/power', { device: deviceName, on: newState })
      .then(() => {
        alert(`${deviceName} ${newState ? '코드 실행(일반 모드)' : '코드 중지(딥슬립 모드)'} 완료`);
        fetchStatus();
      })
      .catch(err => alert('모드 변경 실패: ' + (err.response?.data?.error || err.message)));
  };

  // ---- 로그인/로그아웃/회원가입 ----
  const handleLogin = () => {
    api.post('/api/login', { username, password })
      .then(() => { alert('로그인 성공'); setIsLoggedIn(true); })
      .catch(err => alert('로그인 실패: ' + (err.response?.data?.error || err.message)));
  };

  const handleLogout = () => {
    setSelectedName(null);
    api.post('/api/logout', {})
      .then(() => {
        setIsLoggedIn(false);
        setUsername(''); setPassword('');
        alert('로그아웃 되었습니다');
      })
      .catch(err => alert('로그아웃 실패: ' + (err.response?.data?.error || err.message)));
  };

  const handleRegister = () => {
    api.post('/api/register', { username, password })
      .then(() => { alert('회원가입 완료. 로그인 해주세요.'); setIsRegistering(false); })
      .catch(err => {
        const status = err.response?.status;
        const msg = err.response?.data?.error || err.message;
        if (status === 409) alert('이미 존재하는 사용자입니다. 다른 아이디를 사용하세요.');
        else if (status === 400) alert('아이디와 비밀번호를 모두 입력하세요.');
        else alert(`회원가입 실패: ${msg}`);
      });
  };

  // ---- 헬퍼 ----
  const getSignalLabel = (rssiStr) => {
    const v = parseInt(rssiStr, 10);
    if (Number.isNaN(v)) return 'N/A';
    if (v >= -50) return '좋음';
    if (v >= -70) return '보통';
    return '나쁨';
  };

  // 선택된 장치 객체 (렌더 시점에 동기화)
  const selectedDevice = selectedName
    ? devices.find(d => d.name === selectedName) || null
    : null;

  // ---- 상세 뷰 ----
  const renderDeviceDetail = (device) => (
    <div style={{
      border: '1px solid #00e0ff',
      borderRadius: '12px',
      padding: '20px',
      width: '320px',
      background: '#101820',
      color: '#00e0ff',
      margin: '0 auto',
      boxShadow: '0 0 20px #00e0ff88',
      fontFamily: 'Consolas, monospace'
    }}>
      <h3>{device.name}</h3>
      <p>상태: {device.status}</p>
      <p>보고: {device.last_report}</p>
      <p>업데이트 시간: {device.last_updated}</p>
      <p>신호 강도: {device.signal_strength} ({getSignalLabel(device.signal_strength)})</p>
      <p>측정 거리: {device.distance ?? 'N/A'}</p>
      <p>모드 상태: <strong>{device.power ? '코드 실행 중' : '코드 중지됨'}</strong></p>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '16px' }}>
        <button
          onClick={() => togglePower(device.name, true)}
          disabled={device.power}
          style={{ backgroundColor: '#00ffcc', color: '#000', padding: '8px', borderRadius: '6px', border: 'none' }}>
          코드 실행
        </button>
        <button
          onClick={() => togglePower(device.name, false)}
          disabled={!device.power}
          style={{ backgroundColor: '#ff0066', color: '#fff', padding: '8px', borderRadius: '6px', border: 'none' }}>
          코드 중지
        </button>
      </div>

      <div style={{ marginTop: '16px' }}>
        {/* ✅ 이름 상태를 null로 바꿔 목록으로 복귀 */}
        <button onClick={() => setSelectedName(null)}
          style={{ background: '#333', color: '#fff', padding: '6px', border: 'none', borderRadius: '4px' }}>
          뒤로가기
        </button>
      </div>
    </div>
  );

  // ---- 메인 UI ----
  return (
    <div style={{
      backgroundColor: '#0d0d0d',
      color: '#fff',
      minHeight: '100vh',
      padding: '40px',
      fontFamily: 'Consolas, monospace'
    }}>
      <div style={{
        maxWidth: '960px',
        margin: '0 auto',
        backgroundColor: '#1a1a1a',
        padding: '30px',
        borderRadius: '12px',
        boxShadow: '0 0 20px #00e0ff55'
      }}>
        {!isLoggedIn ? (
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ color: '#00e0ff' }}>{isRegistering ? '📝 회원가입' : '🔐 장치 대시보드 로그인'}</h2>
            <input
              type="text"
              placeholder="아이디"
              value={username}
              onChange={e => setUsername(e.target.value)}
              style={{ marginBottom: '10px', padding: '5px', width: '90%' }}
            /><br />
            <input
              type="password"
              placeholder="비밀번호"
              value={password}
              onChange={e => setPassword(e.target.value)}
              style={{ marginBottom: '10px', padding: '5px', width: '90%' }}
            /><br />
            {isRegistering ? (
              <>
                <button onClick={handleRegister}>회원가입</button>
                <p><button onClick={() => setIsRegistering(false)}>로그인 화면으로</button></p>
              </>
            ) : (
              <>
                <button onClick={handleLogin}>로그인</button>
                <p><button onClick={() => setIsRegistering(true)}>회원가입</button></p>
              </>
            )}
          </div>
        ) : (
          <>
            <h2 style={{ textAlign: 'center', color: '#00e0ff' }}>🔌 장치 대시보드</h2>

            {selectedDevice ? (
              renderDeviceDetail(selectedDevice)
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', justifyContent: 'center' }}>
                {devices.map(device => (
                  <div
                    key={device.name}
                    onClick={() => setSelectedName(device.name)}  // ✅ 이름만 저장
                    style={{
                      border: '2px solid #00e0ff',
                      cursor: 'pointer',
                      borderRadius: '8px',
                      width: '100px',
                      height: '100px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '24px',
                      backgroundColor: '#222',
                      color: '#00e0ff',
                      boxShadow: 'inset 0 0 10px #00e0ff33'
                    }}>
                    {device.name.replace('chair', '')}
                  </div>
                ))}
              </div>
            )}

            <div style={{ textAlign: 'center', marginTop: '20px' }}>
              <button onClick={handleLogout}
                style={{ backgroundColor: '#444', color: '#fff', padding: '8px 16px', borderRadius: '6px', border: 'none' }}>
                로그아웃
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default App;
