"""

Objects Storage
===============

Using an S3 compatible API, customers may store data in our Object Storage
service and serve it without the need to manage their own storage system.

"""

import boto3
import requests
import secrets


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
