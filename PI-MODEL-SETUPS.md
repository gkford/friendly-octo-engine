# Pi model setups — cheap/fast coding-agent configs (Groq + Makora)

Configs for running the [pi coding agent](https://github.com/earendil-works/pi)
on cheap, fast hosted models. Three daily-drivable setups live in
`~/.pi/agent/models.json`, all selectable via `/model`:

## The three setups at a glance

Per model **and reasoning level** — intelligence, verbosity, and what that
means in wall-clock and dollars. "Index tokens" = output tokens Artificial
Analysis measured (or we estimate) to run their full Intelligence Index;
"wall-clock" = those tokens at our measured decode TPS; "cost" = AA's
measured cost to run the index at that endpoint's prices.

| Setup + thinking level | Intelligence (AA) | Index tokens | TPS (ours) | Wall-clock, full AA eval | Cost, full AA eval |
|---|---|---|---|---|---|
| Groq Qwen 3.6 27B — **off** | 30 | 25M | ~490 | **~14 h** | $356 |
| Groq Qwen 3.6 27B — **thinking** | 37 | 140M | ~490 | ~79 h | $668 |
| Flash (Makora) — **off** | 29 | not published (~10–25M est.) | 272 | ~10–26 h est. | ~$15–35 est. |
| Flash (Makora) — **high** | 37 | not published (est. ~60–90M; our n=1 hard task: 36k tokens, 92% thinking) | 272 | ~60–90 h est. | ~$40–60 est. |
| Flash (Makora) — **max** | 40 | 230M | 272 | ~235 h | ~$62 (at Makora prices; $74 first-party) |
| Kimi K2.7 Code (Makora) — **thinking** | 42 | 100M | 207 | ~134 h | ~$445 (at Makora prices) |

Setup-level properties:

| | **Groq Qwen 3.6 27B** | **DeepSeek V4 Flash (Makora)** | **Kimi K2.7 Code (Makora)** |
|---|---|---|---|
| Price per 1M in / out | $0.60 / $3.00 | $0.096 / $0.237 | $0.816 / $3.383 |
| Cached input per 1M | no cache discount on Groq | $0.0723 (25% off) | $0.1615 (80% off) |
| Thinking levels (Shift+Tab) | off, high | off, high, max | off, high |
| Rate limits | tight (250k TPM, 32k OTPM/min) — needs the fallback extension | none hit yet (pay-as-you-go) | none hit yet (pay-as-you-go) |
| Prompt caching | none — replay stripping essential | priced (25% off) but `cached_tokens` always 0 in our tests so far | priced (80% off) — makes preserved reasoning cheap to replay |
| Reasoning replay | stripped by extension | preserved (V4 requires it in tool-call turns) | preserved (Kimi is trained for it) |
| Sweet spot | fastest raw decode for routine work | ~8× cheapest; max mode = budget smart model | smartest; best wall-clock among smart models (least verbose reasoner) |

Notes on the per-level rows:
- Qwen thinking-mode numbers are AA's (intelligence 37, 140M tokens, $668) —
  thinking costs 5.6× the tokens and ~2× the money of Qwen-off for +7 points.
- Flash **high** verbosity is unpublished; the 60–90M estimate scales AA's
  230M max-effort figure by our measured high:max ratio on one hard prompt
  (36.4k vs ≥100k truncated — so ≥2.7×, likely more). Treat as rough.
- Flash **max** is unbounded thinking: our hard-prompt test burned 100k
  tokens without finishing its reasoning. Budget wall-clock accordingly.
- Same-intelligence-tier comparison (37): Qwen-thinking ~79 h/$668 vs
  Flash-high ~60–90 h/~$50 — similar wall-clock, ~13× cheaper on Flash.

Rule of thumb: **Flash-off for routine work** (Qwen-level intelligence at a
tenth of the cost), **Kimi for hard problems** (intelligence 42, and the best
wall-clock per task among the smart models), **Flash-max when the hard problem
can wait** (intelligence 40 at ~$0.13 blended, but its thinking is very
verbose — ~2.3× Kimi's tokens per task). Groq Qwen remains the pure-speed
option when per-request latency is king.

## Switching between the three modes

`~/.pi/agent/settings.json` wires the three working modes into a **Ctrl+P
cycle** (each entry carries its own thinking level, so cycling models also
sets the right effort — no separate Shift+Tab needed):

```json
{
  "defaultProvider": "makora",
  "defaultModel": "deepseek-ai/DeepSeek-V4-Flash",
  "defaultThinkingLevel": "high",
  "enabledModels": [
    "makora/deepseek-ai/DeepSeek-V4-Flash:high",
    "makora/moonshotai/Kimi-K2.7-Code:high",
    "groq/qwen/qwen3.6-27b:off"
  ]
}
```

- **Default on startup: Flash high** (the ~13×-cheaper equal-intelligence
  replacement for Qwen-thinking).
- **Ctrl+P** → Kimi thinking (extra oomph, still fast per task).
- **Ctrl+P again** → Groq Qwen no-thinking (max TPS for trivial edits).
- **Shift+Tab** still escalates within a model (e.g. Flash high → max for a
  hard problem — but note max is unbounded thinking; see table notes).
- `/model` and `/scoped-models` remain available for anything outside the
  cycle. Manual switches stand down the groq-fallback automation as before.

The rest of this doc: first the original Groq setup (whose rate-limit
machinery motivated most of the extensions), then the Makora additions.

# Setup 1: Pi + Groq (Qwen 3.6 27B) — with rate-limit fallback

Runs Groq's `qwen/qwen3.6-27b` (very fast, but tight per-minute rate limits) with
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
    "makora": {
      "baseUrl": "https://inference.makora.com/v1",
      "api": "openai-completions",
      "apiKey": "$MAKORA_API_KEY",
      "compat": {
        "supportsDeveloperRole": false
      },
      "models": [
        {
          "id": "deepseek-ai/DeepSeek-V4-Flash",
          "name": "DeepSeek V4 Flash (Makora)",
          "reasoning": true,
          "thinkingLevelMap": {
            "off": "none",
            "minimal": null,
            "low": null,
            "medium": null,
            "high": "high",
            "xhigh": null,
            "max": "max"
          },
          "input": ["text"],
          "contextWindow": 1000000,
          "maxTokens": 131072,
          "cost": { "input": 0.096, "output": 0.237, "cacheRead": 0.0723, "cacheWrite": 0.096 }
        },
        {
          "id": "moonshotai/Kimi-K2.7-Code",
          "name": "Kimi K2.7 Code (Makora)",
          "reasoning": true,
          "thinkingLevelMap": {
            "off": "none",
            "minimal": null,
            "low": null,
            "medium": null,
            "high": "enabled",
            "xhigh": null,
            "max": null
          },
          "input": ["text"],
          "contextWindow": 262144,
          "maxTokens": 32000,
          "cost": { "input": 0.8155, "output": 3.383, "cacheRead": 0.1615, "cacheWrite": 0.8155 }
        }
      ]
    },
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
  each provider (stored in `~/.pi/agent/auth.json`). `makora` is a fully
  custom provider — its key comes from `$MAKORA_API_KEY`, not `/login`.
- `thinkingLevelMap`: `null` = level unsupported (pi won't offer it), string =
  what to send as `reasoning_effort`. Net effect: off/high on Qwen and Kimi,
  off/high/max on Flash.
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

Stops pi from re-sending accumulated reasoning as input on every request —
for models where dropping it is safe (Qwen-on-Groq); Kimi and DeepSeek V4 are
exempted because they are trained/required to reason over preserved thinking
(see the header comment).
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
 *
 * Exceptions — models trained to reason over preserved thinking:
 * - Kimi K2.7 Code: Moonshot's API forces preserve_thinking on.
 * - DeepSeek V4 (Flash/Pro): in tool-calling conversations the docs REQUIRE
 *   reasoning_content to be passed back (first-party API 400s without it);
 *   V4 preserves reasoning across turns whenever tool calls are present.
 *   https://api-docs.deepseek.com/guides/thinking_mode/
 * Stripping would degrade (or break) their multi-turn agentic quality, so
 * their messages are left untouched. Qwen-on-Groq etc. are still stripped.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PRESERVE_THINKING = /kimi|deepseek/i; // matched against the message's model id

export default function (pi: ExtensionAPI) {
  pi.on("message_end", async (event) => {
    if (event.message.role !== "assistant") return;
    if (PRESERVE_THINKING.test((event.message as any).model ?? "")) return;
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

# Setups 2 & 3: Makora — DeepSeek V4 Flash + Kimi K2.7 Code (added 2026-07-12)

`models.json` also registers **Makora** (`https://inference.makora.com/v1`,
OpenAI-completions, auth via `$MAKORA_API_KEY` exported in `~/.bashrc`) with
two models, both speed-verified against our own key:

- **DeepSeek V4 Flash** — measured 272 tok/s decode, $0.096/$0.237 per 1M
  (cached input $0.0723 — only a 25% discount on Makora, nothing like the 98%
  DeepSeek first-party offers, but uncached input is already near-free).
  Reasoning levels wired as off→`none`, high→`high`, max→`max`
  (Shift+Tab cycles all three). 1M context, `maxTokens: 131072` (thinking on
  max effort can run very long — vLLM's recipe wants ≥384k headroom, and a
  truncated thought wastes the whole request; 32k proved too tight).
  Default to **high** effort: DeepSeek's own docs default to it, and max buys
  +3 AA-index points for ~2.6× the thinking tokens.
- **Kimi K2.7 Code** — measured 207 tok/s, $0.8155/$3.383 per 1M
  (cached input $0.1615 — an 80% discount, so replayed context including its
  preserved reasoning is cheap once caching kicks in). Makora exposes
  only `enabled` as a reasoning level (plus `none` accepted empirically), so
  the map is off→`none`, high→`enabled`.

Notes:
- `strip-reasoning-replay` now **skips Kimi and DeepSeek models** (regex on
  the message's `model` id). Kimi: Moonshot trains K2.7 over preserved
  thinking and forces `preserve_thinking` on. DeepSeek V4: the R1/V3-era
  "don't send reasoning back" rule is REVERSED for tool-calling conversations
  — reasoning_content must be passed back (first-party API 400s without it;
  https://api-docs.deepseek.com/guides/thinking_mode/). Qwen-on-Groq is
  still stripped.
- DeepSeek V4 gotchas (from vLLM/community issue trackers, 2026-07): don't
  set temperature (ignored in thinking mode; V4 wants 1.0 anyway); avoid tool
  parameters literally named `arguments` or `input` (vLLM's deepseek_v4 tool
  parser mangles them); don't rely on json_mode/structured outputs while
  thinking is on (known vLLM bug). Long-context quality: solid to ~256k
  (MRCR >0.82), degrades beyond — `/compact` around 200–256k, not at 1M.
- The `groq-fallback` extension is inert on Makora models (it checks the
  active model is the Groq primary before acting) — no changes needed.
- Makora rate limits: free tier is ~1 request/window; pay-as-you-go lifts it.
- Makora publishes cached-input pricing (Flash $0.0723, Kimi $0.1615 per 1M),
  but in our tests `prompt_tokens_details.cached_tokens` has only ever been 0
  — cache *hits* are unverified in practice. Keep prompts prefix-stable and
  watch that field in real sessions; until hits appear, budget at uncached
  rates. No cache discount at all on Groq.
- Makora's marketing TPS numbers are batch throughput, not per-request: their
  Qwen endpoints measured 35–39 tok/s single-stream despite a "609 TPS" claim.
  The two models above are their genuinely fast endpoints (AA-verified too).

## Recreating from scratch

1. Install pi, run it once, then `/login` for **groq** and **openrouter**.
2. Export `MAKORA_API_KEY` in `~/.bashrc` (Makora auth is via env var in
   models.json, not `/login`).
3. Create the four files above under `~/.pi/agent/`
   (`models.json`, `AGENTS.md`, `extensions/strip-reasoning-replay.ts`,
   `extensions/groq-fallback.ts`), plus the `defaultModel`/`enabledModels`
   keys in `~/.pi/agent/settings.json` (see "Switching between the three
   modes" at the top).
4. Start a new pi session — it opens on Flash-high; **Ctrl+P** cycles
   Flash-high → Kimi-thinking → Qwen-off.
5. Shift+Tab escalates within a model when needed (e.g. Flash high → max for
   a hard problem; on Groq keep **off** for routine work — faster, no
   truncation, lighter OTPM).

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
