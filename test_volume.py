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
from warnings import warn

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


def test_snapshot_volume_attached(server, volume):
    """ Attached volumes can be snapshotted and reverted.

    It is possible to create a snapshot of a volume which is currently attached
    and to revert the volume back to this state.

    Snapshots of volumes taken while they are attached and mounted are crash
    consistent. Some in-flight data might not be in the snapshot, but the
    volume can always be recovered to a consistent state.

    """

    # Attach volume to server and format
    volume.attach(server)
    time.sleep(5)
    server.assert_run('sudo mkfs.ext4 /dev/sdb')
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Create two files. The first is synced to disk with fsync, the second
    # is not synced. Data might still be in-flight.
    server.assert_run(
        'sudo dd if=/dev/zero of=/mnt/synced count=1 bs=1M conv=fsync')
    server.assert_run(
        'sudo dd if=/dev/zero of=/mnt/not-synced count=1 bs=1M')

    # Create snapshot
    snapshot = volume.snapshot('snap')

    # Write test file to volume
    server.assert_run('sudo touch /mnt/after-snapshot')

    # Try reverting while attached (should fail)
    with pytest.raises(HTTPError) as error:
        volume.revert(snapshot)

    # Assert a HTTP 400 BadRequest response with a specific error message
    assert error.value.response.status_code == 400
    assert error.value.response.json()['detail'] == (
        'Cannot revert non-root volumes while they are attached to a server.'
    )

    # Detach volume
    server.assert_run('sudo umount /mnt')
    volume.detach()
    time.sleep(5)

    # Revert volume to snapshot
    volume.revert(snapshot)

    # Reattach and mount the volume
    volume.attach(server)
    time.sleep(5)
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Verify the test files are in the correct state
    assert server.file_path_exists('/mnt/synced')
    assert not server.file_path_exists('/mnt/after-snapshot')

    # "Warn" if the file "not-synced" exists. This is not a failure because
    # depending on the exact timing the data might get written to disk.
    if server.file_path_exists('/mnt/not-synced'):
        warn(
            'File "not-synced" is included in the snapshot although it was '
            'not explicitly synced.',
        )


def test_snapshot_volume_detached(server, volume):
    """ Detached volumes can be snapshotted and reverted.

    It is possible to create a snapshot of a volume which is detached
    and to revert the volume back to this state.

    Snapshots taken while the volume is detached and the filesystem unmounted
    are always fully consistent. All data is written to the disk.

    """

    # Attach and format volume to server
    volume.attach(server)
    time.sleep(5)
    server.assert_run('sudo mkfs.ext4 /dev/sdb')
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Create two files. The first is synced to disk with fsync, the second
    # is not synced. Data might still be in-flight.
    server.assert_run(
        'sudo dd if=/dev/zero of=/mnt/synced count=1 bs=1M conv=fsync')
    server.assert_run('sudo dd if=/dev/zero of=/mnt/not-synced count=1 bs=1M')

    # Unmount the volume
    server.assert_run('sudo umount /mnt')

    # Record the checksum of the first 1GiB of the volume
    # (Checksumming the whole volume would take too much time.)
    sha256_before = server.output_of(
        'sudo dd if=/dev/sdb count=1 bs=1GiB 2>/dev/null | sha256sum')

    # Detach and create snapshot
    volume.detach()
    snapshot = volume.snapshot('snap')

    # Attach and mount the volume again
    volume.attach(server)
    time.sleep(5)
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Write test file to volume
    server.assert_run('sudo touch /mnt/after-snapshot')

    # Detach volume
    server.assert_run('sudo umount /mnt')
    volume.detach()
    time.sleep(5)

    # Revert volume to snapshot
    volume.revert(snapshot)

    # Reattach volume
    volume.attach(server)
    time.sleep(5)

    # Recored the volume checksum
    sha256_after = server.output_of(
        'sudo dd if=/dev/sdb count=1 bs=1GiB 2>/dev/null | sha256sum')

    # Verify the checksums match
    assert sha256_before == sha256_after

    # Mount the volume
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Verify the test files are in the correct state
    assert server.file_path_exists('/mnt/synced')

    # Because the volume was unmounted everything must be synced to disk
    assert server.file_path_exists('/mnt/not-synced')
    assert not server.file_path_exists('/mnt/after-snapshot')


def test_snapshot_root_volume(create_server):
    """ Root volumes can be snapshotted and reverted.

    It is possible to create a snapshot of a root volume and to revert it
    back to the this state.

    Snapshots are crash consistent and data commited to the volume before the
    snapshot is part of the snapshot, but writes in flight can be missing.

    """

    server = create_server(image='debian-13')
    volume = server.root_volume

    # Sync everything written during boot to disk (eg. SSH host keys)
    server.assert_run('sync')

    # Create two files. The first is synced to disk with fsync, the second
    # is not synced. Data might still be in-flight.
    server.assert_run('dd if=/dev/zero of=synced count=1 bs=1M conv=fsync')
    server.assert_run('dd if=/dev/zero of=not-synced count=1 bs=1M')

    # Create snapshot
    snapshot = volume.snapshot('snap')

    # Write test file to volume
    server.assert_run('touch after-snapshot')

    # Try reverting while the server is running (should fail)
    with pytest.raises(HTTPError) as error:
        volume.revert(snapshot)

    # Assert a HTTP 400 BadRequest response with a specific error message
    assert error.value.response.status_code == 400
    assert error.value.response.json()['detail'] == (
        'Root volumes can only be reverted if server state is "stopped".'
    )

    # Stop the server
    server.stop()

    # Revert volume to snapshot
    volume.revert(snapshot)

    # Start the server again
    server.start()

    # Verify the test files are in the correct state
    assert server.file_path_exists('synced')
    assert not server.file_path_exists('after-snapshot')

    # "Warn" if the file "not-synced" exists. This is not a failure because
    # depending on the exact timing the data might get written to disk.
    if server.file_path_exists('not-synced'):
        warn(
            'File "not-synced" is included in the snapshot although it was '
            'not explicitly synced.',
        )


def test_snapshots_in_multiple_steps(server, volume):
    """ Volumes can be snapshotted and reverted in multiple steps.

    The test creates snapshots, whilst creating evidence as follows:
    - /mnt/snapshot-<n>-before
    - /mnt/snapshot-<n>-created

    It then reverts these snapshots in reverse order, from newest to oldest,
    verifying that the evidence on the disk matches expectations.

    """

    # Prepare the volume
    volume.attach(server)
    time.sleep(5)

    server.assert_run('sudo mkfs.ext4 /dev/sdb')
    server.assert_run('sudo mount /dev/sdb /mnt')

    # Function to create snapshots with evidence
    def create_snapshot(n):
        server.assert_run(f'sudo touch /mnt/snapshot-{n}-before && sync')
        snapshot = volume.snapshot(f'snap-{n}')
        server.assert_run(f'sudo touch /mnt/snapshot-{n}-created && sync')
        return snapshot

    # Create three snapshots
    snapshots = []

    for index in range(3):
        snapshots.append(create_snapshot(index + 1))

    # Gather final evidence
    evidence = [server.output_of('ls -1 /mnt/snapshot-*')]

    # Erase all evidence
    server.assert_run('sudo rm /mnt/snapshot-*')
    assert server.output_of('ls -1 /mnt/snapshot-* | wc -l') == "0"

    # Revert snapshots from latest to earliest
    for snapshot in reversed(snapshots):

        # To revert a snapshot, the volume needs to be detached
        server.assert_run('sudo umount /mnt')
        volume.detach()

        # Revert the snapshot and mount it
        volume.revert(snapshot)

        volume.attach(server)
        time.sleep(5)

        server.assert_run('sudo mount /dev/sdb /mnt')

        # List the snapshot evidence
        evidence.insert(0, server.output_of('ls -1 /mnt/snapshot-*'))

        # Delete the snapshot
        volume.api.delete(snapshot['href'])

    # The evidence, before snapshots were restored
    assert evidence.pop().splitlines() == [
        '/mnt/snapshot-1-before',
        '/mnt/snapshot-1-created',
        '/mnt/snapshot-2-before',
        '/mnt/snapshot-2-created',
        '/mnt/snapshot-3-before',
        '/mnt/snapshot-3-created',
    ]

    # After the third snapshot was restored
    assert evidence.pop().splitlines() == [
        '/mnt/snapshot-1-before',
        '/mnt/snapshot-1-created',
        '/mnt/snapshot-2-before',
        '/mnt/snapshot-2-created',
        '/mnt/snapshot-3-before',
    ]

    # After the second snapshot was restored
    assert evidence.pop().splitlines() == [
        '/mnt/snapshot-1-before',
        '/mnt/snapshot-1-created',
        '/mnt/snapshot-2-before',
    ]

    # After the first snapshot was restored
    assert evidence.pop().splitlines() == [
        '/mnt/snapshot-1-before',
    ]

    # Unmount and detach
    server.assert_run('sudo umount /mnt')
    volume.detach()
