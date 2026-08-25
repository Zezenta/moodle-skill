---
name: moodle
description: Fetch tasks, deadlines, course info, and task details from ESPAM Moodle (evirtual.espam.edu.ec). Use for pending assignments, upcoming deadlines, courses, task content, or other Moodle requests.
compatibility: Python 3.10+, outbound access to evirtual.espam.edu.ec, and ESPAM_STS_USER / ESPAM_STS_PASSWORD set. Notifications additionally need OpenClaw or Hermes.
---

# Moodle Integration (ESPAM)

Fetch courses, tasks, and assignment details from a Moodle account. Authentication is handled automatically.

## When to Use

Use this skill when the user asks about ESPAM Moodle:

- Pending assignments, deadlines, or overdue work.
- Active courses and their progress.
- An assignment's instructions, submission state, grade, or attachments.
- A scheduled check for newly posted tasks.

Do not use this skill for Academico grades or attendance; use the Academico
skill for those requests.

## Credentials and Safety Rules

Set `ESPAM_STS_USER` and `ESPAM_STS_PASSWORD`. Never print, return, save, or
commit these values. Do not ask the user to paste credentials into chat.

Treat `cookies.lwp`, `session.json`, `tasks.json`, `moodle_known.json`, and
downloaded files as private generated data. Do not version them or attach them
to a response unless the user explicitly requests a specific downloaded file.

## Procedure

1. Identify whether the user needs a task list, active courses, a task detail,
   attachments, or a new-task check.
2. Confirm the required credential variables are available. Authentication is
   performed automatically by the scripts.
3. Use `moodle_tasks.py` for pending-task and deadline questions. Prefer
   `--oneline` for a compact summary.
4. Use `list_courses_inprogress.py` for active-course questions.
5. Use `moodle_task_detail.py <URL> --json` only with the relevant Moodle task
   URL. Download attachments only when the user explicitly asks for them.
6. Use `moodle_check_new.py` for scheduled monitoring only after a notification
   provider and target are configured.
7. Summarize only the information relevant to the request.

## Scripts

Run these commands from this skill's directory.

### List pending tasks

```bash
python3 scripts/moodle_tasks.py           # Detailed output
python3 scripts/moodle_tasks.py --oneline # One line per task
```

Results also saved to `scripts/tasks.json`.

### Read task content

```bash
python3 scripts/moodle_task_detail.py <URL>
python3 scripts/moodle_task_detail.py <URL> --json
```

Outputs JSON with: title, description/instructions, due date, submission status, grades, attached files.

**Resource links:** If the task description contains links to Moodle resources (presentations, PDFs referenced via `/mod/resource/view.php`), the script automatically resolves them to their actual file URLs (`pluginfile.php`). The output includes both the original link and the resolved URL with the filename.

**Download files:**

```bash
python3 scripts/moodle_task_detail.py <URL> --download [--download-dir DIR]
```

Downloads all files attached to the task (including resolved resource links). Default directory: `downloads/`. Each file entry gets a `local_path` field on success or `download_error` on failure.

### List courses in progress

```bash
python3 scripts/list_courses_inprogress.py
python3 scripts/list_courses_inprogress.py --json
```

Shows course id, name, progress %, and category.

### Notify about newly posted tasks

```bash
python3 scripts/moodle_check_new.py
```

Set `MOODLE_NOTIFICATION_PROVIDER` to `openclaw` or `hermes`, and set
`MOODLE_NOTIFICATION_TARGET` to the destination. The optional
`MOODLE_NOTIFICATION_CHANNEL` defaults to `whatsapp`.

For OpenClaw, the checker runs `openclaw message send`. For Hermes, it runs
`hermes send --to whatsapp:+<E.164> "message"`; configure the corresponding
messaging integration before using it.

## Verification

- A successful task-list request prints tasks and writes `tasks.json` locally.
- A successful task-detail request returns structured JSON with task metadata,
  instructions, status, and available files.
- A successful course request prints course names, IDs, progress, and category.
- The new-task checker exits successfully when there are no pending tasks or no
  newly discovered tasks; read its output before treating an empty result as an
  error.

## Expected Output Examples

The following examples are synthetic. They show success shapes and markers,
not real course, task, or account data.

`python3 scripts/moodle_tasks.py --oneline` prints one task per line. A
successful empty result prints `No pending tasks.`; the detailed form prints
`No pending tasks found.`:

```text
  Thu 01/01/2026 12:00  Example Course  Example assignment (faltan 2h 0m) → Submit
```

`python3 scripts/moodle_task_detail.py <URL> --json` returns structured task
data:

```json
{
  "title": "Example assignment",
  "description": "Synthetic instructions.",
  "due_date": "01/01/2026 12:00",
  "submission_status": "Not submitted",
  "grades": "",
  "files": [],
  "url": "https://evirtual.espam.edu.ec/mod/assign/view.php?id=123",
  "status_code": 200
}
```

`python3 scripts/list_courses_inprogress.py` prints the course count followed
by entries in this form:

```text
Courses in progress (1):
  [123] Example Course — 0% — Example Category
```

The task checker prints `✅ No hay tareas nuevas. Terminando silenciosamente.`
when there are no new tasks, or `✅ Proceso completado.` after sending and
recording notifications.

## Failure Modes and Pitfalls

- **Missing credentials or authentication failure:** verify
  `ESPAM_STS_USER` and `ESPAM_STS_PASSWORD` without exposing their values.
- **Invalid or inaccessible task URL:** ask for the Moodle assignment URL; do
  not guess one.
- **Attachment download failure:** report the individual `download_error` and
  leave any successful downloads intact.
- **New-task checker first run:** an empty `moodle_known.json` treats all
  currently pending tasks as new. Confirm the notification target before the
  first scheduled run.
- **Ignored tasks:** `scripts/moodle_ignore.txt` contains regular-expression
  filters. Do not modify it unless the user asks to change the ignore policy.
- **Portal/API changes:** report parsing or authentication errors rather than
  inventing missing task, due-date, or grade information.
