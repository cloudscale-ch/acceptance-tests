"""

Flexible Volume Management
==========================

cloudscale.ch offers flexible volume managment with SSD and bulk storage
volumes that can be attached, detached, and resized.

"""

import time
import pytest

from requests.exceptions import HTTPError
from util import extract_number

# Volume sizes are measured in GiB
GiB = 1024 ** 3
MiB = 1024 ** 2


def test_attach_and_detach_volume_on_all_images(server, volume, image):
    """ Volumes can be dynamically attached and detached from servers.

    """

    # Attach the volume to the server
    volume.attach(server)

    # Give some time for the change to actually propagate
    time.sleep(5)

    # Virtio block device serial numbers contain at least the first 20 bytes
    # of the Volume UUID. On newer compute hosts this may be the full UUID.
    #
    # Note: The CSI driver relies on this behavior, changes to it may require
    # an upgrade of the CSI driver.
    volume_paths = server.output_of(
        f"ls -1 /dev/disk/by-id/*{volume.uuid[:20]}*").splitlines()

    # Some images refer to the same volume twice
    assert 1 <= len(volume_paths) <= 2

    # Check that volume is present
    assert server.file_path_exists(volume_paths[0])

    # Detach volume from server
    volume.detach()

    # Give some time for the change to actually propagate
    time.sleep(5)

    # Check that volume is no longer present
    assert not server.file_path_exists(volume_paths[0])


def test_expand_volume_online_on_all_images(create_server, image):
    """ On first boot, the volume size should be set to a default of 10GiB.

    It can then be live resized.

    """

    # Test the default server which comes with 10GiB of storage
    server = create_server(image=image)

    # Ensure that the device size is 10 GiB
    command = 'lsblk --bytes --nodeps --noheadings --output SIZE /dev/sda'
    assert server.output_of(command) == str(10 * GiB)

    # Resize the root disk to 16 GiB
    server.scale_root_disk(16)

    # Give some time for the change to actually propagate
    time.sleep(5)

    # Ensure that the device has been resized
    command = 'lsblk --bytes --nodeps --noheadings --output SIZE /dev/sda'
    assert server.output_of(command) == str(16 * GiB)


def test_expand_filesystem_online_on_common_images(create_server, image):
    """ Volumes can be resized while the host is online.

    Filesystems commonly have facilities to expand to the added space on the
    block device.

    """

    # This should work with all the images we offer
    server = create_server(image=image)

    # Resize the root disk (default is 10 GiB)
    server.scale_root_disk(16)

    # Give some time for the change to actually propagate
    time.sleep(5)

    # Get the name of the device that contains root
    device = server.output_of('mount | grep -w / | cut -d " " -f 1')

    # Get the partition number of the device that contains root
    partition = extract_number(device)

    # Grow the root partition on the running system
    server.assert_run(f'sudo growpart /dev/sda {partition}')

    # Get the device's filesystem
    fs_type = server.output_of(f'df --output=fstype {device} | tail -n 1')

    # Grow the disk using the appropriate method
    if fs_type == 'ext4':
        server.assert_run(f'sudo resize2fs {device}')
    elif fs_type == 'xfs':
        server.assert_run('sudo xfs_growfs /')
    else:
        raise NotImplementedError(f"No known resize command for {fs_type}")

    # Ensure that the device has been resized.
    # The /boot and /boot/efi partition may take up to 1249 MiB of space.
    assert (16 * GiB - 1249 * MiB) <= server.fs_size(device) <= 16 * GiB


def test_expand_filesystem_on_boot_on_common_images(create_server, image):
    """ Volumes can be resized while the server is stopped.

    Filesystems commonly grow to expand to the added space on the block device
    during boot.

    """

    # Create the server
    server = create_server(image=image)

    # Get the name of the device that contains root
    device = server.output_of('mount | grep -w / | cut -d " " -f 1')

    # Stop the server
    server.stop()

    # Resize the block device
    server.scale_root_disk(16)

    # Start the server
    server.start()

    # Ensure that the device has been resized.
    # The /boot and /boot/efi partition may take up to 1249 MiB of space.
    assert (16 * GiB - 1249 * MiB) <= server.fs_size(device) <= 16 * GiB


def test_maximum_number_of_volumes(server, create_volume):
    """ It is possible to attach up to 128 additional volumes to a server.

    """

    # Attach 127 volumes to the server (1 is already attached)
    for _ in range(127):
        volume = create_volume(size=10, volume_type="ssd")
        volume.attach(server)

    # The server now has 128 disks
    disks = server.output_of('lsblk | grep disk').splitlines()
    assert len(disks) == 128

    # The first disk is named 'sda'
    assert disks[0].split(' ')[0] == 'sda'

    # The last disk is named 'sddx'
    assert disks[-1].split(' ')[0] == 'sddx'

    # Try to attach one more volume (to reach 129), which fails
    with pytest.raises(HTTPError) as error:
        volume = create_volume(size=10, volume_type="ssd")
        volume.attach(server)

    # A specific error messages is returned
    assert error.value.response.json()['detail'] == (
        "Due to internal limitations, it is currently not possible "
        "to attach more than 128 volumes.")
