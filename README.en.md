# Wonpyobot — an agent that keeps the help desk open while the only operator is away

> **How a one-person support function stays reachable when that person isn't.**
> An intake page, an event-driven watcher, and — the part that matters — **a write gate**.
> This repo is an **anonymized reference implementation** and a set of design notes. No real company data.

**English** · [한국어](README.md)

---

## The problem

When one person *is* the support function, their absence is a structural hole. Requests stop being answered, users don't know when an answer is coming, and the operator returns to a pile.

Wonpyobot does **not** try to fill that hole completely. It splits it:

| Case | Who handles it |
|---|---|
| Acknowledgement · questions the knowledge base already settles | **The bot**, within minutes |
| Anything where judgment could go either way | **Parked** for the operator |
| Business-stopping urgency | **Escalated to a named human** |

The axis of this design is not *"AI handles it"* — it's **"the boundary of what AI handles is written down, in a document, before any code."**

---

## Architecture

```
user browser ──▶ intake server ──▶ append-only log
                                        │
                                 (file change)
                                        ▼
                                    watcher ──▶ LLM ──▶ verdict JSON
                                        │                    │
                                        └──▶ write gate ◀────┘
                                                  │
                                     (labels · verifies · records)
                                                  ▼
                                            append-only log
```

### Three decisions that carry the design

**1. The agent gets no write access.**

Tell an LLM to "record the answer" and one small deviation in how it forms the command trips the permission check — it researches perfectly and then **stops silently**. That happened. The investigation was right; nothing was written; nobody knew.

→ The agent returns **a verdict JSON only.** One gate touches the file.

**2. The gate enforces the rules the agent might forget.**

- The **AI disclosure label is attached automatically** and cannot be dropped
- Internal notes **cannot reach the answer field** — they travel a different path entirely
- The AI **never closes a request as “done”** → everything lands in *pending*; a human closes it
- After writing, it **re-reads to confirm** — a success response is not proof of effect
- It **renders what the user will actually see** and validates that

**3. Level-triggered, not polling.**

It doesn't remember how far it got. Every pass **recomputes from the whole file**.

- Down for three hours? On restart it picks up everything that queued
- Offset-tracking loses a record forever if you read mid-write
- Idle stretches never call the LLM → **zero cost while waiting**

---

## What it cost to learn

The real content of this repo is this table. Every row actually happened.

| Failure | Why it's dangerous | Fix |
|---|---|---|
| Agent's write permission rejected on a command-shape mismatch | Researches, then **silence**. No signal at all | Remove write access; route through the gate |
| A safety check ("if output shrinks >30%, substitute a standard message") **overwrote an entire conversation** | The safety check destroyed data | Validate **only the newly written turn** |
| Answer appended over an acknowledgement **lost the mandatory label** | User reads it as a confirmed human answer | Append only onto a real prior answer |
| Daemon rewrote the whole state file, **undoing "stop"** | You turn it off and it keeps running | Give each process **ownership of specific fields** |
| Server died quietly while the watcher stayed healthy | Intake is impossible but every indicator is green | Watcher **checks the desk is alive and revives it** |
| Two processes **bound the same port** (Windows) | The old process keeps serving old code | `allow_reuse_address = False` |
| A syntax error in served JS **killed the whole page** | No button or tab responds | **Syntax-check the served bundle** on every deploy |

They share a shape: **almost all of them were silent failures.** Logs green, metrics normal, and nothing reaching the user. In an unattended system the expensive failure isn't stopping — it's **not knowing you stopped**.

So this design records heartbeats, desk-liveness, and *downtime* in the return report. When the operator comes back, *"no one asked"* and *"it was dead"* must be distinguishable.

---

## Reference implementation

`reference/demo.py` is a runnable minimum — intake server, watcher, and write gate in one file, with fictional data. It is a fresh implementation of the same structure, not the production code.

```bash
cd reference
python demo.py            # http://127.0.0.1:8710
```

The LLM call is isolated in `answer_fn()`. Swap in your model there; it still cannot write.

`reference/rules.sample.md` is a template for the delegation-scope document. **That document is the source of truth for the system** — not the code.

---

## Related repositories

| | |
|---|---|
| **[Third-Party-Brain](https://github.com/11pyo/Third-Party-Brain)** | Where the bot looks things up. An **AI-searchable ops knowledge archive in one HTML file** — a method for surviving a key person's exit |
| **[ai-collab-dashboard](https://github.com/11pyo/ai-collab-dashboard)** | Where the bot writes. A backend-free, **concurrency-safe ops dashboard** — kanban plus an append-only id-merge log |

The three are one set: **the archive holds knowledge, the dashboard holds the record, and Wonpyobot answers the door.**

---

## Honest limits

- **Your PC is the server.** If it sleeps or reboots, intake stops. Keep an existing channel open alongside it.
- **Identity is a name match.** There's no login. It assumes an internal network, and the real defence is not putting sensitive data in answers.
- **Out-of-scope requests pile up.** For a long absence you need **a human backup**. No tool closes that gap.

---

## License

MIT. Take it — but **write the delegation-scope document first.** That comes before the code.
