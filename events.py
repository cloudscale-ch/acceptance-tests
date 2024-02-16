import functools
import json
import os

from collections import OrderedDict
from constants import EVENTS_PATH
from constants import LOCKS_PATH
from constants import WORKER_ID
from datetime import datetime
from filelock import FileLock
from observable import Observable
from types import SimpleNamespace
from util import arguments_as_namespace
from util import dot_access
from util import global_run_id

# Global observable object
OBS = Observable()

# Global context
CTX = SimpleNamespace(
    current_test=None,
    worker_id=WORKER_ID,
)

# Undefined return value
UNDEFINED = object()


def trigger(event, **attributes):
    """ Triggers the event with the given name, passing the given attributes
    to any handler connected to it.

    Returns True if any handlers were invoked. False if there were none.

    """

    return OBS.trigger(event, **attributes)


def with_trigger(event):
    """ Trigger the given event as follows:

    First the event '<event>.before' is triggered, with the function arguments
    passed, using the argument name 'args'.

    Second, the event '<event>.after' is triggered, with 'args', as well as the
    following values:

        * 'exception' -> An exception object or None.
        * 'result' -> The result of the function (if no exception).
        * 'took' -> The seconds the call took.

    """

    def decorator(fn):

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            trigger_args = arguments_as_namespace(fn, args, kwargs)

            trigger(
                event=f'{event}.before',
                args=trigger_args,
            )

            time = datetime.utcnow()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                trigger(
                    event=f'{event}.after',
                    args=trigger_args,
                    exception=e,
                    result=UNDEFINED,
                    took=(datetime.utcnow() - time).total_seconds(),
                )
                raise e
            else:
                trigger(
                    event=f'{event}.after',
                    args=trigger_args,
                    exception=None,
                    result=result,
                    took=(datetime.utcnow() - time).total_seconds(),
                )
                return result

        return wrapper

    return decorator


def record(event, **attributes):
    """ Writes a new record to the event log.

    The "event" should be something easily identifyable. For example:

    - run.start
    - server.start
    - server.stop

    The other key-value attributes will be added to the record. Additionally,
    the following attributes are automatically added:

    - current test
    - time
    - worker

    """

    for reserved in ('time', 'worker', 'test'):
        assert reserved not in attributes

    record = OrderedDict()
    record['time'] = datetime.now().isoformat()
    record['worker'] = CTX.worker_id
    record['test'] = CTX.current_test
    record['event'] = event
    record['run'] = int(os.environ.get('TEST_RUN', '1'))

    for key in sorted(attributes):
        record[key] = attributes[key]

    line = json.dumps(record)

    file_path = f'{EVENTS_PATH}/{global_run_id()}.log'
    lock_path = f'{LOCKS_PATH}/{global_run_id()}.lock'

    with FileLock(lock_path):
        with open(file_path, 'a') as f:
            f.write(line)
            f.write("\n")


def track_in_event_log(event, include=None):
    """ Listens to the given event in perpetuity and writes it to the event
    log using the attributes as given.

    The attributes included in the event log are specified through the
    `include` parameter, which is a dictionary where the key is the key that
    will be recorded, and the value is an expression that will be evaluated.

    1. If the value is a string, it will be resolved by looking at the
       attributes of the event.

       For examaple, if 'request': 'request' is given, the request attribute is
       stored under the request key in the log.

       If 'url': 'request.url' is given, the url attribute of the request
       attribute is stored under url key in the log.

    2. If the value is a callable, a namespace with all attributes is passed
       to the given function and the result of the function is stored under
       the given key.

    """

    include = include or {}

    def extract_data(attributes):
        for k, v in include.items():

            if isinstance(v, str):
                try:
                    yield k, dot_access(v, attributes)
                except AttributeError:
                    # Ignore attribute if it does not exist
                    pass

            elif callable(v):
                yield k, v(SimpleNamespace(**attributes))

            else:
                raise RuntimeError(f"Unexpected value for {k}: {v}")

    @OBS.on(event)
    def on_record_event(**attributes):
        record(**{'event': event, **dict(extract_data(attributes))})


# Often tracked attributes
RESOURCE_ID = {
    'name': 'args.self.name',
    'uuid': 'args.self.uuid',
}

RESULT = {
    'took': 'took',
    'result': lambda a: a.exception and 'failure' or 'success',
}


# Keep track of test runs
track_in_event_log('run.start', include={'run_id': 'run_id'})
track_in_event_log('run.end', include={'result': 'result', 'run_id': 'run_id'})


# Keep track of test items, recording their start and their result
track_in_event_log('test.start')


@OBS.on('test.start')
def on_test_start(name):
    CTX.current_test = name.split('::')[-1]


@OBS.on('test.teardown')
def on_test_teardown(name, outcome, error, short_error):
    CTX.current_test = None


for phase in ('call', 'setup', 'teardown'):
    track_in_event_log(f'test.{phase}', include={
        'outcome': 'outcome',
        'error': 'error',
        'short_error': 'short_error',
    })


# Keep track of API requests
track_in_event_log('request.after', include={
    'event': lambda a: f'request.{a.request.method}',
    'url': 'request.url',
    'status': 'response.status_code',
    'took': lambda a: a.response.elapsed.total_seconds(),
})


# Keep track of server creation
track_in_event_log('server.create.before', include={
    'name': 'args.self.name',
    'zone': 'args.self.zone',
    'image': 'args.self.image',
    'flavor': 'args.self.flavor',
})

track_in_event_log('server.create.after', include={
    **RESOURCE_ID,
    **RESULT,
    'zone': 'args.self.zone.slug',
    'image': 'args.self.image.slug',
    'public_ipv4': lambda a: str(a.args.self.ip('public', 4, False)),
    'public_ipv6': lambda a: str(a.args.self.ip('public', 6, False)),
    'private_ipv4': lambda a: str(a.args.self.ip('private', 4, False)),
    'private_ipv6': lambda a: str(a.args.self.ip('private', 6, False)),
})

track_in_event_log('resource.wait.before', include={
    **RESOURCE_ID,
    'status': 'args.status',
})

track_in_event_log('resource.wait.after', include={
    **RESOURCE_ID,
    **RESULT,
    'status': 'args.status',
})

track_in_event_log('server.connect.before', include=RESOURCE_ID)

track_in_event_log('server.connect.after', include={
    **RESOURCE_ID,
    **RESULT,
})

track_in_event_log('server.wait-for-cloud-init.after', include={
    **RESOURCE_ID,
    **RESULT,
})

track_in_event_log('server.wait-for-port.after', include={
    **RESOURCE_ID,
    **RESULT,
    'port': 'args.port',
    'state': 'args.state',
})

track_in_event_log('server.wait-for-non-tentative-ipv6.after', include={
    **RESOURCE_ID,
    **RESULT,
})

track_in_event_log('server.wait-for-ipv6-default-route.after', include={
    **RESOURCE_ID,
    **RESULT,
})

# Keep track of server changes
track_in_event_log('server.update.after', include={
    **RESOURCE_ID,
    **RESULT,
    'changes': lambda a: {
        k: v for k, v in a.args.__dict__.items() if k != 'self'
    }
})

track_in_event_log('sever.scale-root.after', include={
    **RESOURCE_ID,
    **RESULT,
    'new_size': 'args.new_size',
})

# Keep track of power events
track_in_event_log('server.start.before', include={
    **RESOURCE_ID,
})

track_in_event_log('server.start.after', include={
    **RESOURCE_ID,
    **RESULT,
})

track_in_event_log('server.stop.before', include={
    **RESOURCE_ID,
})

track_in_event_log('server.stop.after', include={
    **RESOURCE_ID,
    **RESULT,
})

track_in_event_log('server.reboot.before', include={
    **RESOURCE_ID,
})

track_in_event_log('server.reboot.after', include={
    **RESOURCE_ID,
    **RESULT,
})


# Keep track of server commands
track_in_event_log('server.run.after', include={
    **RESOURCE_ID,
    **RESULT,
    'command': 'args.command',
    'exit_status': 'result.exit_status',
})

track_in_event_log('server.output-of.after', include={
    **RESOURCE_ID,
    **RESULT,
    'command': 'args.command',
})

track_in_event_log('server.assert-run.after', include={
    **RESOURCE_ID,
    **RESULT,
    'command': 'args.command',
})


# Keep track of server groups
track_in_event_log('server-group.create.after', include={
    **RESOURCE_ID,
    **RESULT,
    'zone': 'args.self.zone.slug',
})

track_in_event_log('server-group.rename.after', include={
    **RESOURCE_ID,
    **RESULT,
    'zone': 'args.self.zone.slug',
})


# Keep track of Floating IPs
track_in_event_log('floating-ip.create.after', include={
    'network': lambda a: str(a.args.self.network),
    'region': 'args.self.region.slug',
    **RESULT,
})

track_in_event_log('floating-ip.assign.after', include={
    'network': lambda a: str(a.args.self.network),
    'server_name': 'args.server.name',
    'server_uuid': 'args.server.uuid',
    'load_balancer_name': 'args.load_balancer.name',
    'load_balancer_uuid': 'args.load_balancer.uuid',
    **RESULT,
})

track_in_event_log('floating-ip.update.after', include={
    'network': lambda a: str(a.args.self.network),
    **RESULT,
    'changes': lambda a: {
        k: v for k, v in a.args.__dict__.items() if k != 'self'
    }
})

# Keep track of Volumes
track_in_event_log('volume.create.after', include={
    **RESOURCE_ID,
    **RESULT,
    'size_gb': 'args.self.size_gb',
    'type': 'args.self.type',
    'zone': 'args.self.zone'
})

track_in_event_log('volume.attach.after', include={
    **RESOURCE_ID,
    **RESULT,
    'server_name': 'args.server.name',
    'server_uuid': 'args.server.uuid',
})

track_in_event_log('volume.scale.after', include={
    **RESOURCE_ID,
    **RESULT,
    'new_size': 'args.new_size',
})

track_in_event_log('volume.detach.after', include={
    **RESOURCE_ID,
    **RESULT,
})


# Keep track of networks
track_in_event_log('network.create.after', include={
    **RESOURCE_ID,
    **RESULT,
    'zone': 'args.self.zone.slug',
    'auto_create_ipv4_subnet': 'args.self.auto_create_ipv4_subnet',
})

track_in_event_log('network.change-mtu.after', include={
    **RESOURCE_ID,
    **RESULT,
    'mtu': 'args.mtu',
})


# Keep track of subnets
track_in_event_log('subnet.create.after', include={
    **RESULT,
    'uuid': 'args.self.uuid',
    'network_uuid': 'args.self.network.uuid',
    'network_name': 'args.self.network.name',
    'cidr': 'args.self.cidr',
    'gateway_address': 'args.self.gateway_address',
    'dns_servers': 'args.self.dns_servers',
})

track_in_event_log('subnet.change-dns-servers.after', include={
    **RESOURCE_ID,
    **RESULT,
    'dns_servers': 'args.self.dns_servers',
})


# Keep track of custom images
track_in_event_log('custom-image.import.after', include={
    **RESOURCE_ID,
    **RESULT,
    'url': 'args.self.url',
    'slug': 'args.self.slug',
    'user_data_handling': 'args.self.user_data_handling',
    'zones': 'args.self.zones',
    'source_format': 'args.self.source_format',
})
