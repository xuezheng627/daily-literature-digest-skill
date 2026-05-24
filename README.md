# Daily Literature Digest Skill

A Codex skill for setting up a personal daily AI literature digest. It monitors Crossref, OpenAlex, and arXiv for new papers matching your research keywords, summarizes open metadata and abstracts, saves a local Markdown archive, and sends a daily Gmail digest.

## What It Does

- Runs every day at your chosen local time, defaulting to 09:00.
- Searches Elsevier, Springer Nature, Wiley, Taylor & Francis/Routledge, and arXiv by default.
- Treats keyword matching inclusively: one configured keyword term is enough to include a paper.
- Summarizes only title, abstract, keywords, subjects, DOI, journal, author, source, and open metadata during unattended daily runs.
- Writes local archives under `daily-literature-digests/`.
- Sends the concise digest through the Codex Gmail connector when Gmail is connected.
- Creates a follow-up list for papers without abstracts, so you can manually log in or provide PDFs for explicit full-text follow-up.

It does not store passwords, log in to publisher websites, bypass paywalls, or run unattended publisher downloads.

## Install

Clone or download this repository, then copy the skill folder into your Codex skills directory.

Windows PowerShell:

```powershell
$skills = "$env:USERPROFILE\.codex\skills"
New-Item -ItemType Directory -Path $skills -Force | Out-Null
Copy-Item -Recurse -Force .\daily-literature-digest "$skills\daily-literature-digest"
```

macOS/Linux:

```bash
mkdir -p ~/.codex/skills
cp -R ./daily-literature-digest ~/.codex/skills/daily-literature-digest
```

Restart Codex if it does not immediately discover the new skill.

## Connect Gmail

In Codex, connect the Gmail app/connector for the account that should send the digest. The skill uses Gmail connector tools only; it does not ask for SMTP passwords or Gmail app passwords.

## Create Your Daily Digest

After installing the skill and connecting Gmail, ask Codex something like:

```text
Use $daily-literature-digest to create a daily literature digest.
Send it to me@example.com every day at 09:00.
Use English.
My keywords are:
- multi-objective optimization
- surrogate-assisted optimization
- reinforcement learning
- building energy
- HVAC control
```

Codex will create a workspace config named `daily-literature-digest.config.json`, run a dry fetch, generate the first digest, send a test/daily email if Gmail is connected, and create the recurring automation.

## Generated Workspace Files

The skill creates runtime files in the user's own workspace, not inside this repository:

```text
daily-literature-digest.config.json
daily-literature-digests/data/YYYY-MM-DDTHHMMSSZ.json
daily-literature-digests/YYYY-MM-DD.md
daily-literature-digests/fulltext-inbox/to-download-YYYY-MM-DD.md
daily-literature-digests/fulltext-summaries/
daily-literature-digests/state.json
```

Do not commit your generated config, state, emails, downloaded PDFs, or full-text notes if they contain private research interests or institutional access traces.

## Automation Caveat

Codex local automations depend on your local Codex runner/environment. If your computer is asleep, shut down, offline, or Codex's local automation runner is not active at 09:00, the digest may not run until the environment is available again.

## Full-Text Follow-Up

The unattended daily digest is intentionally abstract/open-metadata based. For papers with no abstract or restricted full text:

1. Open the follow-up list in `daily-literature-digests/fulltext-inbox/`.
2. Log in to your university or publisher access yourself in the active browser.
3. Tell Codex to process that explicit batch.

Codex can then summarize accessible pages or PDFs you provide, but it should not save passwords, cookies, or create unattended download automation.

## License

MIT
