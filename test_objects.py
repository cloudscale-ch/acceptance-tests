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
from util import oneliner


def test_bucket_urls(objects_endpoint, access_key, secret_key):
    """ We can create a bucket using the official AWS SDK for Python, upload
    an object, and make it available publicly. It is then available as follows:

    - <bucket>.objects.rma|lpg.cloudscale.ch/<key>
    - objects.rma|lpg.cloudscale.ch/<bucket>/<key>

    """

    # Establish a connection using the official AWS SDK for Python
    s3 = boto3.client(
        's3',
        endpoint_url=f"https://{objects_endpoint}",
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

    # Read the expected URLs using an anonymous HTTP client
    urls = (
        f"https://{objects_endpoint}/{bucket}/key.txt",
        f"https://{bucket}.{objects_endpoint}/key.txt",
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
    server.assert_run(oneliner("""
        sudo apt update;
        sudo apt install -y podman jq;
        sudo systemd-run --unit webhook.service
            podman run
                --name webhook
                --publish 80:8080
                --env LOG_WITHOUT_NEWLINE=true
                --env DISABLE_REQUEST_LOGS=true
                docker.io/mendhak/http-https-echo:38
    """))

    # Get an SNS client (Simple Notification Service)
    sns = boto3.client(
        'sns',
        endpoint_url=f"https://{objects_endpoint}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name='default',
    )

    # Create a test topic
    name = f"at-{secrets.token_hex(8)}"

    topic = sns.create_topic(Name=name, Attributes={
        "push-endpoint": f"http://{server.ip('public', 4)}",
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

        count = int(server.output_of(oneliner("""
            sudo journalctl CONTAINER_NAME=webhook | grep pre-check | wc -l
        """)))

        if count:
            break

        time.sleep(1)

    # Generate multiple notifications
    for i in range(3):
        bucket.put_object(Key=f'count-{i}', Body=b'')

    # Ensure they were received (excluding the pre-check objects from above)
    messages = [json.loads(m)['json'] for m in server.output_of(oneliner(r"""
        sudo journalctl CONTAINER_NAME=webhook -o json
        | jq -r .MESSAGE
        | grep -v pre-check
        | grep -E '^\{'
    """)).splitlines()]

    # A single message may contain multiple records
    records = flatten(m['Records'] for m in messages)
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
