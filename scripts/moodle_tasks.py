#!/usr/bin/env python3
"""
Moodle tasks list for ESPAM university.
Fetches enrolled courses and pending tasks/deadlines.

Requires valid session — runs moodle_auth.py automatically if needed.

Usage:
  python3 moodle_tasks.py           # Full output
  python3 moodle_tasks.py --oneline # One line per task
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moodle_lib import MoodleClient, ensure_auth, ts_to_local, ts_to_local_dt, countdown_str, now_local, TASKS_JSON

# --- Ignore list ---
# Regex patterns (one per line) matched against "task_name | course_name".
# Case-insensitive. Use .* for flexible matching between words.
IGNORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moodle_ignore.txt')


def load_ignore_list():
    """Load compiled regex patterns from moodle_ignore.txt."""
    patterns = []
    if os.path.exists(IGNORE_FILE):
        with open(IGNORE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        patterns.append(re.compile(line, re.IGNORECASE))
                    except re.error as e:
                        print(f'  Warning: bad regex in ignore.txt: {line!r} ({e})', file=sys.stderr)
    return patterns


def is_ignored(task, patterns):
    """Check if a task matches any ignore regex pattern.
    Haystack is "task_name | course_name".
    """
    if not patterns:
        return False
    haystack = f"{task['task']} | {task['course']}"
    for pattern in patterns:
        if pattern.search(haystack):
            return True
    return False


def get_courses(client):
    """Get enrolled courses that are in progress."""
    print('[1/2] Fetching enrolled courses...', file=sys.stderr)
    result = client.ajax(
        'core_course_get_enrolled_courses_by_timeline_classification',
        args={
            'offset': 0,
            'limit': 24,
            'classification': 'inprogress',
            'sort': 'fullname',
            'customfieldname': '',
            'customfieldvalue': '',
            'requiredfields': ['id', 'fullname', 'shortname', 'showcoursecategory', 'showshortname', 'visible', 'enddate'],
        }
    )
    if isinstance(result, dict) and result.get('error'):
        print(f'  ERROR: {result.get("exception", {}).get("message", result)}', file=sys.stderr)
        return []
    if not result or not isinstance(result, list):
        print(f'  ERROR: unexpected response', file=sys.stderr)
        return []

    courses = result[0]['data']['courses']
    print(f'  Found {len(courses)} courses', file=sys.stderr)
    return courses


def get_course_tasks(client, course_id, limit=20):
    """Get action events (tasks/deadlines) for a specific course."""
    now = int(time.time())
    result = client.ajax(
        'core_calendar_get_action_events_by_courses',
        args={
            'courseids': [course_id],
            'limitnum': limit,
            'timesortfrom': now - 86400 * 30,
        }
    )
    if not result or result[0].get('error'):
        return []

    grouped = result[0]['data'].get('groupedbycourse', [])
    if not grouped:
        return []

    events = grouped[0].get('events', [])
    return events


def get_all_tasks(client):
    """Fetch courses and all tasks. Returns (courses, tasks)."""
    courses = get_courses(client)
    if not courses:
        return courses, []

    print('[2/2] Fetching tasks per course...', file=sys.stderr)
    all_tasks = []
    ignore_patterns = load_ignore_list()
    for course in courses:
        events = get_course_tasks(client, course['id'])
        ignored = 0
        for evt in events:
            task = {
                'course': course['fullname'],
                'course_id': course['id'],
                'task': evt.get('activityname', evt.get('name', '?')),
                'type': evt.get('modulename', '?'),
                'type_label': evt.get('activitystr', ''),
                'due': ts_to_local(evt.get('timesort')),
                'due_ts': evt.get('timesort'),
                'overdue': evt.get('overdue', False),
                'action': evt.get('action', {}).get('name', ''),
                'actionable': evt.get('action', {}).get('actionable', False),
                'url': evt.get('action', {}).get('url', evt.get('url', '')),
                'description': re.sub(r'<[^>]+>', '', evt.get('description', ''))[:200].strip(),
            }
            if is_ignored(task, ignore_patterns):
                ignored += 1
                continue
            all_tasks.append(task)
        total = len(events)
        shown = total - ignored
        print(f'  {course["fullname"][:50]}: {shown}/{total} tasks ({ignored} ignored)', file=sys.stderr)

    return courses, all_tasks


def print_oneline(tasks):
    """Print each task on a single line."""
    if not tasks:
        print('No pending tasks.')
        return
    for t in tasks:
        overdue = '⚠️ ' if t['overdue'] else '   '
        action = f" → {t['action']}" if t['actionable'] else ''
        course = t['course'].split(' - ')[0].strip()
        countdown = countdown_str(ts_to_local_dt(t['due_ts']))
        cd = f' ({countdown})' if countdown else ''
        print(f"  {overdue}{t['due']}  {course}  {t['task']}{cd}{action}")


def main():
    oneline = '--oneline' in sys.argv

    client, ok = ensure_auth()
    if not ok:
        print('ERROR: Could not authenticate.', file=sys.stderr)
        sys.exit(2)

    courses, tasks = get_all_tasks(client)

    ignore_patterns = load_ignore_list()
    if ignore_patterns:
        print(f'  Ignore list: {len(ignore_patterns)} patterns from moodle_ignore.txt', file=sys.stderr)

    if oneline:
        print_oneline(tasks)
    else:
        # Print header with today's date
        today = now_local().strftime('%A %d/%m/%Y')
        print(f'\n📅 Fecha de hoy: {today}')

        # Print courses
        print(f'\n{"="*60}')
        print(f'COURSES ({len(courses)}):')
        print(f'{"="*60}')
        for c in courses:
            print(f'  [{c["id"]}] {c["fullname"]} — {c["progress"]}% progress')

        # Print tasks
        print(f'\n{"="*60}')
        print(f'TASKS ({len(tasks)}):')
        print(f'{"="*60}')
        if not tasks:
            print('  No pending tasks found.')
        else:
            for i, t in enumerate(tasks, 1):
                overdue_flag = ' ⚠️ OVERDUE' if t['overdue'] else ''
                action_flag = f" → {t['action']}" if t['actionable'] else ''
                countdown = countdown_str(ts_to_local_dt(t['due_ts']))
                cd = f' ({countdown})' if countdown else ''
                print(f'\n  {i}. {t["task"]}{overdue_flag}')
                print(f'     Course: {t["course"][:60]}')
                print(f'     Type: {t["type_label"] or t["type"]}')
                print(f'     Due: {t["due"]}{cd}{action_flag}')
                if t['description']:
                    print(f'     Info: {t["description"][:150]}')
                print(f'     URL: {t["url"]}')

    # Save to JSON
    output = {
        'timestamp': datetime.now(timezone(timedelta(hours=-5))).isoformat(),
        'userid': client.userid,
        'courses': courses,
        'tasks': tasks,
    }
    with open(TASKS_JSON, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    if not oneline:
        print(f'\nSaved to {TASKS_JSON}')


if __name__ == '__main__':
    main()
