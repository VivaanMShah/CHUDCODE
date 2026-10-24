# CLAUDE.md — Hackathon mode

This repo is a 24-hour hackathon project. The goal is a working demo judges can see in 2-3 minutes, not production software. Optimize every decision for that.

## Priorities (in order)

1. **The demo works.** A feature that isn't demoable doesn't exist.
2. **Speed.** Ship the simplest thing that works. Polish later, only if time allows.
3. **Reliability during the demo.** It must not crash in front of judges.
4. **Code quality.** Last. Readable is enough.

## How to work

- Build the happy path first. Handle the error cases that could break the demo; skip the rest.
- Don't over-engineer. No abstractions, design patterns, or config systems unless they save time right now.
- Prefer libraries and APIs over writing things from scratch.
- Use the stack the team already knows. Don't introduce a new framework mid-hackathon.
- Hardcoding is fine: sample data, demo users, fixed config. Put it in one obvious place so it's easy to swap.
- Mock or fake anything slow, flaky, paid, or blocked (APIs, auth, payments, bank data). Keep a toggle so the demo can run fully offline if wifi dies.
- Make small, working changes. Don't do large refactors, especially after hour 16.
- When there are two ways to do something, pick the faster one and say why in one line.
- If a task will take more than ~30 minutes, say so before starting and suggest a cheaper version.
- Don't write tests unless asked. A quick manual check or a tiny script that runs the core path is enough.
- Ask before deleting files, rewriting large chunks of working code, or changing the project structure.

## Code conventions

- Keep files small and named by what they do (`news_fetch.py`, `Dashboard.tsx`).
- Comments only where the logic isn't obvious.
- Log clearly when something fails so we can debug fast at 3 AM.
- Pin dependency versions. Update `requirements.txt` / `package.json` whenever you add one.

## Secrets

- API keys go in `.env`, never in code. Keep `.env` in `.gitignore`.
- Maintain a `.env.example` with every key name and no values.
- Never print keys in logs or commit them. If one gets committed, tell me immediately so I can rotate it.

## Git

- Commit after every working step with a short, clear message.
- Never leave `main` broken. If something risky is in progress, use a branch.
- Before any big change, make sure the current working state is committed so we can roll back.

## Demo safety

- Seed data must make the demo look good and run the same way every time.
- Cache or save API responses used in the demo so a rate limit or outage can't kill it.
- Show loading states instead of a frozen screen.
- Hide or remove anything half-built before judging.

## Time phases

- **Hours 0-1:** Scope, repo setup, skeleton that runs.
- **Hours 1-8:** Each piece works on its own with fake data.
- **Hours 8-14:** Wire everything together end to end.
- **Hours 14-20:** Fix bugs, then at most one stretch feature.
- **Hours 20-24:** Code freeze. Bug fixes only. Record a backup demo video, write the README, practice the pitch.

If I ask for a new feature after hour 20, remind me we're in code freeze and ask if I'm sure.

## README (for Devpost and judges)

Keep it updated. It needs:
- One-line description of the project
- The problem and who it's for
- How it works (short)
- Tech stack
- How to run it locally
- What was built during the hackathon vs. what was pre-existing (libraries, datasets, starter code). Be honest about this.

## Project details

<!-- Fill this in once the idea is locked -->
- **Project:**
- **One-liner:**
- **Stack:**
- **Run command:**
- **Team / who owns what:**