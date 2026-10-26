# Running the race planner on a Grok Bot

A [Grok Bot](https://docs.x.ai/grok-bot/overview) has its own persistent cloud computer with a
terminal, a browser and a `/workspace` folder that survives restarts. That is enough to run the
race planner once a week and send you the report. This folder holds what the Bot needs:

| File | What it does |
|---|---|
| `bootstrap.sh` | Clone or update the repo, (re)install the two packages, load the seed, run `doctor`. Safe to rerun. |
| `run_weekly.sh` | `bootstrap.sh`, then `python -m tri_coach.race_planner run`. Writes `reports/latest.md`. |

Layout on the Bot's computer:

```text
/workspace/tri-coach/            the repo checkout (+ .venv)
/workspace/tri-coach-data/       TRI_RACE_PLANNER_DIR: database, page snapshots, seed_self.json, reports/
```

## Before you start

- **Privacy.** `seed_self.json` holds your race history, results and plans. Every Bot on your
  account shares the same computer, so any of them can read it. Leave out anything you don't want
  there; the planner only needs the `athlete.profile` block (gender, age group, home city,
  `planner` settings) and the races on your calendar.
- **Branch.** The bootstrap clones `main`. Until the race planner is merged, set
  `TRI_COACH_BRANCH` to the branch that has it (and that branch has to be pushed).
- **Reachability.** The Bot's computer is outside mainland China. Run `doctor` first: it says
  whether zuicool, chinatriathlon and wildto answer from there. ctsa.org.cn usually does not; the
  planner does not need it yet.

## One-time setup (in a chat with the Bot)

1. Ask the Bot to run (replace `main` with the branch if the planner is not merged yet):

   ```bash
   git clone --branch main https://github.com/SweetGingerGreen/tri-coach.git /workspace/tri-coach
   ```

   ```bash
   TRI_COACH_BRANCH=main bash /workspace/tri-coach/deploy/grok-bot/bootstrap.sh
   ```

   If you used a branch, put the same `TRI_COACH_BRANCH=…` in front of the command in the skill
   below, or the next update switches the checkout back to `main`.

2. Upload your seed (attach the file in the chat) and ask the Bot to save it as
   `/workspace/tri-coach-data/seed_self.json`. Start from `examples/race_planner/seed_example.json`
   if you don't have one.
3. Run the bootstrap again so it loads the seed, and read the `doctor` output. Every `reach` line
   for zuicool, chinatriathlon and wildto should say `ok`.
4. Do a first run and check the report:

   ```bash
   bash /workspace/tri-coach/deploy/grok-bot/run_weekly.sh
   ```

5. Save the skill and create the routine below.

## Skill: "Race planner weekly run"

Paste this into the chat and ask the Bot to save it as a skill:

> **When to use:** every Monday morning, or when I ask to "update the race plan".
>
> **Inputs and access:** the repo at `/workspace/tri-coach`, data at `/workspace/tri-coach-data`.
> No logins and no secrets are needed.
>
> **Steps:**
> 1. Run `bash /workspace/tri-coach/deploy/grok-bot/run_weekly.sh` in the terminal.
> 2. If it exits non-zero, read the `[bootstrap]` and `doctor` lines, tell me which check failed,
>    and stop. Do not try other websites or work around a blocked site.
> 3. Read `/workspace/tri-coach-data/reports/latest.md` and `latest.json`.
>
> **What to return:** a short message in Chinese with (a) the "这次有什么变化" items,
> (b) registration deadlines in the next three weeks, (c) the races under "建议加上的", and
> (d) how many prize tables are still unverified. Attach `latest.md`.
>
> **Checks:** the report's date line must be today's date; if it isn't, say so.
>
> **Never, without asking me first:** register for a race or pay anything; edit
> `seed_self.json` or the database; run `verify`; send the report anywhere except this chat;
> commit or push to git; fetch pages beyond what the planner itself requests.

## Routine

In the same Bot's chat:

> Every Monday at 08:00 Asia/Shanghai, run the "Race planner weekly run" skill and post the result here.
> If a run fails, post the failing check instead of retrying.

## If the Chinese sites don't answer from the Bot

The planner can also run on a machine inside China (your Mac) and the Bot only reads the result:
crawl there with `python3 -m tri_coach.race_planner run`, put the data folder somewhere the Bot can
read, and change step 1 of the skill to `python -m tri_coach.race_planner run --no-crawl`, which
rebuilds the report from the database without fetching anything.
