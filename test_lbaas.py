"""

Load Balancer as a Service
==========================

You can create, modify and delete TCP load balancers.

"""
import pytest

from time import sleep
from util import construct_http_url
from util import get_backends_for_request
from util import in_parallel
from util import RESOURCE_NAME_PREFIX
from util import retry_for
from util import setup_lbaas_backend
from util import setup_lbaas_http_test_server
from util import start_persistent_download


def test_simple(prober, create_load_balancer_scenario):
    """ Create a simple TCP load balancer with one backend. """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(num_backends=1)

    # Test if the load balancer works on IPv4
    content = load_balancer.get_url(prober, addr_family=4)
    assert 'Backend server running on ' in content

    # Test if the load balancer works on IPv6
    content = load_balancer.get_url(prober, addr_family=6)
    assert 'Backend server running on ' in content


def test_end_to_end(prober, create_load_balancer_scenario):
    """ Multi backend load balancer end-to-end test scenario.

    * Load balancer on a public network with multiple backend servers on a
      private network
    * Send TCP traffic through the LB and verify backend answers

    """

    # Create a load balancer setup with two backends on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario()

    # Issue 10 requests on IPv4 and IPv6 to the load balancer
    for i in range(10):
        content = load_balancer.get_url(prober, addr_family=4)
        assert 'Backend server running on' in content

        content = load_balancer.get_url(prober, addr_family=6)
        assert 'Backend server running on' in content

    # Assert logs on both backend servers show they received traffic
    assert set(backends) == set(get_backends_for_request(backends))


def test_multi_listener(prober, create_load_balancer_scenario):
    """ Two load balancer listeners connected to the same pool.

    """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener1, pool, (backend, ), private_network = \
        create_load_balancer_scenario(num_backends=1)

    # Add an additional listener on Port 81
    load_balancer.add_listener('listener-81', pool, 81)

    # Assert the LB works on port 80
    assert prober.http_get(f'http://{load_balancer.vip(4)}/hostname') \
        == backend.name

    # Assert backend is also reachable on port 81 (try for 30s as it might not
    # be ready yet)
    retry_for(seconds=30).or_fail(
        load_balancer.verify_backend,
        msg='Additional listener not reachable within 30s.',
        prober=prober,
        backend=backend,
        port=81,
    )


def test_multi_listener_multi_pool(prober, create_server, image,
                                   create_private_network,
                                   create_load_balancer_scenario):
    """ Two listeners connected to their own pool of member servers each.

    """

    # Create a load balancer setup with one backends on a private network
    load_balancer, listener1, pool1, (backend1, ), private_network1 = \
        create_load_balancer_scenario(num_backends=1)

    # Create an additonal backend server
    backend2 = create_server(
        name='backend2',
        image=image,
        use_public_network=False,
        use_private_network=True,
        jump_host=prober,
    )

    # Create a second pool
    pool2 = load_balancer.add_pool(f'lb-pool-2', 'round_robin', 'tcp')

    # Create an additional backend network
    private_network2 = create_private_network(auto_create_ipv4_subnet=True)

    # Create an additional backend server for the second pool
    setup_lbaas_backend(backend2, load_balancer, pool2, private_network2)

    # Add an additional listener on Port 81 for the second pool
    load_balancer.add_listener('listener-81', pool2, 81)

    # Assert backend1 is reachable on port 80 (must already be ready)
    assert prober.http_get(f'http://{load_balancer.vip(4)}/hostname') \
        == backend1.name

    # Assert backend2 is reachable on port 81 (try for 20s as it might not be
    # ready yet)
    retry_for(seconds=30).or_fail(
        load_balancer.verify_backend,
        msg='Additional backend and/or listener not reachable within 30s.',
        prober=prober,
        backend=backend2,
        port=81,
    )


def test_algo_round_robin(prober, create_load_balancer_scenario):
    """ The round_robin balancing algorithm schedules connections in turn among
    pool members.

    """
    # Create a load balancer setup with 3 backends on a private network
    num_backends = 3
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(num_backends=num_backends)

    # Issue a request to each backend to get the round robin order
    backend_order = [load_balancer.get_url(prober, url='/hostname')
                     for i in range(num_backends)]

    # Assert all backends got a request
    assert len(set(backend_order)) == num_backends

    # Issue 10 requests to each backend and verify round robin distribution
    for n in range(10 * num_backends):
        # Assert the correct backend received the request
        backend_hit = load_balancer.get_url(prober, url='/hostname')
        assert backend_hit == backend_order[n % num_backends]


def test_algo_source_ip(prober, create_load_balancer_scenario, create_server,
                        image):
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
        backend_per_client[client.name] = load_balancer.get_url(
            client,
            url='/hostname',
        )

    # Issue 10 requests and verify the correct backend receives the request
    for n in range(10):
        for client in clients:
            backend_hit = load_balancer.get_url(client, url='/hostname')
            assert backend_per_client[client.name] == backend_hit


def test_algo_least_connections(server, create_load_balancer_scenario):
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
        )

    # start a persistent endless download of random data to "block" one backend
    blocked_backend = start_persistent_download(prober, load_balancer,
                                                backends)

    # Verify requests go to the other backend as it has less active
    # connections
    for i in range(10):
        backend_hit = load_balancer.get_url(prober, url='/hostname')

        assert backend_hit != blocked_backend.name


@pytest.mark.parametrize('health_monitor_type',
                        ['ping', 'tcp', 'http', 'https', 'tls-hello'])
def test_health_monitors(prober, create_load_balancer_scenario,
                         health_monitor_type):
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
        load_balancer.add_pool_member(
            pool, member_first["name"],
            member_first["protocol_port"],
            member_first["address"],
            member_first["subnet"]["uuid"],
        )
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


def test_private_frontend(create_server, image, create_load_balancer_scenario,
                          private_network):
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

    # Create a private load balancer setup with 1 backend on a private network
    load_balancer, listener, pool, (backend, ), backend_network = \
        create_load_balancer_scenario(
            num_backends=1,
            algorithm='least_connections',
            frontend_subnet=frontend_subnet,
            prober=prober,
    )

    # Assert the backend is reachable from the prober over the load balancer
    load_balancer.verify_backend(prober, backend)


def test_floating_ip(prober, create_load_balancer_scenario, floating_ip):
    """ A Floating IP can be assigned to a load balancer and used to receive
    client connections.

    """

    # Create a load balancer setup with one backend on a private network
    load_balancer, listener, pool, backends, private_network = \
        create_load_balancer_scenario(num_backends=1)

    # Assign Floating IP to load balancer
    floating_ip.assign(load_balancer=load_balancer)

    # Assert load balancer is reachable on the Floating IP after at most 20s
    retry_for(seconds=20).or_fail(
        prober.http_get,
        msg='Load balancer not reachable on Floating IP after 20s.',
        url=construct_http_url(floating_ip.address),
    )


def test_floating_ip_reassign(prober, create_load_balancer_scenario,
                              floating_ipv4, server):
    """ Test if a Floating IP can be reassigned from a server to a load
    balancer, to another load balancer and back to a server.

    """

    def check_content(url, content):
        assert prober.http_get(url) == content

    # Create two load balancer setups with one backend each
    ((load_balancer1, listener1, pool1, (backend1, ), private_network1),
     (load_balancer2, listener1, pool2, (backend2, ), private_network2)) = \
        in_parallel(create_load_balancer_scenario,
                    [{'name': 'lb1', 'num_backends': 1},
                     {'name': 'lb2', 'num_backends': 1}])

    # Assign Floating IP to the server
    floating_ipv4.assign(server=server)

    # Configure the Floating IP on the server
    server.configure_floating_ip(floating_ipv4)

    # Check if the Floating IP is reachable (wait up to 15 seconds)
    prober.ping(floating_ipv4, count=1, tries=15)

    # Assign Floating IP to the first load balancer
    floating_ipv4.assign(load_balancer=load_balancer1)

    # Check if backend1 (via load_balancer1) receives requests on the
    # Floating IP
    retry_for(seconds=20).or_fail(
        check_content,
        msg='Floating IP not working within 20s',
        url=f'http://{floating_ipv4.address}/hostname',
        content=backend1.name,
    )

    # Assign Floating IP to the second load balancer
    floating_ipv4.assign(load_balancer=load_balancer2)

    # Check if backend2 (via load_balancer2) receives requests on the
    # Floating IP
    retry_for(seconds=20).or_fail(
        check_content,
        msg='Floating IP not working within 20s',
        url=f'http://{floating_ipv4.address}/hostname',
        content=backend1.name,
    )

    # Assign Floating IP back to the server
    floating_ipv4.assign(server=server)

    # Check if the Floating IP is reachable (wait up to 15 seconds)
    prober.ping(floating_ipv4, count=1, tries=15)


def test_allowed_cidr(prober, create_load_balancer_scenario):
    """ Frontend connection source IPs can be restricted by CIDRs. This works
    for IPv4 and IPv6. The access restrictions can be updated on existing
    load balancers.

    """

    # Create a load balancer setup with one backend on a private network
    # Restrict access to the prober
    load_balancer, listener, pool, (backend, ), private_network = \
        create_load_balancer_scenario(
            num_backends=1,
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
            pool_protocol=proxy_protocol
    )

    # Assert the PROXY protocol header gets logged for IPv4
    expected_log_line = {
        'proxy': f'PROXY V1 header received: '
                 f'TCP4 {prober.ip("public", 4)} {load_balancer.vip(4)}',
        'proxyv2': f'PROXY V2 header received: PROXY '
                   f'TCP4 {prober.ip("public", 4)} {load_balancer.vip(4)}'
    }
    load_balancer.get_url(prober, addr_family=4)
    logs = backend.output_of('journalctl --user-unit lbaas-http-test-server')
    assert expected_log_line[proxy_protocol] in logs

    # Assert the PROXY protocol header gets logged for IPv6
    expected_log_line = {
        'proxy': f'PROXY V1 header received: '
                 f'TCP6 {prober.ip("public", 6)} {load_balancer.vip(6)}',
        'proxyv2': f'PROXY V2 header received: PROXY '
                   f'TCP6 {prober.ip("public", 6)} {load_balancer.vip(6)}'
    }
    load_balancer.get_url(prober, addr_family=6)
    logs = backend.output_of('journalctl --user-unit lbaas-http-test-server')
    assert expected_log_line[proxy_protocol] in logs
