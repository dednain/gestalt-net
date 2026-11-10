'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { WebSocketServer, WebSocket } = require('ws');

const PUBLIC_DIR = path.join(__dirname, 'public');
const MAX_PEERS = 6;
const MIME = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.webmanifest':'application/manifest+json','.svg':'image/svg+xml','.png':'image/png'};
const rooms = new Map();
const port = Number(process.env.PORT || 3000);

function iceServersFor(id) {
  const servers = [];
  if (process.env.STUN_URL !== 'off') servers.push({urls: process.env.STUN_URL || 'stun:stun.l.google.com:19302'});
  const urls = (process.env.TURN_URLS || '').split(',').map(s => s.trim()).filter(Boolean);
  if (urls.length && process.env.TURN_SECRET) {
    const username = String(Math.floor(Date.now() / 1000) + 3600) + ':' + id;
    const credential = crypto.createHmac('sha1', process.env.TURN_SECRET).update(username).digest('base64');
    servers.push({urls, username, credential});
  }
  return servers;
}

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  if (pathname === '/health') {
    res.writeHead(200, {'Content-Type':'application/json','Cache-Control':'no-store'});
    res.end(JSON.stringify({ok:true, activeRooms:rooms.size}));
    return;
  }
  let requestPath;
  try { requestPath = decodeURIComponent(pathname); } catch { res.writeHead(400); res.end(); return; }
  if (requestPath === '/') requestPath = '/index.html';
  const filename = path.resolve(PUBLIC_DIR, '.' + requestPath);
  if (!filename.startsWith(PUBLIC_DIR + path.sep)) { res.writeHead(403); res.end(); return; }
  fs.stat(filename, (err, stats) => {
    if (err || !stats.isFile()) { res.writeHead(404); res.end('Not found'); return; }
    const ext = path.extname(filename);
    res.writeHead(200, {'Content-Type':MIME[ext] || 'application/octet-stream','X-Content-Type-Options':'nosniff','Cache-Control':ext === '.html' ? 'no-store' : 'public, max-age=300'});
    fs.createReadStream(filename).pipe(res);
  });
});

const wss = new WebSocketServer({noServer:true, maxPayload:8192, perMessageDeflate:false});
server.on('upgrade', (req, socket, head) => {
  if (new URL(req.url, 'http://localhost').pathname !== '/signal') return socket.destroy();
  if (process.env.ALLOWED_ORIGIN && [process.env.ALLOWED_ORIGIN, 'https://localhost', 'capacitor://localhost'].includes(req.headers.origin) === false) return socket.destroy();
  wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws));
});

function send(ws, object) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(object)); }
function peerInfo(peer) { return {id:peer.id, name:peer.name, mic:peer.mic, deafened:peer.deafened}; }
function announce(room, exclude, object) { for (const peer of room.values()) if (peer !== exclude) send(peer.ws, object); }
function leave(peer) {
  if (!peer.room) return;
  const room = rooms.get(peer.room);
  if (room) {
    room.delete(peer.id);
    announce(room, peer, {type:'peer-left', id:peer.id});
    if (room.size === 0) rooms.delete(peer.room);
  }
  peer.room = null;
}

wss.on('connection', ws => {
  const peer = {id:crypto.randomBytes(8).toString('hex'), ws, room:null, name:'Гость', mic:false, deafened:false, alive:true};
  ws.on('pong', () => { peer.alive = true; });
  ws.on('message', bytes => {
    let data;
    try { data = JSON.parse(bytes.toString()); } catch { return send(ws, {type:'error', message:'Некорректное сообщение'}); }
    if (!data || typeof data !== 'object' || typeof data.type !== 'string') return;
    if (data.type === 'join') {
      if (peer.room) return send(ws, {type:'error', message:'Вы уже в комнате'});
      const code = String(data.room || '').toUpperCase();
      if (!/^[A-Z0-9]{6}$/.test(code)) return send(ws, {type:'error', message:'Нужен код из 6 символов'});
      let room = rooms.get(code);
      if (room && room.size >= MAX_PEERS) return send(ws, {type:'error', message:'Комната заполнена (максимум 6 человек)'});
      if (!room) { room = new Map(); rooms.set(code, room); }
      peer.name = String(data.name || 'Гость').replace(/[\u0000-\u001f]/g, '').trim().slice(0, 24) || 'Гость';
      peer.room = code;
      send(ws, {type:'joined', id:peer.id, room:code, peers:[...room.values()].map(peerInfo), iceServers:iceServersFor(peer.id)});
      room.set(peer.id, peer);
      announce(room, peer, {type:'peer-joined', peer:peerInfo(peer)});
      return;
    }
    if (!peer.room) return send(ws, {type:'error', message:'Сначала войдите в комнату'});
    const room = rooms.get(peer.room);
    if (!room) return;
    if (data.type === 'status') {
      if (typeof data.mic === 'boolean') peer.mic = data.mic;
      if (typeof data.deafened === 'boolean') peer.deafened = data.deafened;
      announce(room, peer, {type:'peer-status', id:peer.id, mic:peer.mic, deafened:peer.deafened});
    } else if (['offer','answer','ice'].includes(data.type)) {
      if (typeof data.to !== 'string' || data.to === peer.id) return;
      const recipient = room.get(data.to);
      if (!recipient) return;
      if (data.type === 'ice' && (!data.candidate || typeof data.candidate !== 'object')) return;
      if (data.type !== 'ice' && (!data.description || typeof data.description !== 'object' || data.description.type !== data.type)) return;
      send(recipient.ws, {type:data.type, from:peer.id, ...(data.type === 'ice' ? {candidate:data.candidate} : {description:data.description})});
    }
  });
  ws.on('close', () => leave(peer));
  ws.on('error', () => leave(peer));
});

const heartbeat = setInterval(() => {
  for (const ws of wss.clients) {
    const peer = [...rooms.values()].flatMap(r => [...r.values()]).find(p => p.ws === ws);
    if (peer && !peer.alive) { ws.terminate(); continue; }
    if (peer) peer.alive = false;
    ws.ping();
  }
}, 30000);
heartbeat.unref();

if (require.main === module) server.listen(port, '0.0.0.0', () => console.log('AURA VOICE listening on :' + port));
module.exports = {server, rooms, wss, iceServersFor};
