from common.common import SUCCESS, FAILED, CustomError, get_boto3_client_with_assumed_role, send_cfn_signal

import boto3

from botocore.config import Config
from botocore.exceptions import ClientError
from json import dumps
from random import randrange
from time import sleep
from traceback import format_exc


def handler(event, context):
    """
    Creates/Deletes ACM certificates and adds DNS zone entries for certificate validation

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
                "retain_certificate_on_in_use_failure": str, (Optional - defaults to "false" - Must be either "true" or "false")
                "dns_zone_domain": str,
                "route53_hosted_zone_id": str,
                "dns_zone_role_arn": str, (Optional - Must be provided if Route53 Hosted Zone is in a different account)
                "record_ttl": str, (Optional - defaults to 60 - Must be a stringified int)
                "delete_dns_records_with_certificate": str (Optional - defaults to "false" - Must be either "true" or "false")
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]

    print()

    acm_domain_name = resource_properties["acm_domain_name"]
    acm_subject_alternative_names = (
        resource_properties["acm_subject_alternative_names"]
        if "acm_subject_alternative_names" in resource_properties.keys()
        else []
    )
    acm_region = resource_properties["acm_region"]
    retain_certificate_on_in_use_failure = resource_properties["retain_certificate_on_in_use_failure"]
    dns_zone_domain = resource_properties["dns_zone_domain"]
    route53_hosted_zone_id = resource_properties["route53_hosted_zone_id"]
    dns_zone_role_arn = (
        resource_properties["dns_zone_role_arn"] if "dns_zone_role_arn" in resource_properties.keys() else None
    )
    record_ttl = int(resource_properties["record_ttl"]) if "record_ttl" in resource_properties.keys() else 60
    delete_dns_records_with_certificate = (
        True
        if "delete_dns_records_with_certificate" in resource_properties.keys()
        and resource_properties["delete_dns_records_with_certificate"] == "true"
        else False
    )

    print(f"- Request type: {request_type}")
    print(f"- ACM domain name: {acm_domain_name}")
    print(f"- ACM region: {acm_region}")
    print(f"- Subject alternative names: {', '.join(acm_subject_alternative_names)}")
    print(f"- DNS zone domain name: {dns_zone_domain}")
    print(f"- DNS zone role ARN: {dns_zone_role_arn}")
    print(f"- Route53 Hosted Zone ID: {route53_hosted_zone_id}")
    print(f"- Record TTL: {record_ttl}")
    print(f"- Delete DNS records with certificate: {delete_dns_records_with_certificate}")

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": acm_domain_name,
    }

    region_config = Config(
        region_name=acm_region, signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
    )
    global_config = Config(
        region_name="us-east-1", signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
    )
    acm_client = boto3.client("acm", config=region_config)
    ssm_client = boto3.client("ssm", config=region_config)
    if dns_zone_role_arn:
        route53_client = get_boto3_client_with_assumed_role("route53", dns_zone_role_arn, global_config)
    else:
        route53_client = boto3.client("route53", config=global_config)

    idempotency_token_ssm_name = f"/ResumeAppACMIdempotencyToken/{acm_domain_name}"
    acm_certificate_ssm_name = f"/ResumeAppACMCertificateArn/{acm_domain_name}"

    print(f"- Idempotency token SSM parameter name: {idempotency_token_ssm_name}")
    print(f"- ACM certificate SSM parameter name: {acm_certificate_ssm_name}")

    try:
        try:
            idempotency_token_response = ssm_client.get_parameter(Name=idempotency_token_ssm_name, WithDecryption=False)
            idempotency_token = idempotency_token_response["Parameter"]["Value"]
        except ssm_client.exceptions.ParameterNotFound:
            if request_type == "Create":
                chars = "abcdefghijklmnopqrstuvwxyz0123456789"
                idempotency_token = ""
                for _ in range(0, 12):
                    idempotency_token += chars[randrange(0, len(chars) - 1, 1)]
                try:
                    print(f"- Creating SSM parameter: {idempotency_token_ssm_name}")
                    _ = ssm_client.put_parameter(
                        Name=idempotency_token_ssm_name, Value=idempotency_token, Type="String"
                    )
                except ClientError as e:
                    ret["reason"] = e.response["Error"]["Message"]
                    raise CustomError(format_exc())
            elif request_type == "Delete":
                ret["status"] = SUCCESS
                send_cfn_signal(**ret)
                return ret
            else:
                ret["reason"] = (
                    f'Error fetching idempotency token from SSM - Parameter with name "{idempotency_token_ssm_name}" could not be found'
                )
                raise CustomError(format_exc())

        print(f"- Idempotency token: {idempotency_token}")

        if request_type != "Create":
            try:
                acm_certificate_response = ssm_client.get_parameter(Name=acm_certificate_ssm_name, WithDecryption=False)
                acm_certificate_arn = acm_certificate_response["Parameter"]["Value"]
                print(f"- Got certificate ARN from SSM: {acm_certificate_arn}")
            except ssm_client.exceptions.ParameterNotFound:
                if request_type == "Update":
                    ret["reason"] = (
                        f'Error fetching ACM Certificate ARN from SSM - Parameter with name "{acm_certificate_ssm_name}" could not be found'
                    )
                    raise CustomError(ret["reason"])
                else:
                    ret["status"] = SUCCESS
                    send_cfn_signal(**ret)
                    return ret

        if request_type == "Delete":
            acm_certificate_response = acm_client.describe_certificate(CertificateArn=acm_certificate_arn)

            acm_certificate_in_use = True
            max_attempts = 30
            attempt_wait_time = 4
            attempts = 0
            while attempts < max_attempts:
                in_use_by = acm_certificate_response["Certificate"]["InUseBy"]
                print(f"- Attempt {attempts + 1} - ACM Certificate in use by: {', '.join(in_use_by)}")
                if in_use_by:
                    sleep(attempt_wait_time)
                    acm_certificate_response = acm_client.describe_certificate(CertificateArn=acm_certificate_arn)
                else:
                    acm_certificate_in_use = False
                    break
                attempts += 1
            if acm_certificate_in_use:
                acm_certificate_in_use_by = " ,".join(acm_certificate_response["Certificate"]["InUseBy"])
                if not retain_certificate_on_in_use_failure:
                    raise CustomError(f"ERROR: Certificate is still in use by {acm_certificate_in_use_by}")
                else:
                    print(f"- ACM certificate still in use by {acm_certificate_in_use_by}")
                    print("- Continuing anyway due to retain_certificate_on_in_use_failure == True")

            acm_certificate = acm_certificate_response["Certificate"]

            if delete_dns_records_with_certificate:
                recordset_changes = []
                for domain in acm_certificate["DomainValidationOptions"]:
                    record_name = domain["ResourceRecord"]["Name"]
                    record_type = domain["ResourceRecord"]["Type"]
                    record_value = domain["ResourceRecord"]["Value"]

                    recordset_response = route53_client.list_resource_record_sets(
                        HostedZoneId=route53_hosted_zone_id,
                        StartRecordName=record_name,
                        StartRecordType=record_type,
                        MaxItems="1",
                    )
                    if (
                        len(recordset_response["ResourceRecordSets"]) != 0
                        and "ResourceRecords" in recordset_response["ResourceRecordSets"][0].keys()
                        and recordset_response["ResourceRecordSets"][0]["Name"] == record_name
                        and recordset_response["ResourceRecordSets"][0]["ResourceRecords"][0]["Value"] == record_value
                        and recordset_response["ResourceRecordSets"][0]["Type"] == record_type
                    ):
                        recordset_changes.append(
                            {
                                "Action": "DELETE",
                                "ResourceRecordSet": {
                                    "Name": record_name,
                                    "Type": record_type,
                                    "TTL": record_ttl,
                                    "ResourceRecords": [
                                        {
                                            "Value": record_value,
                                        },
                                    ],
                                },
                            }
                        )

                print("- DNS recordset changes:")
                print(dumps(recordset_changes, indent=". "))
                if recordset_changes:
                    _ = route53_client.change_resource_record_sets(
                        HostedZoneId=route53_hosted_zone_id, ChangeBatch={"Changes": recordset_changes}
                    )

            parameters = [idempotency_token_ssm_name, acm_certificate_ssm_name]
            print(f"- Deleting SSM parameters: {', '.join(parameters)}")
            _ = ssm_client.delete_parameters(Names=[idempotency_token_ssm_name, acm_certificate_ssm_name])

            if not acm_certificate_in_use:
                print(f"- Deleting ACM certificate: {acm_certificate_arn}")
                acm_client.delete_certificate(CertificateArn=acm_certificate_arn)

            ret["status"] = SUCCESS
            send_cfn_signal(**ret)
            return None

        elif request_type == "Create":
            print(f"- Requesting new ACM certificate")
            if acm_subject_alternative_names:
                acm_certificate_response = acm_client.request_certificate(
                    DomainName=acm_domain_name,
                    ValidationMethod="DNS",
                    SubjectAlternativeNames=acm_subject_alternative_names,
                    IdempotencyToken=idempotency_token,
                )
            else:
                acm_certificate_response = acm_client.request_certificate(
                    DomainName=acm_domain_name,
                    ValidationMethod="DNS",
                    IdempotencyToken=idempotency_token,
                )

            acm_certificate_arn = acm_certificate_response["CertificateArn"]
            print(f"- New certificate ARN: {acm_certificate_arn}")
            try:
                _ = ssm_client.put_parameter(Name=acm_certificate_ssm_name, Value=acm_certificate_arn, Type="String")
                print(f"- Creating SSM parameter: {acm_certificate_ssm_name}")
            except:
                pass

            max_attempts = 30
            attempt_wait_time = 5
            attempts = 0
            while attempts < max_attempts:
                acm_certificate_response = acm_client.describe_certificate(CertificateArn=acm_certificate_arn)
                if "DomainValidationOptions" in acm_certificate_response["Certificate"].keys():
                    domain_validations = acm_certificate_response["Certificate"]["DomainValidationOptions"]
                    ready_domain_validations = [
                        domain
                        for domain in domain_validations
                        if domain["ValidationStatus"] == "SUCCESS" or "ResourceRecord" in domain.keys()
                    ]
                    print(
                        f"- Attempt {attempts + 1} - Ready domains: {len(ready_domain_validations)}/{len(domain_validations)}"
                    )
                    if len(domain_validations) == len(ready_domain_validations):
                        break
                attempts += 1
                sleep(attempt_wait_time)

            if attempts == max_attempts:
                raise CustomError("Certificate never gave domain information for validation")

            acm_certificate = acm_certificate_response["Certificate"]

            if acm_certificate["Status"] == "FAILED":
                failure_reason = acm_certificate["FailureReason"]
                ret["reason"] = f"Unable to find Route53 hosted zone {dns_zone_domain}. Reason: {failure_reason}"
                raise CustomError(ret["reason"])
            else:
                recordset_changes = []
                for domain in acm_certificate["DomainValidationOptions"]:
                    print(
                        f"- Validation status for domain {domain['ResourceRecord']['Name']}: {domain['ValidationStatus']}"
                    )
                    if domain["ValidationStatus"] == "PENDING_VALIDATION":
                        record_name = domain["ResourceRecord"]["Name"]
                        record_type = domain["ResourceRecord"]["Type"]
                        record_value = domain["ResourceRecord"]["Value"]
                        recordset_response = route53_client.list_resource_record_sets(
                            HostedZoneId=route53_hosted_zone_id,
                            StartRecordName=record_name,
                            StartRecordType=record_type,
                            MaxItems="1",
                        )

                        if (
                            len(recordset_response["ResourceRecordSets"]) == 0
                            or "ResourceRecords" not in recordset_response["ResourceRecordSets"][0].keys()
                            or recordset_response["ResourceRecordSets"][0]["Name"] != record_name
                            or recordset_response["ResourceRecordSets"][0]["Type"] != record_type
                        ):
                            recordset_changes.append(
                                {
                                    "Action": "UPSERT",
                                    "ResourceRecordSet": {
                                        "Name": record_name,
                                        "Type": record_type,
                                        "TTL": record_ttl,
                                        "ResourceRecords": [
                                            {
                                                "Value": record_value,
                                            },
                                        ],
                                    },
                                }
                            )

                print("- DNS recordset changes:")
                print(dumps(recordset_changes, indent=". "))
                if recordset_changes:
                    _ = route53_client.change_resource_record_sets(
                        HostedZoneId=route53_hosted_zone_id, ChangeBatch={"Changes": recordset_changes}
                    )

        else:
            acm_certificate = acm_client.describe_certificate(CertificateArn=acm_certificate_arn)
            new_subject_alternative_names = set([acm_domain_name, *acm_subject_alternative_names])
            certificate_subject_alternative_names = new_subject_alternative_names.intersection(
                acm_certificate["Certificate"]["SubjectAlternativeNames"]
            )

            if len(new_subject_alternative_names) != len(certificate_subject_alternative_names):
                ret["reason"] = (
                    "Changing the SSL certificate domain name or subject alternative names has not yet been implemented"
                )
                raise CustomError(ret["reason"])

        ret["status"] = SUCCESS
        ret["data"] = {"certificate_arn": acm_certificate_arn}
        return ret

    except ClientError as e:
        ret["reason"] = e.response["Error"]["Message"]
        send_cfn_signal(**ret)
        print(ret["reason"])
        print(format_exc())
    except CustomError as e:
        send_cfn_signal(**ret)
        print(", ".join(e.args))
    except Exception as e:
        ret["reason"] = f"Error: {', '.join(e.args)}"
        send_cfn_signal(**ret)
        print(format_exc())
