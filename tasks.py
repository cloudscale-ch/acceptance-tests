import importlib
import inspect
import json
import re

from api import API
from constants import EVENTS_PATH
from datetime import date
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
def summary(c):
    """ Analyzes test last test-run (including reruns) and prints a markdown
    summary to be used with $GITHUB_STEP_SUMMARY or stdout in general.

    """

    pattern = f'at-{date.today().year}-*.log'
    results = []
    requests = []

    # Go from newest to oldest, until events with only one run are found. This
    # way, we get the latest events of run n, then n-1, towards 1.
    maxrun = 1

    for log in sorted(Path('events').glob(pattern), reverse=True):
        with log.open('r') as f:
            for line in f:
                e = json.loads(line)

                # Collect all request retry events
                if e['event'].startswith('request.'):
                    requests.append(e)

                if e.get('event') not in ('test.setup', 'test.call'):
                    continue

                # There won't be an additional phase if the setup fails
                if e['event'] == 'test.setup' and e['outcome'] != 'passed':
                    results.append(e)

                if e['event'] == 'test.call':
                    results.append(e)

        if results and results[-1]['run'] == 1:
            break

        if results:
            maxrun = max(maxrun, results[-1]['run'])

    results.sort(key=lambda e: e['time'])

    # List of possible test outcomes
    known_outcomes = {'passed', 'failed', 'skipped', 'xfailed', 'xpassed'}

    # Gather statistics
    successes = sum(
        1 for r in results
        if r['outcome'] == 'passed' and r['run'] == 1)

    skipped = sum(
        1 for r in results
        if r['outcome'] == 'skipped' and r['run'] == 1)

    reruns = [
        r for r in results
        if r['outcome'] == 'passed' and r['run'] != 1]

    failures = sum(
        1 for r in results
        if r['outcome'] == 'failed' and r['run'] == maxrun)

    xfailed = sum(
        1 for r in results
        if r['outcome'] == 'xfailed' and r['run'] == maxrun)

    xpassed = sum(
        1 for r in results
        if r['outcome'] == 'xpassed' and r['run'] == maxrun)

    unknowns = sum(
        1 for r in results
        if r['outcome'] not in known_outcomes)

    maintenance_retries = sum(
        sum(1 for status in r['history'] if status == 503)
        for r in requests
        if r['retries'])

    print("# Test Run Summary")
    print("")

    if successes:
        print(f"✅ {successes} tests passed on the first try.\n")

    if skipped:
        print(f"ℹ️ {skipped} tests were skipped.\n")

    if xfailed:
        print(f"🥹‍ {xfailed} tests failed as expected (XFAIL).\n")

    if xpassed:
        print(f"‍🤨 {xpassed} tests passed unexpectedly (XPASS).\n")

    if unknowns:
        print(f"🛸 {unknowns} tests had an unknown outcome.\n")

    if reruns and maxrun == 2:
        print(f"⚠️ {len(reruns)} passed after a rerun.\n")

    if reruns and maxrun > 2:
        print(f"⚠️ {len(reruns)} passed after multiple reruns.\n")

    if failures:
        print(f"⛔️ {len(failures)} did not pass at all.\n")

    if maintenance_retries:
        print(
            f"🚧 A total of {maintenance_retries} requests were retried "
            "due to API maintenance.\n"
        )

    if any(r['outcome'] != 'passed' or r['run'] != 1 for r in results):
        print("## Detailed Results")
        print("")

        for r in results:
            if r['outcome'] == 'passed' and r['run'] == 1:
                continue  # Skip tests that passed cleanly

            print('<details><summary><code>', end='')
            test_name = r.get('test', 'UnknownTest')
            short_error = r.get('short_error') or f"{r['outcome']}"
            print(f"{test_name}: {short_error}", end='')
            print('</code></summary>')
            print('')

            # Full error block, if available
            full_error = r.get('error')
            if full_error:
                print('```python')
                print(full_error)
                print('```')
            else:
                print(f"`{r['outcome']}` (detailed result unavailable)")

            print('')
            print('</details>')
            print('')


@task
def implemented_tests_table(c):
    """ Generate the Markdown table of the implemented tests in the README. """

    headers = ['Category', 'Test Name', 'Images']
    rows = []

    # Exclude certain files from the table
    exclude_list = [
        'test_infrastructure.py'
    ]

    # Special cases for capitalizing test category titles
    category_capitalization = {
        'floating ip': 'Floating IP',
        'api': 'API',
    }

    for module_path in sorted(Path('.').glob('test_*.py')):
        # Exclude certain files from the table
        if module_path.name in exclude_list:
            continue

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
