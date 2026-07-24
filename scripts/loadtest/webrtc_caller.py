#!/usr/bin/env python3
"""Headless WebRTC synthetic caller — Phase 0 load driver for finding K.

Drives N concurrent live calls against ONE pinned `web` pod over the WebRTC
signaling path (the only no-PSTN live-audio origination), so we can ramp
concurrency and read the pod's saturation knee. See AUTOSCALING_PLAN.md /
plans/make-a-plan-for-agile-pony.md.

This is standalone tooling — NOT shipped in the app image. It needs `aiortc`
(see requirements.txt in this dir) and must run against a real dev server with
TURN + real provider config; it cannot be exercised on a laptop.

Per call it:
  1. POST /api/v1/workflow/{id}/runs (mode=smallwebrtc) -> workflow_run_id
  2. WS  /api/v1/ws/signaling/{workflow_id}/{run_id}?api_key=...
  3. sends {"type":"offer","payload":{pc_id,sdp,type:"offer"}}, applies the
     server "answer", streams a looped speech WAV (media file supplies the
     turn cadence — put speech + >0.5s silence in the WAV and loop it), and
     consumes the returned bot audio track to measure received-audio gaps.
  4. records the LATENCY_MEASURED / TTFB_METRIC feedback frames the server pushes.

The x-axis + saturation signals are polled separately from
GET /api/v1/health/active-calls (active_calls, loop_lag_p95_ms, loop_lag_max_ms).

Turn cadence lives in the WAV, not code (ponytail: no custom audio-track
frame-injection). Prepare a loopable clip: a spoken utterance followed by
~0.7s of silence so Silero VAD fires a real end-of-turn each loop.

VERIFY AGAINST THE LIVE SERVER before trusting a ramp:
  * run with --concurrency 1 and confirm active_calls goes 0->1->0 and the bot
    audio track arrives (see the one-call smoke summary it prints);
  * ICE/TURN: aiortc bundles candidates in the SDP, but if FORCE_TURN_RELAY is
    set you must pass working TURN creds via --ice-server, or negotiation fails
    and you measure signaling, not the pipeline.
"""

import argparse
import asyncio
import json
import statistics
import time

import aiohttp
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer


async def _create_run(session, base_url, api_key, workflow_id):
    """Create a smallwebrtc workflow run and return its id."""
    async with session.post(
        f"{base_url}/api/v1/workflow/{workflow_id}/runs",
        headers={"X-API-Key": api_key},
        json={"mode": "smallwebrtc"},
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    # The run id field name can vary by version — confirm against the live API.
    return data.get("id") or data.get("run_id") or data["workflow_run_id"]


class _Call:
    """One synthetic call: negotiate, stream WAV, collect signals."""

    def __init__(self, idx, base_url, api_key, workflow_id, wav, ice_servers):
        self.idx = idx
        self.base_url = base_url
        self.api_key = api_key
        self.workflow_id = workflow_id
        self.wav = wav
        self.ice_servers = ice_servers
        self.latencies: list[float] = []
        self.ttfbs: list[float] = []
        self.bot_frame_ts: list[float] = []  # arrival times of bot audio frames
        self.error: str | None = None

    async def run(self, session, stop: asyncio.Event):
        ws_base = self.base_url.replace("http", "ws", 1)
        pc_id = f"loadtest-{self.idx}-{int(time.time() * 1000)}"
        pc = RTCPeerConnection(RTCConfiguration(iceServers=self.ice_servers))
        player = MediaPlayer(self.wav, loop=True)
        pc.addTrack(player.audio)
        consumers: list[asyncio.Task] = []

        @pc.on("track")
        def _on_track(track):
            # Bot audio: timestamp each frame to measure continuity/underruns.
            # This is the SOLE consumer of the track — a second reader (e.g. a
            # MediaBlackhole) would split frames between them and manufacture gaps.
            async def _consume():
                try:
                    while True:
                        await track.recv()
                        self.bot_frame_ts.append(time.monotonic())
                except Exception:
                    pass  # track ended / pc closed
            consumers.append(asyncio.ensure_future(_consume()))

        try:
            run_id = await _create_run(
                session, self.base_url, self.api_key, self.workflow_id
            )
            url = (
                f"{ws_base}/api/v1/ws/signaling/{self.workflow_id}/{run_id}"
                f"?api_key={self.api_key}"
            )
            async with session.ws_connect(url) as ws:
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
                await ws.send_json(
                    {
                        "type": "offer",
                        "payload": {
                            "pc_id": pc_id,
                            "sdp": pc.localDescription.sdp,
                            "type": pc.localDescription.type,
                        },
                    }
                )
                await self._pump(ws, pc, stop)
        except Exception as e:  # noqa: BLE001 — driver records, never crashes the ramp
            self.error = f"{type(e).__name__}: {e}"
        finally:
            for c in consumers:
                c.cancel()
            await pc.close()

    async def _pump(self, ws, pc, stop: asyncio.Event):
        """Read signaling + feedback frames until the ramp step ends."""
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                break  # closed / error
            data = json.loads(msg.data)
            mtype, payload = data.get("type"), data.get("payload", {})
            if mtype == "answer":
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
                )
            elif mtype == "error":
                self.error = json.dumps(payload)
                break
            # Feedback frames carry the type at the TOP level (not "realtime_feedback")
            # with the value in payload — see RealtimeFeedbackType in pipecat enums
            # and build_ttfb_metric_event / on_latency_measured in run_pipeline.py.
            elif mtype == "rtf-latency-measured" and "latency_seconds" in payload:
                self.latencies.append(payload["latency_seconds"])
            elif mtype == "rtf-ttfb-metric" and "ttfb_seconds" in payload:
                self.ttfbs.append(payload["ttfb_seconds"])

    def underrun_rate(self, frame_ms=20.0, jitter_ms=40.0):
        """Fraction of inter-frame gaps exceeding frame+jitter (audible breakup)."""
        ts = self.bot_frame_ts
        if len(ts) < 3:
            return None
        gaps = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
        bad = sum(1 for g in gaps if g > frame_ms + jitter_ms)
        return bad / len(gaps)


async def _poll_health(session, base_url, devops_secret, out, errors, stop):
    """Sample the pod's per-pod saturation signals for the whole step.

    Only 2xx JSON is recorded as a sample. A non-2xx (e.g. 403 bad secret, 503
    secret unset) records a health-poll error instead — otherwise the error
    body's missing keys default to 0 and a failed measurement masquerades as an
    idle pod, hiding the real saturation.
    """
    headers = {"X-Dograh-Devops-Secret": devops_secret} if devops_secret else {}
    while not stop.is_set():
        try:
            async with session.get(
                f"{base_url}/api/v1/health/active-calls", headers=headers
            ) as r:
                if r.status // 100 == 2:
                    out.append(await r.json())
                else:
                    errors.append(f"health {r.status}: {(await r.text())[:120]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"health poll: {type(e).__name__}: {e}")
        await asyncio.sleep(2)


async def run_step(args, n, wav, ice_servers):
    """Hold n concurrent calls for args.hold seconds; return the step report."""
    stop = asyncio.Event()
    health: list[dict] = []
    health_errors: list[str] = []
    async with aiohttp.ClientSession() as session:
        calls = [
            _Call(i, args.base_url, args.api_key, args.workflow_id, wav, ice_servers)
            for i in range(n)
        ]
        tasks = [asyncio.create_task(c.run(session, stop)) for c in calls]
        poller = asyncio.create_task(
            _poll_health(
                session, args.base_url, args.devops_secret, health, health_errors, stop
            )
        )
        await asyncio.sleep(args.hold)
        stop.set()
        await asyncio.gather(*tasks, poller, return_exceptions=True)

    lat = [x for c in calls for x in c.latencies]
    ttfb = [x for c in calls for x in c.ttfbs]
    underruns = [u for c in calls if (u := c.underrun_rate()) is not None]
    errors = [c.error for c in calls if c.error]
    active = [h.get("active_calls", 0) for h in health]
    lag_p95 = [h.get("loop_lag_p95_ms", 0) for h in health]
    lag_max = [h.get("loop_lag_max_ms", 0) for h in health]
    return {
        "target_n": n,
        "active_calls_max": max(active) if active else 0,
        "loop_lag_p95_ms_max": max(lag_p95) if lag_p95 else 0,
        "loop_lag_max_ms": max(lag_max) if lag_max else 0,
        "turn_latency_p50": round(statistics.median(lat), 3) if lat else None,
        "turn_latency_p95": round(_p95(lat), 3) if lat else None,
        "ttfb_p95": round(_p95(ttfb), 3) if ttfb else None,
        "underrun_rate_mean": round(statistics.mean(underruns), 4) if underruns else None,
        "errors": len(errors),
        "error_samples": errors[:3],
        "health_samples": len(active),
        "health_poll_errors": len(health_errors),
        "health_error_samples": health_errors[:3],
    }


def _p95(values):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def _parse_ice(specs):
    servers = [RTCIceServer(urls="stun:stun.l.google.com:19302")]
    for spec in specs or []:
        # format: urls[,username,credential]
        parts = spec.split(",")
        servers.append(
            RTCIceServer(
                urls=parts[0],
                username=parts[1] if len(parts) > 1 else None,
                credential=parts[2] if len(parts) > 2 else None,
            )
        )
    return servers


async def main():
    ap = argparse.ArgumentParser(description="WebRTC synthetic caller (Phase 0 K load test)")
    ap.add_argument("--base-url", required=True, help="e.g. https://dev.dograh.com")
    ap.add_argument("--api-key", required=True, help="test-org API key")
    ap.add_argument("--workflow-id", type=int, required=True)
    ap.add_argument("--wav", required=True, help="loopable speech+silence WAV")
    ap.add_argument("--devops-secret", default="", help="X-Dograh-Devops-Secret for /health")
    ap.add_argument("--ramp", default="1,2,5,10,15,20,25,30",
                    help="comma-separated concurrency steps")
    ap.add_argument("--hold", type=int, default=240, help="seconds to hold each step")
    ap.add_argument("--ice-server", action="append", dest="ice_servers",
                    help="urls[,user,cred] — repeatable; required if FORCE_TURN_RELAY")
    ap.add_argument("--drain", type=int, default=60,
                    help="seconds to wait between steps for active_calls to return to 0")
    args = ap.parse_args()

    ice = _parse_ice(args.ice_servers)
    steps = [int(x) for x in args.ramp.split(",")]
    print(json.dumps({"config": {"ramp": steps, "hold_s": args.hold}}))
    for n in steps:
        report = await run_step(args, n, args.wav, ice)
        print(json.dumps(report))
        if n != steps[-1]:
            # Let calls tear down and stale slots settle before the next step.
            await asyncio.sleep(args.drain)


if __name__ == "__main__":
    asyncio.run(main())
