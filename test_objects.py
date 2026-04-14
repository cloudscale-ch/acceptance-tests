"""

Objects Storage
===============

Using an S3 compatible API, customers may store data in our Object Storage
service and serve it without the need to manage their own storage system.

"""

import boto3
import json
import requests
import secrets
import time

from util import flatten
from util import setup_notification_endpoint

from urllib.parse import urlparse


def test_bucket_urls(objects_endpoint, access_key, secret_key):
    """ We can create a bucket using the official AWS SDK for Python, upload
    an object, and make it available publicly. It is then available as follows:

    - <bucket>.objects.rma|lpg.cloudscale.ch/<key>
    - objects.rma|lpg.cloudscale.ch/<bucket>/<key>

    """

    # Establish a connection using the official AWS SDK for Python
    s3 = boto3.client(
        's3',
        endpoint_url=objects_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    # Generate a bucket name that has not been used yet (bucket names are
    # global across all our object storage endpoints).
    bucket = f"at-{secrets.token_hex(8)}"

    # Create the bucket
    s3.create_bucket(Bucket=bucket)

    # Upload an object
    s3.put_object(
        Bucket=bucket,
        Key='key.txt',
        Body=b'test',
        ACL='public-read',
    )

    parts = urlparse(objects_endpoint)

    # Read the expected URLs using an anonymous HTTP client
    urls = (
        f"{objects_endpoint}/{bucket}/key.txt",
        f"{parts.scheme}://{bucket}.{parts.netloc}/key.txt",
    )

    for url in urls:
        response = requests.get(url)
        assert response.status_code == 200
        assert response.text == "test"


def test_notifications(
    bucket,
    access_key,
    secret_key,
    objects_endpoint,
    server,
    region,
):
    """ Using S3 SNS (Simple Notification Service) we can be informed via
    webhooks, when something changes on a bucket.

    """

    # Run a service that can act as a webhook endpoint
    setup_notification_endpoint(server)

    # Get an SNS client (Simple Notification Service)
    sns = boto3.client(
        'sns',
        endpoint_url=objects_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name='default',
    )

    # Create a test topic
    name = f"at-{secrets.token_hex(8)}"

    topic = sns.create_topic(Name=name, Attributes={
        "push-endpoint": f"http://{server.ip('public', 4)}:8000",
    })

    # Get notified whenever an object is created
    bucket.Notification().put(NotificationConfiguration={
        "TopicConfigurations": [
            {
                "Id": name,
                "TopicArn": topic['TopicArn'],
                "Events": ["s3:ObjectCreated:*"]
            }
        ]
    })

    # We have to wait a moment for the configuration to propagate
    timeout = time.monotonic() + 30

    while time.monotonic() < timeout:
        bucket.put_object(Key='pre-check', Body=b'')

        found = False
        with server.get_file_handle('notification-body.log') as notifications:
            for line in notifications:
                n = json.loads(line)
                for r in n['Records']:
                    if r['s3']['object']['key'] == 'pre-check':
                        found = True

        if found:
            break

        time.sleep(1)

    # Generate multiple notifications
    for i in range(3):
        bucket.put_object(Key=f'count-{i}', Body=b'')

    # Ensure they were received (excluding the pre-check objects from above)
    with server.get_file_handle('notification-body.log') as notification_log:
        notifications = [json.loads(line) for line in notification_log
                         if 'pre-check' not in line]

    # A single message may contain multiple records
    records = flatten(m['Records'] for m in notifications)
    assert len(records) == 3

    # The records are sent in order
    assert records[0]['s3']['object']['key'] == 'count-0'
    assert records[1]['s3']['object']['key'] == 'count-1'
    assert records[2]['s3']['object']['key'] == 'count-2'

    # They all share some properties
    for record in records:
        assert record['eventName'] == 'ObjectCreated:Put'
        assert record['awsRegion'] == region
        assert record['s3']['bucket']['name'] == bucket.name
