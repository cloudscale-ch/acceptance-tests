"""

Custom Images Tests
===================

Customers can upload images and start servers using those images.

"""


def test_custom_image_with_slug(create_server, custom_alpine_image):
    """ Custom images can be used with a slug prefixed with 'custom:' """

    # Create a server that uses that image
    slug = f'custom:{custom_alpine_image.slug}'
    server = create_server(image=slug, username='alpine', use_ipv6=False)

    # Make sure the server can be connected to.
    assert server.output_of('whoami') == 'alpine'


def test_custom_image_with_uuid(create_server, custom_alpine_image):
    """ Custom images can be used with a uuid instead of a slug. """

    # Create a server that uses that image
    image_uuid = custom_alpine_image.uuid
    server = create_server(image=image_uuid, username='alpine', use_ipv6=False)

    # Make sure the server can be connected to.
    assert server.output_of('whoami') == 'alpine'
