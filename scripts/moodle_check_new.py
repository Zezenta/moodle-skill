#!/usr/bin/env python3
"""
Moodle task checker - Identifica y notifica tareas nuevas en Moodle.
Script para ser usado en cron jobs.

Uso:
  python3 moodle_check_new.py
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_JSON = os.path.join(SCRIPT_DIR, 'tasks.json')
KNOWN_JSON = os.environ.get('MOODLE_KNOWN_TASKS_FILE', os.path.join(SCRIPT_DIR, 'moodle_known.json'))
NOTIFICATION_PROVIDER = os.environ.get('MOODLE_NOTIFICATION_PROVIDER', 'openclaw').lower()
NOTIFICATION_CHANNEL = os.environ.get('MOODLE_NOTIFICATION_CHANNEL', 'whatsapp')
# MOODLE_WHATSAPP_TARGET is retained for backwards compatibility.
NOTIFICATION_TARGET = (
    os.environ.get('MOODLE_NOTIFICATION_TARGET')
    or os.environ.get('MOODLE_WHATSAPP_TARGET')
)


def extract_mod_id(url):
    """Extrae el mod_id de una URL de Moodle."""
    match = re.search(r'id=(\d+)', url)
    return match.group(1) if match else ''


def form_task_key(course, task, mod_id):
    """Forma la key de la tarea usando el formato: course + '::' + task + '::' + mod_id"""
    return f"{course}::{task}::{mod_id}"


def get_current_tasks():
    """Obtiene las tareas actuales de Moodle."""
    print('[1/3] Obteniendo tareas actuales de Moodle...')
    
    # Ejecutar moodle_tasks.py
    try:
        result = subprocess.run([
            'python3', os.path.join(SCRIPT_DIR, 'moodle_tasks.py')
        ], capture_output=True, text=True, check=True)
        
        # Verificar si se generó tasks.json
        if not os.path.exists(TASKS_JSON):
            print('ERROR: No se pudo generar tasks.json')
            return None
            
    except subprocess.CalledProcessError as e:
        print(f'ERROR: Falló moodle_tasks.py: {e}')
        return None
    except Exception as e:
        print(f'ERROR: Error al ejecutar moodle_tasks.py: {e}')
        return None
    
    # Cargar tareas desde tasks.json
    try:
        with open(TASKS_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('tasks', [])
    except Exception as e:
        print(f'ERROR: No se pudo leer tasks.json: {e}')
        return None


def get_known_tasks():
    """Obtiene las tareas conocidas de moodle_known.json."""
    print('[2/3] Leyendo tareas conocidas...')
    
    if not os.path.exists(KNOWN_JSON):
        print('Advertencia: moodle_known.json no existe, creando uno nuevo')
        return {'known': [], 'ignore': [], 'last_check': None, 'new_tasks_added': []}
    
    try:
        with open(KNOWN_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'ERROR: No se pudo leer moodle_known.json: {e}')
        return {'known': [], 'ignore': [], 'last_check': None, 'new_tasks_added': []}


def find_new_tasks(current_tasks, known_data):
    """Encuentra tareas nuevas que no están en known ni ignore."""
    print('[3/3] Comparando tareas nuevas con conocidas...')
    
    # Obtener todas las keys conocidas
    known_keys = set()
    for task in known_data.get('known', []):
        known_keys.add(task['key'])
    for task in known_data.get('ignore', []):
        known_keys.add(task['key'])
    
    # Encontrar tareas nuevas
    new_tasks = []
    current_task_keys = set()
    
    for task in current_tasks:
        mod_id = extract_mod_id(task['url'])
        key = form_task_key(task['course'], task['task'], mod_id)
        current_task_keys.add(key)
        
        if key not in known_keys:
            new_tasks.append({
                'key': key,
                'course': task['course'],
                'task': task['task'],
                'type': task['type'],
                'due': task['due'],
                'url': task['url'],
                'overdue': task['overdue']
            })
            print(f'  Nueva tarea encontrada: {task["task"][:50]}...')
    
    # También verificar si hay tareas que ya no están en Moodle (se eliminaron)
    removed_tasks = []
    for key in known_keys:
        if key not in current_task_keys:
            # Buscar la tarea completa para saber cuál se eliminó
            for task in known_data.get('known', []):
                if task['key'] == key:
                    removed_tasks.append(task['task'])
                    break
    
    if removed_tasks:
        print(f'  {len(removed_tasks)} tareas ya no están en Moodle (podrían reaparecer)')
    
    return new_tasks, current_task_keys


def send_whatsapp_notification(task):
    """Send a new-task notification through OpenClaw or Hermes."""
    if not NOTIFICATION_TARGET:
        print('  ⚠️ Set MOODLE_NOTIFICATION_TARGET; notification skipped.')
        return False

    print(f'  Sending notification for: {task["task"][:50]}...')
    message = f'📋 Tarea nueva en Moodle:\n{task["task"]}\n{task["course"]}\n{task["due"]}\n{task["url"]}'

    if NOTIFICATION_PROVIDER == 'openclaw':
        cmd = [
            'openclaw', 'message', 'send',
            '--channel', NOTIFICATION_CHANNEL,
            '--target', NOTIFICATION_TARGET,
            '--message', message,
        ]
    elif NOTIFICATION_PROVIDER == 'hermes':
        cmd = [
            'hermes', 'send',
            '--to', f'{NOTIFICATION_CHANNEL}:{NOTIFICATION_TARGET}',
            message,
        ]
    else:
        print(
            f'  ⚠️ Unsupported MOODLE_NOTIFICATION_PROVIDER: {NOTIFICATION_PROVIDER!r}. '
            'Use "openclaw" or "hermes".',
        )
        return False
    
    try:
        subprocess.run(cmd, check=True)
        print('  ✅ Notification sent')
        return True
    except subprocess.CalledProcessError as e:
        print(f'  ❌ Notification command failed: {e}')
        return False
    except Exception as e:
        print(f'  ❌ Unexpected notification error: {e}')
        return False


def update_known_tasks(known_data, new_tasks, current_task_keys):
    """Actualiza moodle_known.json con las nuevas tareas."""
    print('Actualizando moodle_known.json...')
    
    # Convertir a listas para poder modificarlas
    known_list = known_data.get('known', [])
    ignore_list = known_data.get('ignore', [])
    
    # Agregar nuevas tareas a 'known'
    for new_task in new_tasks:
        # Verificar si ya existe (evitar duplicados)
        exists = any(t['key'] == new_task['key'] for t in known_list)
        if not exists:
            known_list.append(new_task)
            print(f'  Agregada tarea conocida: {new_task["task"][:50]}...')
    
    # Actualizar timestamp
    known_data['known'] = known_list
    known_data['ignore'] = ignore_list
    known_data['last_check'] = datetime.now(timezone(timedelta(hours=-5))).isoformat()
    
    # Guardar archivo
    try:
        with open(KNOWN_JSON, 'w', encoding='utf-8') as f:
            json.dump(known_data, f, indent=2, ensure_ascii=False)
        print('  ✅ moodle_known.json actualizado')
        return True
    except Exception as e:
        print(f'  ❌ Error al actualizar moodle_known.json: {e}')
        return False


def main():
    """Función principal."""
    print(f'🔍 Moodle Task Checker - {datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")}')
    
    # 1. Obtener tareas actuales
    current_tasks = get_current_tasks()
    if current_tasks is None:
        print('❌ Error al obtener tareas de Moodle. Terminando.')
        sys.exit(1)
    
    if not current_tasks:
        print('ℹ️  No hay tareas pendientes en Moodle. Terminando.')
        sys.exit(0)
    
    print(f'  Se encontraron {len(current_tasks)} tareas pendientes')
    
    # 2. Obtener tareas conocidas
    known_data = get_known_tasks()
    
    # 3. Encontrar tareas nuevas
    new_tasks, current_task_keys = find_new_tasks(current_tasks, known_data)
    
    if not new_tasks:
        print('✅ No hay tareas nuevas. Terminando silenciosamente.')
        sys.exit(0)
    
    print(f'🆕 Se encontraron {len(new_tasks)} tareas nuevas')
    
    # 4. Procesar tareas nuevas
    alerts_sent = 0
    for task in new_tasks:
        if send_whatsapp_notification(task):
            alerts_sent += 1
    
    # 5. Actualizar archivo de tareas conocidas
    if alerts_sent > 0:
        if update_known_tasks(known_data, new_tasks, current_task_keys):
            print(f'✅ Proceso completado. {alerts_sent} alertas enviadas, {len(new_tasks)} tareas agregadas.')
        else:
            print('⚠️  Alertas enviadas pero error al actualizar archivo.')
    else:
        print('❌ No se pudo enviar ninguna alerta.')
    
    sys.exit(0 if alerts_sent > 0 else 1)


if __name__ == '__main__':
    main()
