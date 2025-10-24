"""

Load Balancer as a Service
==========================

You can create, modify and delete TCP load balancers.

"""
import pytest

from time import sleep
from util import build_http_url
from util import get_backends_for_request
from util import in_parallel
from util import RESOURCE_NAME_PREFIX
from util import retry_for
from util import setup_lbaas_http_test_server
from util import setup_lbaas_udp_test_server
from util import start_persistent_download
from util import unique
from util import wait_for_load_balancer_ready
from util import wait_for_url_ready


def test_simple_tcp_load_balancer(prober, create_load_balancer_scenario):
    """ Create a simple TCP load balancer with one backend. """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # Test if the load balancer works on IPv4
    content = prober.http_get(load_balancer.build_url(addr_family=4))
    assert 'Backend server running on' in content

    # Test if the load balancer works on IPv6
    content = prober.http_get(load_balancer.build_url(addr_family=6))
    assert 'Backend server running on' in content


def test_simple_udp_load_balancer(prober, create_load_balancer_scenario):
    """ Create a simple UDP load balancer with one backend. """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='udp',
            listener_protocol='udp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    backend, = backends

    def assert_udp_response():
        assert (f'Backend server running on {backend.name} {backend.uuid}.'
                == prober.udp_get(load_balancer.vip(4), 80))

    # Test if the load balancer works on IPv4
    # Due to the nature of UDP we allow multiple retries
    retry_for(seconds=20).or_fail(
        assert_udp_response,
        msg='No response from UDP load balancer on IPv4',
    )


def test_load_balancer_end_to_end(prober, create_load_balancer_scenario):
    """ Multi backend load balancer end-to-end test scenario.

    * Load balancer on a public network with multiple backend servers on a
      private network
    * Send TCP traffic through the LB and verify backend answers

    """

    # Create a load balancer setup with two backends on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=2,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # Issue 10 requests on IPv4 and IPv6 to the load balancer
    for i in range(10):
        content = prober.http_get(load_balancer.build_url(addr_family=4))
        assert 'Backend server running on' in content

        content = prober.http_get(load_balancer.build_url(addr_family=6))
        assert 'Backend server running on' in content

    # Assert logs on both backend servers show they received traffic
    assert unique(backends) == unique(get_backends_for_request(backends))

    # Repeat tests with UDP

    # Setup UDP test servers on both backends
    for backend in backends:
        setup_lbaas_udp_test_server(backend)

    # Create UDP pool for the load balancer
    udp_pool = load_balancer.add_pool(f'lb-udp-pool', 'round_robin', 'udp')

    # Add backend members to the UDP pool
    for backend in backends:
        load_balancer.add_pool_member(udp_pool, backend, private_network)

    # Add UDP listener on port 80
    load_balancer.add_listener(udp_pool, 80, protocol='udp')

    # Wait until the load balancer is operational
    wait_for_load_balancer_ready(load_balancer, prober,
                                 port=80, timeout=30, protocol='udp')

    # Issue 10 UDP requests to the load balancer
    udp_responses_ipv4 = []

    for i in range(10):
        response = prober.udp_get(load_balancer.vip(4), 80)
        assert 'Backend server running on' in response
        udp_responses_ipv4.append(response)

    # Extract unique backend names from UDP responses
    def extract_backend_names(responses):
        return {
            backend.name
            for response in responses
            for backend in backends
            if backend.name in response
        }

    # Assert both backends received UDP traffic on IPv4
    assert len(extract_backend_names(udp_responses_ipv4)) == len(backends), \
        "Not all backends received UDP traffic on IPv4"


def test_multiple_listeners(prober, create_load_balancer_scenario):
    """ Two load balancer listeners connected to the same pool.

    """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener1, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
    )

    # Add an additional listener on port 81
    load_balancer.add_listener(pool, 81, name='listener-81')

    # Assert the LB still works on port 80
    assert prober.http_get(f'http://{load_balancer.vip(4)}/hostname') \
        == backend.name

    # Wait for the new listener on port 81 to be operational
    wait_for_load_balancer_ready(load_balancer, prober, port=81, timeout=30)

    # Assert backend is also reachable on port 81
    assert prober.http_get(f'http://{load_balancer.vip(4)}:81/hostname') \
        == backend.name


def test_multiple_listeners_multiple_pools(
        prober, create_backend_server, image,
        create_private_network,
        create_load_balancer_scenario,
):
    """ Two listeners connected to their own pool of member servers each.

    """

    # Create a load balancer setup with one backends on a private network
    load_balancer, listener1, pool1, (backend1, ), private_network1 = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
    )

    # Assert backend1 is reachable on port 80
    assert prober.http_get(f'http://{load_balancer.vip(4)}/hostname') \
        == backend1.name

    # Create an additional backend network
    private_network2 = create_private_network(auto_create_ipv4_subnet=True)

    # Create an additonal backend server
    backend2 = create_backend_server(
        name='backend2',
        private_network=private_network2,
    )

    # Create a second pool and add the additional backend to it
    pool2 = load_balancer.add_pool(f'lb-pool-2', 'round_robin', 'tcp')
    load_balancer.add_pool_member(pool2, backend2, private_network2)

    # Add an additional listener on Port 81 for the second pool
    load_balancer.add_listener(pool2, 81, name='listener-81')

    # Assert backend1 is still reachable on port 80
    assert prober.http_get(f'http://{load_balancer.vip(4)}/hostname') \
        == backend1.name

    # Wait for the new backend to be operational
    wait_for_load_balancer_ready(load_balancer, prober, port=81, timeout=30)

    # Assert backend2 is reachable on port 81
    assert prober.http_get(f'http://{load_balancer.vip(4)}:81/hostname') \
        == backend2.name


def test_balancing_algorithm_round_robin(
        prober, create_load_balancer_scenario,
):
    """ The round_robin balancing algorithm schedules connections in turn among
    pool members.

    """
    # Create a load balancer setup with 3 backends on a private network
    num_backends = 3
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=num_backends,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # Issue a request to each backend to get the round robin order
    backend_order = [prober.http_get(load_balancer.build_url(url='/hostname'))
                     for i in range(num_backends)]

    # Assert all backends got a request
    assert len(unique(backend_order)) == num_backends

    # Issue 10 requests to each backend and verify round robin distribution
    for n in range(10 * num_backends):
        # Assert the correct backend received the request
        hit_backend_name = prober.http_get(
            load_balancer.build_url(url='/hostname')
        )
        assert hit_backend_name == backend_order[n % num_backends]


def test_balancing_algorithm_source_ip(
        prober, create_load_balancer_scenario, create_server, image,
):
    """ The source_ip balancing algorithm always schedules connections from the
    same source to the same pool member.

    This test creates a load balancer with 4 backends and 4 clients with
    different source IP addresses and verifies that connections from the same
    client always go to the same backend.

    """
    # Create a load balancer setup with 4 backends on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=4,
            algorithm='source_ip',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # Create 4 client VMs
    clients = in_parallel(
        create_server,
        instances=({
            'name': f'client{i+1}',
            'image': image,
        } for i in range(4))
    )

    # Build map of which client is directed to which backend server
    backend_per_client = {}
    for client in clients:
        # Issue the first request from this client to the LB
        backend_per_client[client.name] = client.http_get(
            load_balancer.build_url(url='/hostname'),
        )

    # Issue 10 requests and verify the correct backend receives the request
    for n in range(10):
        for client in clients:
            hit_backend_name = client.http_get(
                load_balancer.build_url(url='/hostname')
            )
            assert backend_per_client[client.name] == hit_backend_name


def test_balancing_algorithm_least_connections(
        server, create_load_balancer_scenario,
):
    """ The least_connections balancing algorithm schedules connections to the
    backend with the least amount of active connections.

    Note: The current connection count is reset on any configuration changes to
          the load balancer. Still running connections initiated before the
          configuration change are not considered for selecting the backend.
    """

    # This test uses a function scoped prober because we are going to start
    # long running downloads which should not impact other tests.
    prober = server

    # Create a load balancer setup with 2 backends on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=2,
            algorithm='least_connections',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # start a persistent endless download of random data to "block" one backend
    blocked_backend = start_persistent_download(prober, load_balancer,
                                                backends)

    # Verify requests go to the other backend as it has less active
    # connections
    for i in range(10):
        hit_backend_name = prober.http_get(
            load_balancer.build_url(url='/hostname')
        )

        assert hit_backend_name != blocked_backend.name


@pytest.mark.parametrize('health_monitor_type',
                         ['ping', 'tcp', 'http', 'https', 'tls-hello'])
def test_backend_health_monitors(
        prober, create_load_balancer_scenario, health_monitor_type,
):
    """ Different health monitoring methods can be used to verify pool member
    availability:

    * ICMP ping health monitors check pool member availability by sending ICMP
      echo requests to the pool member IP address
    * TCP health monitors check the availability of TCP port on a pool member
    * HTTP(S) health monitors check the response code of a HTTP(S) URL
    * TLS_HELLO health monitors check that a pool member answers by initiating
      a TLS handshake on a TCP port

    """

    # Additional http parameter configuration for health monitor types
    # which require additional configuration.
    health_monitor_http_config = {
        'http': {'host': 'www.example.com'},
        'https': {'version': '1.0'},
    }

    # Configure SSL backend for SSL health checks
    ssl = health_monitor_type in ('https', 'tls-hello') and True or False

    # Create a load balancer setup with 1 backend on a private network
    load_balancer, listener, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='least_connections',
            port=80,
            pool_protocol='tcp',
            allowed_cidrs=None,
            health_monitor_type=health_monitor_type,
            health_monitor_http_config=health_monitor_http_config.get(
                health_monitor_type),
            ssl=ssl,
    )

    # Test function to assert the desired load balancer and health monitor
    # status
    def assert_status(load_balancer_status, monitor_status):
        load_balancer.refresh()
        assert load_balancer.status == load_balancer_status
        assert load_balancer.pool_members[0]['monitor_status'] \
            == monitor_status

    # Verify the health monitor reports the backend as up
    retry_for(seconds=20).or_fail(
        assert_status,
        msg='Health monitor does not report "up" status after 20s',
        load_balancer_status='running',
        monitor_status='up',
    )

    # Shutdown the backend server and verify the health monitor goes down
    backend.stop()
    retry_for(seconds=20).or_fail(
        assert_status,
        msg='Health monitor does not report "down" status after 20s',
        # As all pool members are down the LB is in status "error"
        load_balancer_status='error',
        monitor_status='down',
    )

    # Start the backend again and verfiy the health monitor goes up
    # The test web server on the backend has to be started as well. It's not
    # configured as a persistent systemd unit.
    backend.start()
    setup_lbaas_http_test_server(backend, ssl)
    retry_for(seconds=20).or_fail(
        assert_status,
        msg='Health monitor does not report "up" status after 20s',
        load_balancer_status='running',
        monitor_status='up',
    )


@pytest.mark.parametrize('action', ('disable-enable', 'remove-add'))
def test_pool_member_change(server, create_load_balancer_scenario,
                            action):
    """ A pool member can be removed and added back or disabled and reenabled
    to a load balancer pool without any disruption to connections not
    terminated on this pool member.

    """

    # This test uses a function scoped prober because we are going to start
    # long running downloads which should not impact other tests.
    prober = server

    # Create a load balancer setup with 2 backends on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=2,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # download an endless file to create "persistent" connections
    backend_first = start_persistent_download(prober, load_balancer, backends,
                                              'wget-first')

    # second download to create a "persistent" connection to the other backend
    backend_second = start_persistent_download(prober, load_balancer, backends,
                                               'wget-second')

    # Assert both backends got a request
    assert set(backends) == {backend_first, backend_second}

    # Get pool member serving the first download
    member_first_name = \
        f'{RESOURCE_NAME_PREFIX}-pool-member-{backend_first.name}'
    member_first = next(x for x in load_balancer.pool_members
                        if x['name'] == member_first_name)

    # Remove/Disable member_first from the pool
    if action == 'remove-add':
        load_balancer.remove_pool_member(pool, member_first)
    elif action == 'disable-enable':
        load_balancer.toggle_pool_member(pool, member_first, enabled=False)

    # Assert the persistent download to backend_second is still active
    assert prober.output_of(
        'systemctl --user is-active wget-second') == 'active'

    # Assert the persistent download to backend_first is also still active.
    # Already active connections are not affected by the configuration change.
    assert prober.output_of(
        'systemctl --user is-active wget-first') == 'active'

    # Assert requests only go to backend_second after some time
    retry_for(seconds=20).or_fail(
        load_balancer.verify_backend,
        msg=f'Backend {backend_first.name} not removed from the pool '
            f'within 20s.',
        prober=prober,
        backend=backend_second,
        # If 5 consecutive requests go to backend_second we assume
        # backend_first no longer receives traffic
        count=5,
    )

    # Add the backend back to the pool
    if action == 'remove-add':
        load_balancer.add_pool_member(pool, backend_first, private_network)
    elif action == 'disable-enable':
        load_balancer.toggle_pool_member(pool, member_first, enabled=True)

    # Assert the other backend is added back to the pool and starts to serve
    # requests again
    retry_for(seconds=10).or_fail(
        load_balancer.verify_backend,
        msg=f'Backend {backend_first.name} not added back to the pool '
            f'within 10s.',
        prober=prober,
        backend=backend_first,
    )

    # Assert the persistent downloads are still active
    assert prober.output_of(
        'systemctl --user is-active wget-first') == 'active'
    assert prober.output_of(
        'systemctl --user is-active wget-second') == 'active'


def test_private_load_balancer_frontend(
        create_server, image, create_load_balancer_scenario, private_network,
):
    """ A load balancer can use a private network as it's frontend (VIP)
    network to receive connections.

    """

    # Create a function scoped prober as we are going to attach this server
    # the the private frontend network
    prober = create_server(image=image['slug'], use_private_network=True)

    # Add a subnet to the private frontend network
    frontend_subnet = private_network.add_subnet(cidr='192.168.100.0/24')

    # Attach the frontend network to the server used as an LB client
    prober.update(
        interfaces=[
            {'network': 'public'},
            {'network': prober.interfaces[1]['network']['uuid']},
            {'network': private_network.info['uuid']},
        ],
    )

    prober.enable_dhcp_in_networkd(prober.interfaces[-1])

    # Create a private load balancer setup with 1 backend on a private network
    load_balancer, listener, pool, (backend, ), backend_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='least_connections',
            frontend_subnet=frontend_subnet,
            prober=prober,
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
    )

    # Assert the backend is reachable from the prober over the load balancer
    load_balancer.verify_backend(prober, backend)


def test_floating_ip(prober, create_load_balancer_scenario, floating_ip):
    """ A Floating IP can be assigned to a load balancer and used to receive
    client connections.

    """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
    )

    # Assign Floating IP to load balancer
    floating_ip.assign(load_balancer=load_balancer)

    # Assert load balancer is reachable on the Floating IP after at most 20s
    wait_for_url_ready(
        build_http_url(floating_ip.address),
        prober,
        timeout=20,
    )

    # Assert the load balancer is serving content on the Floating IP
    assert prober.http_get(
        url=build_http_url(floating_ip.address, path='/hostname')
    ) == backend.name


def test_floating_ip_reassign(prober, create_load_balancer_scenario,
                              floating_ipv4, server):
    """ Test if a Floating IP can be reassigned from a server to a load
    balancer, to another load balancer and back to a server.

    """

    # Create two load balancer setups with one backend each
    ((load_balancer1, listener1, pool1, (backend1, ), private_network1),
     (load_balancer2, listener2, pool2, (backend2, ), private_network2)) = \
        in_parallel(create_load_balancer_scenario,
                    [{'name': 'lb1',
                      'num_backends': 1,
                      'algorithm': 'round_robin',
                      'port': 80,
                      'pool_protocol': 'tcp',
                      'ssl': False,
                      'health_monitor_type': None,
                      'allowed_cidrs': None,
                      },
                     {'name': 'lb2',
                      'num_backends': 1,
                      'algorithm': 'round_robin',
                      'port': 80,
                      'pool_protocol': 'tcp',
                      'ssl': False,
                      'health_monitor_type': None,
                      'allowed_cidrs': None,
                      }])

    # Assign Floating IP to the server
    floating_ipv4.assign(server=server)

    # Configure the Floating IP on the server
    server.configure_floating_ip(floating_ipv4)

    # Check if the Floating IP is reachable (wait up to 15 seconds)
    prober.ping(floating_ipv4, count=1, tries=15)

    # Assign Floating IP to the first load balancer
    floating_ipv4.assign(load_balancer=load_balancer1)

    # Wait for up to 20s for the Floating IP to become ready
    wait_for_url_ready(
        f'http://{floating_ipv4.address}/hostname',
        prober,
        content=backend1.name,
        timeout=20,
    )

    # Check if backend1 (via load_balancer1) receives requests on the
    # Floating IP
    assert prober.http_get(f'http://{floating_ipv4.address}/hostname') \
        == backend1.name

    # Assign Floating IP to the second load balancer
    floating_ipv4.assign(load_balancer=load_balancer2)

    # Wait for up to 20s for the Floating IP to become ready
    wait_for_url_ready(
        f'http://{floating_ipv4.address}/hostname',
        prober,
        content=backend2.name,
        timeout=20,
    )

    # Check if backend2 (via load_balancer2) receives requests on the
    # Floating IP
    assert prober.http_get(f'http://{floating_ipv4.address}/hostname') \
        == backend2.name

    # Assign Floating IP back to the server
    floating_ipv4.assign(server=server)

    # Check if the Floating IP is reachable (wait up to 15 seconds)
    prober.ping(floating_ipv4, count=1, tries=15)

    # Repeat tests with UDP

    # Assign Floating IP to the first load balancer
    floating_ipv4.assign(load_balancer=load_balancer1)

    # Setup UDP test servers on both backends
    setup_lbaas_udp_test_server(backend1)
    setup_lbaas_udp_test_server(backend2)

    # Create UDP pools for both load balancers
    udp_pool1 = load_balancer1.add_pool(f'lb1-udp-pool', 'round_robin', 'udp')
    udp_pool2 = load_balancer2.add_pool(f'lb2-udp-pool', 'round_robin', 'udp')

    # Add backend members to their respective UDP pools
    load_balancer1.add_pool_member(udp_pool1, backend1, private_network1)
    load_balancer2.add_pool_member(udp_pool2, backend2, private_network2)

    # Add UDP listeners on port 80 for both load balancers
    load_balancer1.add_listener(udp_pool1, 80, protocol='udp')
    load_balancer2.add_listener(udp_pool2, 80, protocol='udp')

    # Helper function to verify UDP responses from expected backend
    def assert_udp_response(ip, expected_backend):
        expected_response = (
            f'Backend server running on '
            f'{expected_backend.name} {expected_backend.uuid}.'
        )
        actual_response = prober.udp_get(ip, 80)
        assert expected_response == actual_response

    # Check backend1 receives UDP requests on the Floating IP
    retry_for(seconds=20).or_fail(
        lambda: assert_udp_response(floating_ipv4, backend1),
        msg='Assertion not met, when using backend1.',
    )

    # Assign Floating IP to the second load balancer
    floating_ipv4.assign(load_balancer=load_balancer2)

    # Check backend2 receives UDP requests on the Floating IP
    retry_for(seconds=20).or_fail(
        lambda: assert_udp_response(floating_ipv4, backend2),
        msg='Assertion not met, when using backend2.',
    )


def test_frontend_allowed_cidr(prober, create_load_balancer_scenario):
    """ Frontend connection source IPs can be restricted by CIDRs. This works
    for IPv4 and IPv6. The access restrictions can be updated on existing
    load balancers.

    """

    # Create a load balancer setup with one backend on a private network
    # Restrict access to the prober
    load_balancer, listener, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=[
                f'{prober.ip("public", 4)}/32',
                f'{prober.ip("public", 6)}/128',
            ],
    )

    # Assert the load balancer works on IPv4 and IPv6
    prober.http_get(f'http://{load_balancer.vip(4)}/')
    prober.http_get(f'http://[{load_balancer.vip(6)}]/')

    # Restrict access to only the IPv4 address of the prober
    load_balancer.update_listener(
        listener,
        allowed_cidrs=[f'{prober.ip("public", 4)}/32'],
    )

    # Wait some time for the configuration to be applied. Unfortunately the API
    # does not provide this information
    sleep(15)

    # Assert the load balancer works on IPv4 and DOES NOT work on IPv6
    prober.http_get(f'http://{load_balancer.vip(4)}/')
    with pytest.raises(AssertionError):
        prober.http_get(f'http://[{load_balancer.vip(6)}]/')

    # Restrict access to only the IPv6 address of the prober
    load_balancer.update_listener(
        listener,
        allowed_cidrs=[f'{prober.ip("public", 6)}/128'],
    )

    # Wait some time for the configuration to be applied. Unfortunately the API
    # does not provide this information
    sleep(15)

    # Assert the load balancer works on IPv6 and DOES NOT work on IPv4
    prober.http_get(f'http://[{load_balancer.vip(6)}]/')
    with pytest.raises(AssertionError):
        prober.http_get(f'http://{load_balancer.vip(4)}/')

    # Restrict access to documentation IPv4 and IPv6 networks
    # The prober should no longer have access
    load_balancer.update_listener(
        listener,
        allowed_cidrs=['192.0.2.0/24', '2001:db8::/32'],
    )

    # Wait some time for the configuration to be applied. Unfortunately the API
    # does not provide this information
    sleep(15)

    # Assert the load balancer does not work on IPv4 and IPv6
    with pytest.raises(AssertionError):
        prober.http_get(f'http://{load_balancer.vip(4)}/')
    with pytest.raises(AssertionError):
        prober.http_get(f'http://[{load_balancer.vip(6)}]/')


@pytest.mark.parametrize('proxy_protocol', ('proxy', 'proxyv2'))
def test_proxy_protocol(prober, create_load_balancer_scenario, proxy_protocol):
    """ The load balancer can be configured to pass source IP information to
    the backend server via the Proxy Protocol. Version 1 and 2 of the Proxy
    Protocol are supported.

    """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol=proxy_protocol,
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
    )

    # Assert the PROXY protocol header gets logged for IPv4
    expected_log_line = {
        'proxy': f'PROXY V1 header received: '
                 f'TCP4 {prober.ip("public", 4)} {load_balancer.vip(4)}',
        'proxyv2': f'PROXY V2 header received: PROXY '
                   f'TCP4 {prober.ip("public", 4)} {load_balancer.vip(4)}'
    }
    prober.http_get(load_balancer.build_url(addr_family=4))
    logs = backend.output_of('journalctl --user-unit lbaas-http-test-server')
    assert expected_log_line[proxy_protocol] in logs

    # Assert the PROXY protocol header gets logged for IPv6
    expected_log_line = {
        'proxy': f'PROXY V1 header received: '
                 f'TCP6 {prober.ip("public", 6)} {load_balancer.vip(6)}',
        'proxyv2': f'PROXY V2 header received: PROXY '
                   f'TCP6 {prober.ip("public", 6)} {load_balancer.vip(6)}'
    }
    prober.http_get(load_balancer.build_url(addr_family=6))
    logs = backend.output_of('journalctl --user-unit lbaas-http-test-server')
    assert expected_log_line[proxy_protocol] in logs


def test_ping(prober, create_load_balancer_scenario, floating_ipv4,
              floating_ipv6):
    """ The load balancer answers to ICMP echo requests (ping) on all VIP
    addresses and assigned Floating IPs.

    """

    # Create simple load balancer setup with public VIP
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='round_robin',
            port=80,
            pool_protocol='tcp',
            ssl=False,
            health_monitor_type=None,
            allowed_cidrs=None,
        )

    # Verify the load balancer is pingable on IPv4 and IPv6 VIP
    prober.ping(load_balancer.vip(4), count=1, tries=15)
    prober.ping(load_balancer.vip(6), count=1, tries=15)

    # Assign Floating IPs to load balancer
    floating_ipv4.assign(load_balancer=load_balancer)
    floating_ipv6.assign(load_balancer=load_balancer)

    # Verify the load balancer is pingable on the Floating IPs
    prober.ping(floating_ipv4.address, count=1, tries=15)
    prober.ping(floating_ipv6.address, count=1, tries=15)
