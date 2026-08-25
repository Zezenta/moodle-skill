#!/usr/bin/env python3
"""
List courses currently in progress from ESPAM Moodle.

Usage:
  python3 list_courses_inprogress.py
  python3 list_courses_inprogress.py --json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moodle_lib import ensure_auth


def get_inprogress_courses(client):
    """Fetch enrolled courses with classification 'inprogress'."""
    result = client.ajax(
        'core_course_get_enrolled_courses_by_timeline_classification',
        args={
            'offset': 0,
            'limit': 0,
            'classification': 'inprogress',
            'sort': 'fullname',
            'customfieldname': '',
            'customfieldvalue': '',
            'requiredfields': ['id', 'fullname', 'shortname', 'showcoursecategory', 'showshortname', 'visible', 'enddate'],
        }
    )
    if not result or not isinstance(result, list):
        return []
    return result[0]['data']['courses']


def main():
    json_output = '--json' in sys.argv

    client, ok = ensure_auth()
    if not ok:
        print('ERROR: Could not authenticate.', file=sys.stderr)
        sys.exit(2)

    courses = get_inprogress_courses(client)

    if json_output:
        print(json.dumps(courses, indent=2, ensure_ascii=False))
    else:
        if not courses:
            print('No courses in progress.')
        else:
            print(f'Courses in progress ({len(courses)}):')
            for c in courses:
                name = c['fullname']
                progress = f"{c['progress']}%" if c.get('hasprogress') else '?'
                category = c.get('coursecategory', '')
                print(f"  [{c['id']}] {name} — {progress} — {category}")


if __name__ == '__main__':
    main()
