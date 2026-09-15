# Third-party notices

## Hermes Talk and OpenClaw subscription voice transport

`api/services/pipecat/realtime/openai_live_subscription_transport.py` adapts
`talk_live_transport.py`, `talk_live_audio.py`, `talk_live_protocol.py`, and
`talk_live_config.py` from [Hermes Talk](https://github.com/TheSmokeDev/hermes-talk),
revision `8100046e63f23efc7c77b60a65184549bf85ad48` (MIT).
`api/services/configuration/openai_subscription_auth.py` also adapts credential
parsing and refresh behavior from `talk_auth.py` at that same Hermes Talk revision.

The subscription request, call-identity, sideband, model/voice, and delegation
shapes derive from [OpenClaw PR #133079](https://github.com/openclaw/openclaw/pull/133079),
authored by [steipete](https://github.com/steipete), at revision
`76378ddb777eacbe2c7f65c4247692b2f5830e97`. The referenced OpenClaw files are
`extensions/openai/realtime-quicksilver.ts`, `realtime-quicksilver-wire.ts`,
`realtime-quicksilver-sideband.ts`, and `realtime-quicksilver-delegation-controller.ts`.

Credit to [JakeStevenson](https://github.com/JakeStevenson) for the GPT-Live
client-delegation proposal and implementation in
[Hermes Talk PR #135](https://github.com/TheSmokeDev/hermes-talk/pull/135).
Hermes selectively incorporated its Live-mode and browser transport ideas;
this notice does not represent a wholesale merge of that contribution.

The applicable MIT notices are reproduced below. The OpenClaw repository also
maintains its own [additional notices](https://github.com/openclaw/openclaw/blob/76378ddb777eacbe2c7f65c4247692b2f5830e97/THIRD_PARTY_NOTICES.md).

### Hermes Talk

```text
MIT License

Copyright (c) 2026 SmokeDev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### OpenClaw

```text
MIT License

Copyright (c) 2026 OpenClaw Foundation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Hermes Agent subscription reasoning

`api/services/configuration/openai_subscription_responses.py` adapts the Codex
OAuth Responses request and SSE reconstruction behavior from
[Hermes Agent](https://github.com/NousResearch/hermes-agent), revision
`4eb66e3eef41ea6892277f007393a56f102230c8`: `agent/auxiliary_client.py`,
`agent/codex_headers.py`, `agent/transports/codex.py`, and `agent/codex_runtime.py`.
Dograh uses its own client identity and has no Hermes runtime dependency.

```text
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
