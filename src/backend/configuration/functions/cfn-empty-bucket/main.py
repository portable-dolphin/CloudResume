from common.common import SUCCESS, FAILED, send_cfn_signal

import boto3

from botocore.config import Config
from botocore.exceptions import ClientError
from traceback import format_exc


def handler(event, context):
    """
    Deletes all objects within an S3 bucket. This is useful when the need arises to set the bucket policy directly
    instead of via Bucket(..., auto_delete_objects=True).

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the update criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "bucket_name": str,
                "bucket_region": str
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    bucket_name = resource_properties["bucket_name"]
    bucket_region = resource_properties["bucket_region"]

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": bucket_name + bucket_region,
    }

    print(f"- Request type: {request_type}")
    print(f"- Bucket name: {bucket_name}")
    print(f"- Bucket region: {bucket_region}")

    if request_type == "Delete":
        config = Config(
            region_name=bucket_region, signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
        )
        s3_resource = boto3.resource("s3", config=config)

        try:
            bucket = s3_resource.Bucket(bucket_name)
            print("- Deleting all objects in bucket")
            print(bucket.object_versions.all().delete())
            print("- Done")
        except ClientError as e:
            ret["reason"] = e.response["Error"]["Message"]
            send_cfn_signal(**ret)
            print(ret["reason"])
            print(format_exc())
        except Exception as e:
            ret["reason"] = f"Error: {', '.join(e.args)}"
            send_cfn_signal(**ret)
            print(format_exc())

    ret["status"] = SUCCESS
    send_cfn_signal(**ret)
    return
