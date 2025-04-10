from common.common import SUCCESS, FAILED, send_cfn_signal

import boto3

from botocore.config import Config
from botocore.exceptions import ClientError
from json import dumps
from traceback import format_exc


def handler(event, context):
    """
    Checks the creation or deletion status of ACM certificates

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the update criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "acm_domain_name": str,
                "acm_subject_alternative_names": [ (Optional)
                    str
                ],
                "acm_region": str,
                "dns_zone_domain": str,
                "dns_zone_role_arn": str, (Optional - Must be provided if Route53 Hosted Zone is in a different account)
                "record_ttl": str (Optional - defaults to 60 - Must be a stringified int)
            }
    context : dict
        Provided by AWS
    """
    resource_properties = event["ResourceProperties"]
    acm_domain_name = resource_properties["acm_domain_name"]
    acm_region = resource_properties["acm_region"]

    print(f"- ACM domain name: {acm_domain_name}")
    print(f"- ACM region: {acm_region}")

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": acm_domain_name,
    }

    if "data" not in event.keys():
        print("- Data not in event. Nothing to check, returning IsComplete = False")
        return {"IsComplete": False}

    region_config = Config(
        region_name=acm_region,
        signature_version="v4",
        retries={"max_attempts": 5, "mode": "adaptive"},
    )

    acm_client = boto3.client("acm", config=region_config)
    ssm_client = boto3.client("ssm", config=region_config)

    acm_certificate_ssm_name = f"/ResumeAppACMCertificateArn/{acm_domain_name}"

    try:
        acm_certificate_ssm_response = ssm_client.get_parameter(Name=acm_certificate_ssm_name, WithDecryption=False)
        acm_certificate_arn = acm_certificate_ssm_response["Parameter"]["Value"]
        print(f"- Got certificate ARN from SSM: {acm_certificate_arn}")

        acm_certificate_response = acm_client.describe_certificate(CertificateArn=acm_certificate_arn)

        certificate_status = acm_certificate_response["Certificate"]["Status"]
        print(f"- ACM certificate status: {certificate_status}")
        if certificate_status == "PENDING_VALIDATION":
            print("- ACM certificate validation still pending, returning IsComplete = False")
            return {"IsComplete": False}
        elif certificate_status == "ISSUED":
            print("- ACM certificate issued, returning IsComplete = True")
            return {
                "IsComplete": True,
                "Data": event["data"],
            }

        ret["reason"] = f"ACM Certificate failed to validate. (ARN: {acm_certificate_arn})"
        print(f"- {ret['reason']}")
        send_cfn_signal(**ret)

    except ClientError as e:
        ret["reason"] = e.response["Error"]["Message"]
        send_cfn_signal(**ret)
        print(ret["reason"])
        print(format_exc())
    except Exception as e:
        ret["reason"] = f"Error: {', '.join(e.args)}"
        send_cfn_signal(**ret)
        print(format_exc())
