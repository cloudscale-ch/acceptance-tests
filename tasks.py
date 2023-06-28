import importlib
import inspect
import json
import re

from api import API
from constants import EVENTS_PATH
from datetime import datetime
from datetime import timedelta
from invoke import task
from pathlib import Path
from rich.console import Console
from tabulate import tabulate
from time import sleep
from types import SimpleNamespace
from util import yield_lines


# Those keys must be at the beginning
HEAD = (
    'command',
    'path',
    'url',
    'status',
    'exit_status',
    'result',
    'took',
    'public_ipv4',
    'public_ipv6',
    'private_ipv4',
    'private_ipv6',
)

# Those keys must be at the end
TAIL = ()


@task
def tail(c, regex=None):
    """ Follow the event logs that are currently being written, and show them
    using pretty output.

    """

    console = Console(soft_wrap=True)
    regex = regex and re.compile(regex) or None

    followed_logs = set()
    is_startup = True

    def unfollowed_logs():
        for log in Path(EVENTS_PATH).glob('*.log'):

            # Skip logs older than 2 hours
            horizon = datetime.now() - timedelta(hours=2)
            if log.stat().st_mtime < horizon.timestamp():
                continue

            # Skip logs that we already follow
            if log in followed_logs:
                continue

            followed_logs.add(log)
            yield log

    # Follow all existing logs, as well as newly discovered ones
    tails = []

    while True:
        for log in unfollowed_logs():
            tails.append(yield_lines(log, tail=is_startup))

        is_startup = False

        for tail in tails:
            for line in tail:
                if not line:
                    break

                line = process_event_line(line, regex)

                if regex and not regex.search(strip_styles(line)):
                    continue

                console.print(line)

        sleep(0.1)


@task
def pretty_print(c, file=None, regex=None):
    """ Pretty print the latest or the given events log. """

    console = Console(soft_wrap=True)
    regex = regex and re.compile(regex) or None

    latest = tuple(sorted(Path(EVENTS_PATH).glob('*.log')))[-1]

    with open(file or latest, 'r') as f:
        for line in f:
            line = process_event_line(line, regex)

            if regex and not regex.search(strip_styles(line)):
                continue

            console.print(line)


@task
def cleanup(c):
    """ Cleanup all test ressources associated with the current API token. """

    # Do not use events here, that only works during a test run
    from events import OBS
    OBS.off()

    API(scope=None, read_only=False).cleanup(
        limit_to_scope=False,
        limit_to_process=False,
    )


@task
def implemented_tests_table(c):
    """ Generate the Markdown table of the implemented tests in the README. """

    headers = ['Category', 'Test Name', 'Images']
    rows = []

    # Special cases for capitalizing test category titles
    category_capitalization = {
        'floating ip': 'Floating IP',
        'api': 'API',
    }

    for module_path in sorted(Path('.').glob('test_*.py')):
        module = importlib.import_module(module_path.stem)
        cat = module_path.stem.replace('test_', '').replace('_', ' ')
        cat = category_capitalization.get(cat, cat.title())

        functions = []

        for name, fn in inspect.getmembers(module, inspect.isfunction):

            if not name.startswith('test_'):
                continue

            functions.append((name, fn))

        functions.sort(key=lambda i: i[1].__code__.co_firstlineno)

        for name, fn in functions:
            file = module_path.name
            line = fn.__code__.co_firstlineno

            if 'custom_image' in name:
                image = 'custom'
            elif 'all_images' in name:
                image = 'all'
            elif 'common_images' in name:
                image = 'common'
            elif 'test_api.py' in file:
                # API tests are not run against any image, they test basic API
                # functionality
                image = '-'
            else:
                image = 'default'

            rows.append((
                cat and f'**{cat}**' or '',
                f'[{name}](./{file}#L{line})',
                image
            ))

            # Only show the category once per group
            cat = None

    with open('README.md', 'r') as f:
        readme = f.read()

    test_list_section = False
    with open('README.md', 'w') as f:
        for line in readme.splitlines(keepends=True):

            # While not in the test list section, write out lines
            if not test_list_section:
                f.write(line)

            # Start of the test list section, write out the test list
            if line.startswith('## Implemented Tests'):
                test_list_section = True
                f.write(f'\n{tabulate(rows, headers, tablefmt="github")}\n\n')

            # Next section, end of test list section
            elif test_list_section and line.startswith('##'):
                test_list_section = False
                f.write(line)


def format_event_attribute(event, key, value):

    # Instead of the full URL, we only show part of the API path.
    if key == 'url':
        path = value.split('/v1', 1)[-1]

        return f'path=[magenta]{path}[/magenta]'

    # Shorten durations
    if key == 'took':
        return f'{key}=[not bold][blue]{round(value, 3)}s[/blue][/not bold]'

    # Highlight successes/failures
    if key == 'result' and value == 'success':
        return f'{key}=[green]{value}[/green]'

    if key == 'result' and value == 'failure':
        return f'{key}=[red]{value}[/red]'

    # Quote commands
    if key == 'command':
        return f'{key}="[cyan]{value}[/cyan]"'

    # Highlight run ids
    if key == 'run_id':
        return f'{key}=[not bold][cyan]{value}[/cyan][/not bold]'

    # Some keys are shown plain
    if key in ('name', 'image', 'zone', 'region'):
        return f'{key}=[not bold][default]{value}[/default][/not bold]'

    # Color status codes
    if event.event.startswith('request') and key == 'status':

        if 200 <= value <= 299:
            return f'{key}=[not bold][green]{value}[/green][/not bold]'

        if 300 <= value <= 399:
            return f'{key}=[not bold][orange]{value}[/orange][/not bold]'

        return f'{key}=[red]{value}[/red]'

    # Highlight command errors
    if key == 'exit_status':
        if value == 0:
            return f'{key}=[not bold][green]{value}[/green][/not bold]'
        else:
            return f'{key}=[not bold][orange]{value}[/orange][/not bold]'

    return f'{key}={value}'


def event_name_style(name):
    if name.startswith('request'):
        return 'dim'

    if name in ('server.run', 'server.output-of', 'server.assert-run'):
        return 'dim'

    if name.startswith('server.wait-for'):
        return 'dim'

    return 'bold'


def key_order(key):

    try:
        return HEAD.index(key)
    except ValueError:
        pass

    try:
        return 1000 + TAIL.index(key)
    except ValueError:
        pass

    return len(HEAD) + 1


def exclude_key(evt, key):

    # Only show the UUID when the record is created
    if key == 'uuid' and 'create' not in evt.event:
        return True

    return False


def process_event_line(line, regex):
    evt = json.loads(line, object_hook=lambda d: SimpleNamespace(**d))
    evt.time = datetime.fromisoformat(evt.time)

    header = f"[blue]{evt.time:%Y-%m-%d %H:%M:%S}[/blue] {evt.worker}"

    if evt.test:
        header = f"{header} [not bold][default]{evt.test}[/default][/not bold]"

    style = event_name_style(evt.event)
    header = f"{header} [{style}]{evt.event}[/{style}]"

    body = ' '.join(
        format_event_attribute(evt, k, evt.__dict__[k])
        for k in sorted(evt.__dict__, key=key_order) if k not in (
            'time', 'worker', 'test', 'event'
        ) and not exclude_key(evt, k)
    )

    return f"{header} {body}".strip()


def strip_styles(line):
    console = Console(soft_wrap=True)
    return ''.join(s.text for s in console.render(line))
