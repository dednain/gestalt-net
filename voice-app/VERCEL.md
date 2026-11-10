# Luna — voice club

A mobile-first website for audio rooms, originally prototyped under AURA. This branch focuses on the website; Android is postponed.

## Web deployment (Vercel)

- Git repository: `dednain/gestalt-net`
- Branch for this work: `feature/luna-voice-android`
- Root directory: `voice-app`
- Framework: Other (static)
- Build command: `npm run check`
- Output directory: `public`

`vercel.json` contains these settings. The site can be deployed statically from this branch without moving the rest of the repository.

**Important:** Vercel hosts the beautiful web client, not the current WebSocket signaling server (`server.js`) or TURN relay. Until signaling is deployed to a reachable VPS, room buttons cannot establish real calls on the public site.

## Enabling voice

1. Set up a VPS with HTTPS, WSS and TURN (see `deploy/`, `README.md`).
2. Open the deployed Luna site.
3. Choose Settings and enter `wss://YOUR_VOICE_HOST/signal`.
4. On two phones, join the same six-character room and enable microphones.
5. Test from Russian mobile networks. Neither the Vercel site nor foreign VPS can be assumed to work without VPN without network testing.

Local preview of site + signaling: `npm install && npm start` and visit `http://localhost:3000`. Public web voice still requires TURN on restrictive networks.

Browser access to microphone needs a secure context (HTTPS or localhost).

## Design

Editorial dark palette, citron accents, vinyl waveform motif, compact mobile room card and call controls. CSS+SVG; no remote font, analytics or image dependency.
