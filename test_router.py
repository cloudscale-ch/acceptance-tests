import time


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

    subnet = private_network.add_subnet(cidr='192.168.100.0/24')

    router = create_router(internet_gateway=True)

    interface = router.add_interface(
        network=private_network.uuid,
        subnet=subnet.uuid,
        address="192.168.100.1"
    )

    prober = create_server(
        name='jumphost',
        image=image,
        interfaces=[
            {'network': 'public'},
            {'network': private_network.uuid},
        ],
    )

    private_server = create_server(
        name='private_server',
        image=image,
        interfaces=[{'network': private_network.uuid}],
        jump_host=prober,
    )

    assert router.status == 'active'
    assert router.internet_gateway
    private_server.ping('127.0.0.1', tries=5, wait=1)

    router.remove_interface(interface)
    assert not router.interfaces
    time.sleep(2.5)


def test_router_connected_private_networks(
        create_private_network,
        create_router,
        create_server,
        image,
):
    """ Test to create an router between two private networks. """
    pass
