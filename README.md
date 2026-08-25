# Moodle ESPAM Skill

Fetch courses, pending tasks, assignment details, and files from
`evirtual.espam.edu.ec`.

## ESPAM-specific compatibility

This skill is implemented for ESPAM's Moodle configuration and its associated
authentication flow. It may not work with Moodle installations at other
organizations without adapting the login, API, and page-parsing logic.

## Requirements

- Python 3.10 or later
- An authorized ESPAM Moodle account
- Network access to Moodle
- Optional: OpenClaw or Hermes for new-task notifications

This skill has no third-party Python dependencies.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with your account and notification values.
source .env
```

The skill reads exported variables first, then `.env` in the skill directory.
When installed under OpenClaw or Hermes, it also accepts the host-level `.env`.

```bash
ESPAM_STS_USER="YOUR_MOODLE_USERNAME"
ESPAM_STS_PASSWORD="YOUR_MOODLE_PASSWORD"
```

Never commit `.env`, cookies, session data, task data, or downloaded files.

## Usage

```bash
python3 scripts/moodle_tasks.py --oneline
python3 scripts/list_courses_inprogress.py
python3 scripts/moodle_task_detail.py <assignment-url> --json
python3 scripts/moodle_check_new.py
```

For notification delivery, set `MOODLE_NOTIFICATION_PROVIDER` to `openclaw`
or `hermes`, plus `MOODLE_NOTIFICATION_CHANNEL` and
`MOODLE_NOTIFICATION_TARGET`.

See `SKILL.md` for agent-specific usage.
