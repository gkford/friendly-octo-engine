# Pi + Groq (Qwen 3.6 27B) setup — fast cheap coding agent with rate-limit fallback

How to run the [pi coding agent](https://github.com/earendil-works/pi) on Groq's
`qwen/qwen3.6-27b` (very fast, very cheap, but tight per-minute rate limits) with
automatic fallback to `z-ai/glm-5.2:nitro` on OpenRouter when Groq throttles.

Everything lives in `~/.pi/agent/` (global, not per-repo), so in a fresh
codespace/machine you recreate it once and every project gets it. This doc
contains the full contents of every file. Built/debugged 2026-07-11 against
pi 0.80.6; file paths into pi's internals may drift in later versions.

## The problems this setup solves

Discovered the hard way, in order:

1. **`reasoning_effort` 400s** — Groq's Qwen models only accept
   `none`/`default`, but pi sends its own levels (`low`/`medium`/`high`…).
   Fixed with a `thinkingLevelMap` in models.json (same pattern pi's built-in
   Groq qwen3-32b entry uses).
2. **`developer` role 400s** — pi sends the system prompt as a `developer`
   message for reasoning models; Groq's qwen3.6 chat template raises
   "Unexpected message role" on it. Fixed with
   `compat.supportsDeveloperRole: false` (pi then sends a normal `system` role).
3. **OTPM 429s** — Groq's output-tokens-per-minute limit (32k for this model)
   counts the **`max_tokens` reservation per request**, not actual output. A
   large `maxTokens` means one or two requests exhaust the minute. Fixed by
   setting `maxTokens: 8000` (= 4 requests/min of budget).
4. **TPM 429s from context bloat** — the model's `<think>` reasoning (often
   ~90% of its output!) was stored by pi and **replayed as input in every
   subsequent request**. Groq doesn't prompt-cache this model, so a long
   session re-pays the whole reasoning history against the 250k TPM limit on
   every call. Fixed by the `strip-reasoning-replay` extension (pi has no
   built-in setting for this — verified against pi 0.80.6 settings/compat
   docs and serializer source).
5. **Truncated responses** — `maxTokens` covers thinking + answer combined; on
   thinking `high` a long trace can eat the whole budget and truncate the
   answer. Mitigation: run with thinking **off** for routine work (Shift+Tab
   cycles it; our map makes "off" send `reasoning_effort: "none"`), flip to
   high only when needed, and bump `maxTokens` if truncation bites.
6. **Malformed edit tool calls** — small models hallucinate other harnesses'
   edit schemas (`edits` arrays, `replacement` fields, stringified JSON args).
   Mitigated with tool-call rules in the global `AGENTS.md`.
7. **Remaining 429s** — handled by the `groq-fallback` extension: predictive
   routing before the limit is hit, reactive fallback after, automatic return
   to Groq when the window clears.

## Files

### 1. `~/.pi/agent/models.json`

```json
{
  "providers": {
    "openrouter": {
      "models": [
        {
          "id": "z-ai/glm-5.2:nitro",
          "name": "GLM 5.2 Nitro (Groq fallback)",
          "reasoning": false,
          "input": ["text"],
          "contextWindow": 1048576,
          "maxTokens": 16384,
          "compat": {
            "supportsDeveloperRole": false
          }
        }
      ]
    },
    "groq": {
      "models": [
        {
          "id": "qwen/qwen3.6-27b",
          "name": "Qwen 3.6 27B (Groq)",
          "reasoning": true,
          "compat": {
            "supportsDeveloperRole": false
          },
          "thinkingLevelMap": {
            "off": "none",
            "minimal": null,
            "low": null,
            "medium": null,
            "high": "default",
            "xhigh": null,
            "max": null
          },
          "input": ["text", "image"],
          "contextWindow": 131072,
          "maxTokens": 8000
        }
      ]
    }
  }
}
```

Notes:
- `groq` and `openrouter` are built-in pi providers — these entries add custom
  models to them (baseUrl/API type inherited). API keys: `/login` in pi for
  each provider (stored in `~/.pi/agent/auth.json`).
- `thinkingLevelMap`: `null` = level unsupported (pi won't offer it), string =
  what to send as `reasoning_effort`. Net effect: two thinking settings, off
  and high.
- The `:nitro` suffix on the OpenRouter model = "sort hosts by throughput".
  `reasoning: false` because we want the fallback non-thinking.
- `maxTokens: 8000` on the Groq model must match `OTPM_RESERVATION` in the
  fallback extension (below). Trade-off dial: bigger = more room for thinking
  traces, smaller = more requests per minute (OTPM 32000 / maxTokens).
- models.json hot-reloads when you open `/model`.

### 2. `~/.pi/agent/AGENTS.md` — global instructions

```markdown
# Global instructions

## Tool call discipline

- The `edit` tool edits ONE file per call and takes exactly three top-level
  string parameters: `path`, `oldText`, `newText`. There is NO `edits` array,
  NO `replacement` field, and NO multi-edit batching. To change several
  places, make several separate `edit` calls.
- Tool arguments are a plain JSON object. NEVER encode arguments (or any
  parameter) as a JSON string — no stringified arrays, no `\n`-escaped blobs
  nested inside a string value.
- Keep each edit SMALL: match the few lines that change plus minimal
  surrounding context. Do not paste whole functions into `oldText` when only
  a few lines differ — large edits are where malformed or truncated tool
  calls happen.
- Before any `edit`, confirm the schema above; do not reuse edit-tool formats
  from other environments.
```

Loaded into every session (~200 tokens). Reduces (doesn't eliminate) the
malformed-edit retry loops; pi's validation errors remain the backstop.

### 3. `~/.pi/agent/extensions/strip-reasoning-replay.ts`

Stops pi from re-sending accumulated reasoning as input on every request.
Mechanism: pi's openai-completions serializer only replays thinking blocks
that carry a `thinkingSignature`; this hooks `message_end` and strips the
signature when each assistant message is finalized. Reasoning still streams
live in the TUI; it just doesn't ride along in future requests (this is also
how most big-model harnesses treat prior-turn thinking).

```typescript
/**
 * Strip reasoning from replayed context.
 *
 * Reasoning models (e.g. qwen3.6 on Groq) return their chain-of-thought in a
 * `reasoning` field. pi stores it as thinking blocks with a thinkingSignature,
 * and its OpenAI-completions serializer replays all accumulated thinking back
 * to the provider on every subsequent request — inflating input tokens and
 * burning TPM rate limits.
 *
 * The serializer only replays thinking when a block has a thinkingSignature.
 * This extension removes the signature when each assistant message is
 * finalized: the reasoning still streams and displays live in the TUI, but
 * past turns' reasoning silently drops out of future requests.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("message_end", async (event) => {
    if (event.message.role !== "assistant") return;
    const content = event.message.content;
    if (!Array.isArray(content)) return;

    let changed = false;
    const stripped = content.map((block: any) => {
      if (block && block.type === "thinking" && block.thinkingSignature) {
        changed = true;
        const { thinkingSignature, ...rest } = block;
        return rest;
      }
      return block;
    });

    if (!changed) return;
    return { message: { ...event.message, content: stripped } };
  });
}
```

To verify it's working: new assistant messages in the latest
`~/.pi/agent/sessions/**/*.jsonl` should have thinking blocks with **no**
`thinkingSignature` field.

### 4. `~/.pi/agent/extensions/groq-fallback.ts`

Three cooperating layers (full architecture in the file's header comment):

1. **Gauge** — wraps `globalThis.fetch` (pass-through) to read Groq's
   `x-ratelimit-remaining-tokens` / `x-ratelimit-reset-tokens` headers off
   every response, and timestamps every Groq chat request into a sliding 60s
   ledger. Needed because pi's `after_provider_response` extension hook is
   **never emitted by the openai-completions driver** (only
   anthropic/azure/bedrock) — headers are otherwise unreachable. The wrap
   works because pi builds its OpenAI SDK client without a custom `fetch`, so
   the SDK resolves the global one.
2. **Predictor** (`turn_start`) — before a request goes out: if the OTPM
   ledger says this would be one 8k reservation too many, or the TPM gauge
   says remaining tokens < (last input + allowances) × 1.2, switch to the
   OpenRouter fallback *before* the 429 happens.
3. **Reactive backstop** (`message_end`) — Groq 429s surface as assistant
   messages with `stopReason: "error"`; if the parsed "try again in Xs" wait
   is ≥ 20s, switch. (Shorter waits are deliberately left to pi's built-in
   2s/4s/8s retry — switching models for a sub-second window is churn.)

All switches notify in the TUI, schedule an automatic return to Groq when the
window clears, and any manual `/model` / Ctrl+P change cancels the automation
until the next trigger.

Tuning constants at the top of the file: `OTPM_LIMIT` / `OTPM_RESERVATION`
(keep in sync with your Groq tier and models.json `maxTokens`), `TPM_SAFETY`,
`MIN_REACTIVE_SWITCH_S`, allowances.

```typescript
/**
 * Groq rate-limit management: predictive routing + reactive fallback.
 *
 * Primary model: qwen3.6-27b on Groq (fast, but tight per-minute limits).
 * Fallback:      GLM 5.2 :nitro on OpenRouter (slower, effectively unlimited).
 *
 * Three cooperating layers:
 *
 * 1. GAUGE — pi's openai-completions driver never exposes response headers to
 *    extensions, but it uses the OpenAI SDK with the *global* fetch. We wrap
 *    globalThis.fetch (pass-through) and, for api.groq.com responses, read
 *    x-ratelimit-remaining-tokens / x-ratelimit-reset-tokens (TPM only — Groq
 *    has no OTPM header). The same wrapper timestamps every Groq chat request
 *    into a sliding 60s ledger for OTPM reservation accounting.
 *
 * 2. PREDICTOR — on turn_start, if the coming request would likely exceed the
 *    remaining TPM budget (gauge) or the OTPM reservation budget (ledger),
 *    switch to the fallback BEFORE hitting a 429, and schedule the return.
 *
 * 3. REACTIVE — message_end errors matching a Groq 429 with a wait >= 20s
 *    still trigger a switch (backstop for whatever the predictor misses;
 *    shorter waits are left to pi's built-in 2s/4s/8s retry).
 *
 * Manual model changes via /model or Ctrl+P cancel any pending switch-back
 * and stand the automation down until the next trigger.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PRIMARY = { provider: "groq", id: "qwen/qwen3.6-27b" };
const FALLBACK = { provider: "openrouter", id: "z-ai/glm-5.2:nitro" };

// Groq on-demand tier limits for qwen3.6-27b (org-level, per minute)
const OTPM_LIMIT = 32000;
const OTPM_RESERVATION = 8000; // must match maxTokens in models.json

const TPM_SAFETY = 1.2;        // switch when remaining < estimate * this
const GAUGE_MAX_AGE_MS = 120_000; // older gauge readings are ignored
const OUTPUT_ALLOWANCE = 2000; // typical non-thinking output, tokens
const GROWTH_ALLOWANCE = 4000; // context growth between turns, tokens

const MIN_REACTIVE_SWITCH_S = 20;
const DEFAULT_BACKOFF_S = 75;
const MAX_BACKOFF_S = 600;

// ─── Duration parsing ("7.66s", "275.52ms", "2m59.56s") ───
function parseDurationMs(s: string): number | null {
  const m = s.match(/(?:(\d+)m(?![s]))?\s*([\d.]+)\s*(ms|s)/i);
  if (!m) return null;
  const mins = m[1] ? parseInt(m[1], 10) : 0;
  let val = parseFloat(m[2]);
  if (m[3].toLowerCase() === "ms") val /= 1000;
  return (mins * 60 + val) * 1000;
}

// ─── Layer 1: gauge + ledger via global fetch wrap ───
const gauge = {
  remainingTokens: null as number | null,
  resetMs: null as number | null, // time until TPM budget fully restored
  updatedAt: 0,
};
const otpmLedger: number[] = []; // timestamps of Groq chat requests

function installFetchGauge() {
  const g = globalThis as any;
  if (g.__groqGaugeInstalled) return;
  g.__groqGaugeInstalled = true;
  const origFetch = g.fetch.bind(globalThis);
  g.fetch = async (input: any, init?: any) => {
    let isGroqChat = false;
    try {
      const url = typeof input === "string" ? input : input?.url ?? String(input);
      isGroqChat = url.includes("api.groq.com") && url.includes("/chat/completions");
      if (isGroqChat) otpmLedger.push(Date.now());
    } catch {}
    const res = await origFetch(input, init);
    if (isGroqChat) {
      try {
        const rem = res.headers.get("x-ratelimit-remaining-tokens");
        const reset = res.headers.get("x-ratelimit-reset-tokens");
        if (rem !== null) gauge.remainingTokens = parseInt(rem, 10);
        if (reset !== null) gauge.resetMs = parseDurationMs(reset);
        gauge.updatedAt = Date.now();
      } catch {}
    }
    return res;
  };
}

function otpmRequestsInWindow(): number {
  const cutoff = Date.now() - 60_000;
  while (otpmLedger.length && otpmLedger[0] < cutoff) otpmLedger.shift();
  return otpmLedger.length;
}

export default function (pi: ExtensionAPI) {
  installFetchGauge();

  let switchBackTimer: ReturnType<typeof setTimeout> | null = null;
  let autoSwitched = false;
  let internalChange = false;
  let lastInputTokens = 0; // input size of the most recent Groq request

  function cancelTimer() {
    if (switchBackTimer) {
      clearTimeout(switchBackTimer);
      switchBackTimer = null;
    }
  }

  async function setModelInternal(model: any): Promise<boolean> {
    internalChange = true;
    try {
      return await pi.setModel(model);
    } finally {
      internalChange = false;
    }
  }

  function onPrimary(ctx: any): boolean {
    const cur = ctx.model;
    return !!cur && cur.provider === PRIMARY.provider && cur.id === PRIMARY.id;
  }

  async function switchToFallback(ctx: any, reason: string, waitS: number): Promise<void> {
    const fb = ctx.modelRegistry.find(FALLBACK.provider, FALLBACK.id);
    if (!fb) {
      ctx.ui.notify(`Fallback model ${FALLBACK.provider}/${FALLBACK.id} not found in registry`, "error");
      return;
    }
    const ok = await setModelInternal(fb);
    if (!ok) {
      ctx.ui.notify(`Could not switch to ${fb.name}: no API key for ${FALLBACK.provider}`, "error");
      return;
    }
    autoSwitched = true;
    const clamped = Math.min(Math.max(Math.ceil(waitS), 10), MAX_BACKOFF_S);
    ctx.ui.notify(`${reason} — using ${fb.name} for ~${clamped}s`, "warning");

    cancelTimer();
    switchBackTimer = setTimeout(async () => {
      switchBackTimer = null;
      if (!autoSwitched) return;
      autoSwitched = false;
      const now = ctx.model;
      if (now && now.provider === FALLBACK.provider && now.id === FALLBACK.id) {
        const primary = ctx.modelRegistry.find(PRIMARY.provider, PRIMARY.id);
        if (primary && (await setModelInternal(primary))) {
          ctx.ui.notify(`Groq window elapsed — back to ${primary.name}`, "info");
        }
      }
    }, clamped * 1000);
  }

  // Track real usage numbers for the predictor
  pi.on("message_end", async (event: any, _ctx: any) => {
    const msg = event.message;
    if (msg?.role !== "assistant") return;
    if (msg.provider === PRIMARY.provider && msg.usage?.input) {
      lastInputTokens = msg.usage.input;
    }
  });

  // ─── Layer 2: predictive routing ───
  pi.on("turn_start", async (_event: any, ctx: any) => {
    if (!onPrimary(ctx) || autoSwitched) return;

    // OTPM: reservation accounting is exact — would this be one request too many?
    const used = otpmRequestsInWindow() * OTPM_RESERVATION;
    if (used + OTPM_RESERVATION > OTPM_LIMIT) {
      const oldestAge = (Date.now() - otpmLedger[0]) / 1000;
      const waitS = Math.max(65 - oldestAge, 10);
      await switchToFallback(ctx, `Groq OTPM budget full (${used}/${OTPM_LIMIT} reserved)`, waitS);
      return;
    }

    // TPM: header gauge, if fresh
    if (
      gauge.remainingTokens !== null &&
      Date.now() - gauge.updatedAt < GAUGE_MAX_AGE_MS &&
      lastInputTokens > 0
    ) {
      const estimate = lastInputTokens + GROWTH_ALLOWANCE + OUTPUT_ALLOWANCE;
      if (gauge.remainingTokens < estimate * TPM_SAFETY) {
        const waitS = gauge.resetMs !== null ? gauge.resetMs / 1000 : DEFAULT_BACKOFF_S;
        await switchToFallback(
          ctx,
          `Groq TPM low (${gauge.remainingTokens} left, next ≈${estimate})`,
          waitS,
        );
      }
    }
  });

  // ─── Layer 3: reactive 429 backstop ───
  pi.on("message_end", async (event: any, ctx: any) => {
    const msg = event.message;
    if (msg?.role !== "assistant" || msg.stopReason !== "error") return;
    const err = String(msg.errorMessage || "");
    if (!/429|rate.?limit/i.test(err)) return;
    if (!onPrimary(ctx) || autoSwitched) return;

    const waitMs = parseDurationMs(err.match(/try again in\s+(.+?)[.,\s]*(?:$|")/i)?.[1] ?? "") ?? DEFAULT_BACKOFF_S * 1000;
    const waitS = waitMs / 1000;
    if (waitS < MIN_REACTIVE_SWITCH_S) return; // pi's own retry will clear it
    await switchToFallback(ctx, `Groq rate-limited (~${Math.round(waitS)}s)`, waitS + 5);
  });

  // Manual model change = user takes over
  pi.on("model_select", (_event: any, _ctx: any) => {
    if (internalChange) return;
    cancelTimer();
    autoSwitched = false;
  });
}
```

## Recreating from scratch

1. Install pi, run it once, then `/login` for **groq** and **openrouter**.
2. Create the four files above under `~/.pi/agent/`
   (`models.json`, `AGENTS.md`, `extensions/strip-reasoning-replay.ts`,
   `extensions/groq-fallback.ts`).
3. Start a new pi session; select **Qwen 3.6 27B (Groq)** via `/model`.
4. Set thinking with Shift+Tab: **off** for routine agent work (faster, no
   truncation, lighter OTPM), **high** for hard problems.

## Daily-driving notes

- **Watch the notifications** — they say which layer fired (`OTPM budget
  full` / `TPM low` / `rate-limited`) and when it returns to Groq.
- **Context hygiene matters most.** "Get familiar with the repo" prompts make
  a small model read every file whole (~30% of context in one turn, re-sent
  every request). Prefer direct task prompts, `/compact` when context grows,
  fresh session per task.
- **Groq doesn't prompt-cache this model** — the whole context is re-billed
  against TPM every request. That's why replay-stripping and context hygiene
  matter more here than on Anthropic/OpenAI.
- **429s cost nothing** (rejected before processing, not billed) — the harm
  is stalled/failed turns, which is what the fallback removes.
- Extensions load at session start; models.json reloads on `/model`.

## Known limitations

- Pi has no built-in "fallback model on 429" or "don't replay reasoning"
  settings (checked 0.80.6 settings/compat docs and source) — hence the two
  extensions. Worth re-checking in future pi versions; both would be natural
  feature requests.
- The `after_provider_response` hook silently never fires for
  openai-completions providers in 0.80.6 — don't build on it for Groq; the
  fetch-wrap gauge is the workaround. If a pi update passes a custom fetch to
  the OpenAI SDK, the gauge goes dark (predictor inert) but the reactive
  layer keeps working.
- The OTPM ledger is per-process: two simultaneous pi sessions against the
  same Groq org will undercount (the org-wide TPM gauge stays honest).
- `strip-reasoning-replay` permanently removes reasoning from stored sessions
  (that's the point, but it's not recoverable).
