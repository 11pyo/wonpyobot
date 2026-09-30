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
| The scoring copy **shipped with the answer key** | If the system under test can read the answer, the score means nothing | Strip the key from the copy, and **test that it is stripped** |
| Failed runs were **averaged in as zeros** | A tooling fault is recorded as "the AI was wrong" | A failed run is **excluded, not zero**; report the count separately |
| A test copy of the server **kept opening browser tabs on the operator's screen** | Every test run piled up "connection refused" tabs | Start it the way the real server starts, with its side-effect-off option, and **check the server log** that it is off |

They share a shape: **almost all of them were silent failures.** Logs green, metrics normal, and nothing reaching the user. In an unattended system the expensive failure isn't stopping — it's **not knowing you stopped**.

So this design records heartbeats, desk-liveness, and *downtime* in the return report. When the operator comes back, *"no one asked"* and *"it was dead"* must be distinguishable.

---

## How far to trust it — measurement

To widen what an unattended agent may do you need **numbers**, not "it went well this week". This design logs every answer and moves the automation stage up on what a human reviewed. What that taught us:

**1. "20 correct in a row" is a speed gate, not a safety proof.**

Even a perfect streak says little when the sample is small. The 95% Wilson lower bound on accuracy:

| All-correct streak | 1 | 20 | 50 | 100 |
|---|---|---|---|---|
| Accuracy lower bound (95%) | 21% | 84% | 93% | 96% |

To claim at least 90% accuracy you need **35** error-free answers; for 95%, **73** (`n / (n + 3.84)`). So the stage threshold gets the lower bound printed next to it, and passing the threshold alone never opens a permission. `reference/trust_bound.py` reproduces these numbers.

**2. Each stage measures a different population.**

Counting "answers to simple questions" and "classifications of development requests" in one pool tells you nothing about either. Each stage fixes its population by **inquiry type and verdict kind** and counts only inside it.

**3. When samples are scarce, replay the past instead of waiting for live traffic.**

Past inquiries are re-run under the **same prompt, tools and isolation** and scored (replay evaluation).

- The system under test sees only a **knowledge copy with the answer key removed** and a **dummy intake server** — production records are out of reach
- A **separate, tool-less LLM** scores **rubric items only**, blind to which version wrote the answer. Caps ("at most N points if ..."), penalties and totals are computed **in code** — the LLM never does the arithmetic
- A run that fails is **excluded, not scored zero**, and the count is reported separately. "Produced no answer at all" does score zero
- Scores from different case sets are **never compared** (checked by a set fingerprint); the difference between two versions is read from a paired-bootstrap interval
- The judge is under test too — its agreement with earlier grades is compared with **how much the earlier grader disagreed with itself** on the same answers

The tool is entangled with company material, so it is not in this repo. Only the **method** is written down; whoever rebuilds it fills the case set and rubric from their own organisation.

---

## Reference implementation

`reference/demo.py` is a runnable minimum — intake server, watcher, and write gate in one file, with fictional data. It is a fresh implementation of the same structure, not the production code.

```bash
cd reference
python demo.py            # http://127.0.0.1:8710
```

The LLM call is isolated in `answer_fn()`. Swap in your model there; it still cannot write.

`reference/rules.sample.md` is a template for the delegation-scope document. **That document is the source of truth for the system** — not the code.

`reference/trust_bound.py` reproduces the table in the measurement section (standard library only).

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
- **The samples are small.** 20 error-free answers only supports "at least ~84% accurate". Clearing a stage threshold is not the same as being safe.

---

## License

MIT. Take it — but **write the delegation-scope document first.** That comes before the code.
