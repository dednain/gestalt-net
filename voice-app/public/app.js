'use strict';
const $ = id => document.getElementById(id);
const state = {
  socket:null, room:null, selfId:null, peers:new Map(), connections:new Map(),
  iceServers:[], microphone:null, micWanted:false, hearWanted:true, deafened:false,
  busyMic:false, volume:80, joining:false
};
let toastTimer;

function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), 4000);
}
function nativeApp() { return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform()); }
function signalingUrl() {
  const saved = localStorage.getItem('auraVoiceSignaling');
  if (saved) return saved;
  // The static Vercel frontend has no /signal route; configure your standalone WSS server in Settings.
  if (!nativeApp() && /\.vercel\.app$/i.test(location.hostname)) return '';
  if (!nativeApp() && ['https:','http:'].includes(location.protocol)) return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/signal';
  return '';
}
function send(message) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify(message));
}
function peerAvatar(name) { return [...(name || '?')][0].toUpperCase(); }
function renderMembers() {
  const holder = $('members');
  holder.replaceChildren();
  const others = [...state.peers.values()];
  const all = [{id:state.selfId, name:$('nickname').value || 'Ты', mic:!!state.microphone, deafened:state.deafened, self:true}, ...others];
  $('memberCount').textContent = all.length + ' / 6';
  for (const peer of all) {
    const row = document.createElement('div'); row.className = 'member';
    const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = peerAvatar(peer.name);
    const info = document.createElement('div'); info.className = 'member-info';
    const name = document.createElement('div'); name.className = 'member-name'; name.textContent = peer.name + (peer.self ? ' (ты)' : '');
    const status = document.createElement('div'); status.className = 'member-status';
    status.textContent = peer.deafened ? 'Полный мут' : peer.mic ? 'Микрофон включён' : peer.self ? 'Микрофон выключен' : 'Слушает';
    info.append(name, status);
    const indicator = document.createElement('span'); indicator.className = 'member-indicator' + (peer.mic && !peer.deafened ? ' talking' : ''); indicator.textContent = peer.mic && !peer.deafened ? '◖))' : '◌';
    row.append(avatar, info, indicator); holder.append(row);
  }
}
function renderControls() {
  const mic = state.micWanted && !state.deafened;
  const sound = state.hearWanted && !state.deafened;
  $('micBtn').classList.toggle('active', mic);
  $('soundBtn').classList.toggle('active', sound);
  $('deafenBtn').classList.toggle('active', state.deafened);
  $('micBtn').setAttribute('aria-pressed', String(mic));
  $('soundBtn').setAttribute('aria-pressed', String(sound));
  $('deafenBtn').setAttribute('aria-pressed', String(state.deafened));
  $('micBtn').disabled = state.busyMic || state.deafened;
  $('soundBtn').disabled = state.deafened;
  $('micLabel').textContent = mic ? 'Говорю' : 'Микрофон выкл.';
  // Inline SVG is controlled by CSS so icons stay crisp on Android.
  $('soundLabel').textContent = sound ? 'Слушаю' : 'Не слышу';
  $('deafenLabel').textContent = state.deafened ? 'Снять мут' : 'Полный мут';
  document.body.classList.toggle('mic-on', !!state.microphone);
  for (const {audio} of state.connections.values()) if (audio) { audio.muted = !sound; audio.volume = state.volume / 100; }
  if (state.room) renderMembers();
}
function closeConnections() {
  for (const link of state.connections.values()) {
    link.pc.onnegotiationneeded = null;
    link.pc.close();
    if (link.audio) { link.audio.srcObject = null; link.audio.remove(); }
  }
  state.connections.clear(); state.peers.clear();
}
function stopMicrophone() {
  if (state.microphone) { state.microphone.getTracks().forEach(track => track.stop()); state.microphone = null; }
  document.body.classList.remove('mic-on');
}
function leaveRoom(showMessage=true) {
  const wasActive = !!state.room || state.joining;
  if (state.socket) { const socket = state.socket; state.socket = null; socket.onclose = null; socket.close(); }
  closeConnections(); stopMicrophone();
  state.room=null; state.selfId=null; state.iceServers=[]; state.joining=false;
  state.micWanted=false; state.deafened=false; state.hearWanted=true;
  $('call').classList.add('hidden'); $('lobby').classList.remove('hidden');
  document.body.classList.remove('in-call');
  $('heroSubtitle').textContent='Иногда нужно просто услышать друг друга. Создай своё пространство — без лишних людей и лишних слов.';
  document.title='AURA — на одной частоте';
  renderControls();
  $('joinBtn').disabled=false; $('createBtn').disabled=false;
  if (wasActive && showMessage) toast('Ты вышел из комнаты');
}
function makePeer(id) {
  if (state.connections.has(id)) return state.connections.get(id);
  const pc = new RTCPeerConnection({iceServers:state.iceServers, iceCandidatePoolSize:2});
  const link = {pc, audio:null, makingOffer:false, ignoreOffer:false, settingAnswer:false, candidates:[],
    polite:state.selfId > id};
  state.connections.set(id, link);
  link.transceiver = pc.addTransceiver('audio', {direction:state.microphone ? 'sendrecv' : 'recvonly'});
  if (state.microphone) link.transceiver.sender.replaceTrack(state.microphone.getAudioTracks()[0]).catch(console.warn);
  pc.onnegotiationneeded = async () => {
    try {
      link.makingOffer = true;
      await pc.setLocalDescription();
      send({type:pc.localDescription.type, to:id, description:pc.localDescription});
    } catch (error) { console.warn('Negotiation error', error); }
    finally { link.makingOffer = false; }
  };
  pc.onicecandidate = event => { if (event.candidate) send({type:'ice', to:id, candidate:event.candidate.toJSON()}); };
  pc.ontrack = event => {
    if (!link.audio) {
      link.audio = document.createElement('audio'); link.audio.autoplay=true; link.audio.playsInline=true;
      link.audio.style.display='none'; document.body.append(link.audio);
    }
    link.audio.srcObject = event.streams[0] || new MediaStream([event.track]);
    link.audio.muted= !state.hearWanted || state.deafened;
    link.audio.volume=state.volume/100;
    link.audio.play().catch(() => toast('Нажми «Слушаю», чтобы разрешить звук'));
  };
  pc.oniceconnectionstatechange = () => {
    if (pc.iceConnectionState === 'failed') { try { pc.restartIce(); } catch (error) { console.warn(error); } }
  };
  return link;
}
async function receiveSignal(data) {
  if (!state.peers.has(data.from)) return;
  const link = makePeer(data.from), pc = link.pc;
  try {
    if (data.type === 'ice') {
      if (pc.remoteDescription) await pc.addIceCandidate(data.candidate);
      else link.candidates.push(data.candidate);
      return;
    }
    const desc = data.description;
    const ready = !link.makingOffer && (pc.signalingState === 'stable' || link.settingAnswer);
    const collision = desc.type === 'offer' && !ready;
    link.ignoreOffer = !link.polite && collision;
    if (link.ignoreOffer) return;
    link.settingAnswer = desc.type === 'answer';
    await pc.setRemoteDescription(desc);
    link.settingAnswer = false;
    for (const candidate of link.candidates.splice(0)) await pc.addIceCandidate(candidate).catch(console.warn);
    if (desc.type === 'offer') {
      await pc.setLocalDescription();
      send({type:'answer', to:data.from, description:pc.localDescription});
    }
  } catch (error) { link.settingAnswer=false; console.warn('WebRTC:', error); toast('Ошибка соединения. Проверь TURN-сервер.'); }
}
function onServerMessage(data) {
  if (data.type === 'error') { toast(data.message || 'Ошибка сервера'); if (state.joining) leaveRoom(false); return; }
  if (data.type === 'joined') {
    state.joining=false; state.room=data.room; state.selfId=data.id;
    state.iceServers=Array.isArray(data.iceServers) ? data.iceServers : [];
    for (const peer of data.peers) state.peers.set(peer.id, peer);
    $('activeRoom').textContent=state.room;
    $('connectionText').textContent='На связи';
    $('lobby').classList.add('hidden'); $('call').classList.remove('hidden');
    document.body.classList.add('in-call');
    $('heroSubtitle').textContent='Связь установлена. Позови своих людей.';
    document.title=state.room + ' · AURA';
    renderControls();
    for (const peer of data.peers) makePeer(peer.id);
    send({type:'status', mic:false, deafened:state.deafened});
    toast('Комната создана. Включи микрофон, чтобы говорить.');
    return;
  }
  if (data.type === 'peer-joined') { state.peers.set(data.peer.id, data.peer); renderMembers(); makePeer(data.peer.id); toast(data.peer.name + ' присоединился'); }
  if (data.type === 'peer-left') {
    state.peers.delete(data.id);
    const link=state.connections.get(data.id);
    if (link) { link.pc.close(); if (link.audio) link.audio.remove(); state.connections.delete(data.id); }
    renderMembers();
  }
  if (data.type === 'peer-status') {
    const peer=state.peers.get(data.id);
    if (peer) { peer.mic=data.mic; peer.deafened=data.deafened; renderMembers(); }
  }
  if (['offer','answer','ice'].includes(data.type)) receiveSignal(data);
}
function startRoom(code) {
  if (state.room || state.joining) return;
  if (!/^[A-Z0-9]{6}$/.test(code)) return toast('Введи 6 символов кода комнаты');
  if (!('RTCPeerConnection' in window) || !navigator.mediaDevices?.getUserMedia) return toast('Браузер не поддерживает голос. Нужен HTTPS и современный Android Chrome.');
  const url = signalingUrl();
  if (!url) { $('settingsDialog').showModal(); return toast('Сначала укажи адрес своего WSS-сервера'); }
  if (!/^wss:\/\//.test(url) && !/^ws:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(url)) return toast('Для телефона нужен безопасный адрес wss://');
  state.joining=true; $('joinBtn').disabled=true; $('createBtn').disabled=true;
  const nick = $('nickname').value.trim().slice(0,24) || 'Гость';
  localStorage.setItem('auraVoiceNick', nick);
  try {
    const socket=new WebSocket(url); state.socket=socket;
    socket.onopen=()=>send({type:'join',room:code,name:nick});
    socket.onmessage=event=>{try{onServerMessage(JSON.parse(event.data));}catch(error){console.warn(error);}};
    socket.onerror=()=>toast('Не удалось подключиться. Проверь адрес сервера и интернет.');
    socket.onclose=()=>{if(state.socket===socket){leaveRoom(false);toast('Связь с сервером потеряна');}};
  } catch (error) { leaveRoom(false); toast('Неправильный адрес сервера'); }
}
async function syncMicrophone() {
  if (state.busyMic) return;
  state.busyMic=true;renderControls();
  const enabled=state.micWanted && !state.deafened;
  try {
    if (enabled && !state.microphone) {
      state.microphone=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    }
    const track=enabled ? state.microphone?.getAudioTracks()[0] : null;
    for (const link of state.connections.values()) {
      await link.transceiver.sender.replaceTrack(track || null);
      const wanted=track ? 'sendrecv' : 'recvonly';
      if (link.transceiver.direction !== wanted) link.transceiver.direction=wanted;
    }
    if (!enabled) stopMicrophone();
    send({type:'status', mic:!!track, deafened:state.deafened});
  } catch (error) {
    console.warn('Mic:',error);state.micWanted=false;stopMicrophone();
    send({type:'status',mic:false,deafened:state.deafened});
    toast('Нет доступа к микрофону — разреши его в настройках Android/браузера');
  } finally {state.busyMic=false;renderControls();}
}
function randomCode() {
  const alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const bytes=new Uint8Array(6);crypto.getRandomValues(bytes);
  return [...bytes].map(b=>alphabet[b%alphabet.length]).join('');
}
$('joinBtn').addEventListener('click',()=>startRoom($('roomCode').value.trim().toUpperCase()));
$('createBtn').addEventListener('click',()=>{const code=randomCode();$('roomCode').value=code;startRoom(code);});
$('roomCode').addEventListener('input',event=>{event.target.value=event.target.value.toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,6);});
$('roomCode').addEventListener('keydown',event=>{if(event.key==='Enter')$('joinBtn').click();});
$('micBtn').addEventListener('click',async()=>{state.micWanted=!state.micWanted;await syncMicrophone();});
$('soundBtn').addEventListener('click',()=>{state.hearWanted=!state.hearWanted;renderControls();if(state.hearWanted)for(const {audio} of state.connections.values())if(audio)audio.play().catch(()=>{});});
$('deafenBtn').addEventListener('click',async()=>{state.deafened=!state.deafened;renderControls();await syncMicrophone();});
$('volume').addEventListener('input',event=>{state.volume=Number(event.target.value);$('volumeValue').textContent=state.volume+'%';renderControls();});
$('leaveBtn').addEventListener('click',()=>leaveRoom());
$('copyBtn').addEventListener('click',async()=>{
  const invite=nativeApp() ? 'Войди в AURA VOICE, код комнаты: '+state.room : 'Заходи в AURA VOICE: '+location.origin+location.pathname+'?room='+state.room;
  try {await navigator.clipboard.writeText(invite);toast('Приглашение скопировано');}
  catch {toast('Код комнаты: '+state.room);}
});
$('settingsBtn').addEventListener('click',()=>{$('signalUrl').value=localStorage.getItem('auraVoiceSignaling')||'';$('settingsDialog').showModal();});
$('settingsDialog').addEventListener('close',()=>{
  if ($('settingsDialog').returnValue!=='save') return;
  const value=$('signalUrl').value.trim();
  if (value && !/^wss:\/\/[^/]+(?:\/.*)?$/i.test(value)) return toast('Адрес должен начинаться с wss://');
  localStorage.setItem('auraVoiceSignaling',value);toast('Адрес сохранён. Переподключись к комнате для применения.');
});
$('nickname').value=localStorage.getItem('auraVoiceNick')||'Гость';
const inviteCode=new URLSearchParams(location.search).get('room');
if (inviteCode && /^[A-Z0-9]{6}$/i.test(inviteCode)) $('roomCode').value=inviteCode.toUpperCase();
window.addEventListener('beforeunload',()=>{stopMicrophone();if(state.socket)state.socket.close();});
renderControls();
if ('serviceWorker' in navigator && location.protocol==='https:' && !nativeApp()) navigator.serviceWorker.register('/sw.js').catch(console.warn);
