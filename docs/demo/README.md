# End-to-end demo video

[`supportpilot-demo.mp4`](supportpilot-demo.mp4) (95 s, 1080p, no audio) shows the fictional Wattle Bay Hospital site and the
agent console side by side, with a caption for each step:

1. Sam signs in to the agent console.
2. A guest asks about parking: the answer comes from the hospital's own document and cites it.
3. The guest asks about an appointment and is asked to sign in.
4. Peter, signed in, sees his appointment APT-1001.
5. Mary asks about the same number and gets "not found", the same reply as for a number that doesn't exist.
6. Mary reports a problem and a ticket is filed.
7. Peter asks for a person: the phrase hands off without calling the model, and the chat appears in Sam's queue.
8. Sam takes over and they chat live.
9. Sam resolves the conversation.

It was recorded with `LLM_PROVIDER=demo`, the keyword stand-in model, so reply wording is scripted. Sign-in, retrieval,
appointment lookups, tickets, handoff and live messaging are the real code paths. Narration for a voice-over is in the
speaker notes of the technical deck ([`../pitch/`](../pitch/README.md)).

## Re-recording

[`record.js`](record.js) drives [`stage.html`](stage.html) with Playwright. Start the app on a fresh database, then record
and convert:

```bash
cd backend && DATABASE_URL=sqlite+aiosqlite:///./demo-video.db LLM_PROVIDER=demo STATIC_DIR=../frontend/dist .venv/bin/uvicorn app.main:app_factory --factory --port 8000
```

```bash
node docs/demo/record.js /tmp/demo-video && ffmpeg -i /tmp/demo-video/*.webm -c:v libx264 -crf 24 -pix_fmt yuv420p -movflags +faststart -an docs/demo/supportpilot-demo.mp4
```

With a real provider (`LLM_PROVIDER=anthropic` and a key) the same script records natural replies; the waits after each
message may need lengthening.
