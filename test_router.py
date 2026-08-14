"""

Routers
=======

You can connect private networks using routers:

"""

from util import in_parallel


def test_create_router(create_router):
    """ Test to create a router. """

    router = create_router()
    assert router.status == 'active'
    assert not router.internet_gateway


def test_internet_gateway(
        create_router,
        create_server,
        image,
        private_network,
):
    """ Test to create an internet gateway. """

    # Using a private network
    subnet = private_network.add_subnet(cidr='192.168.100.0/24',
                                        gateway_address='192.168.100.1')

    # Create router connecting the private network to public internet
    router = create_router(internet_gateway=True)
    router.add_interface(
        network=private_network.uuid,
        subnet=subnet.uuid,
        address="192.168.100.1"
    )

    # Create prober/jumpost
    prober = create_server(
        name='jumphost',
        image=image,
        interfaces=[
            {'network': 'public'},
            {'network': private_network.uuid},
        ],
    )

    # Create a server connected only to it's own private network
    private_server = create_server(
        name='private_server',
        image=image,
        interfaces=[{'network': private_network.uuid}],
        jump_host=prober,
    )

    assert router.status == 'active'
    assert router.internet_gateway
    private_server.ping('8.8.8.8', tries=5, wait=1)


def test_router_connected_private_networks(
        create_router,
        create_server,
        image,
        create_private_network,
):
    """ Test to create an router between two private networks. """

    # Create two private networks
    private_network_a = create_private_network()
    private_network_b = create_private_network()
    subnet_a = private_network_a.add_subnet(cidr='192.168.10.0/24',
                                            gateway_address='192.168.10.1')
    subnet_b = private_network_b.add_subnet(cidr='192.168.11.0/24',
                                            gateway_address='192.168.11.1')

    # Create router connecting the private networks
    router = create_router(internet_gateway=False)
    router.add_interface(
        network=private_network_a.uuid,
        subnet=subnet_a.uuid,
        address="192.168.10.1"
    )
    router.add_interface(
        network=private_network_b.uuid,
        subnet=subnet_b.uuid,
        address="192.168.11.1"
    )

    # Create prober/jumpost
    prober = create_server(
        name='jumphost',
        image=image,
        interfaces=[
            {'network': 'public'},
            {'network': private_network_a.uuid},
            {'network': private_network_b.uuid},
        ],
    )

    # Create servers each connected only to it's own private network
    s1, s2 = in_parallel(create_server, instances=(
        {
            'name': 's1',
            'image': image,
            'interfaces': [{'network': private_network_a.uuid}],
            'jump_host': prober,
        },
        {
            'name': 's2',
            'image': image,
            'interfaces': [{'network': private_network_b.uuid}],
            'jump_host': prober,
        },
    ))

    assert router.status == 'active'
    assert not router.internet_gateway

    # Each VM can ping the other over private IPv4
    s1.ping(s2.ip('private', 4), tries=5, wait=1)
    s2.ping(s1.ip('private', 4), tries=5, wait=1)
