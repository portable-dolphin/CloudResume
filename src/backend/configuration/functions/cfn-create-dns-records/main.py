from common.common import SUCCESS, FAILED, CustomError, get_boto3_client_with_assumed_role, send_cfn_signal

import boto3

from botocore.config import Config
from botocore.exceptions import ClientError
from time import sleep
from traceback import format_exc


valid_types = [
    "SOA",
    "A",
    "TXT",
    "NS",
    "CNAME",
    "MX",
    "NAPTR",
    "PTR",
    "SRV",
    "SPF",
    "AAAA",
    "CAA",
    "DS",
    "TLSA",
    "SSHFP",
    "SVCB",
    "HTTPS",
]

record_conflicts = {
    "A": ["CNAME", "NS"],
    "AAAA": ["CNAME", "NS"],
    "CNAME": ["A", "AAAA", "MX", "NS", "SRV", "TXT"],
    "MX": ["CNAME", "NS"],
    "NS": ["A", "AAAA", "CNAME", "MX", "SRV", "TXT"],
    "SRV": ["CNAME", "NS"],
    "TXT": ["CNAME", "NS"],
}


def handler(event, context):
    """
    Creates/Deletes DNS records

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the update criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "dns_records": [
                    {
                        "name": str,
                        "type": str, (Valid types: "SOA"|"A"|"TXT"|"NS"|"CNAME"|"MX"|"NAPTR"|"PTR"|"SRV"|"SPF"|"AAAA"|"CAA"|"DS"|"TLSA"|"SSHFP"|"SVCB"|"HTTPS")
                        "resource_records": [
                            {
                                "value": str
                            },
                        ],
                        "alias_target": {
                            "hosted_zone_id": str,
                            "dns_name": str,
                        }
                    }
                ],
                "dns_zone_domain": str,
                "route53_hosted_zone_id": str,
                "dns_zone_role_arn": str, (Optional - Must be provided if Route53 Hosted Zone is in a different account)
                "record_ttl": str, (Optional - defaults to 60 - Must be a stringified int)
                "removal_policy": str (Optional - defaults to "destroy" - Must be either "retain" or "destroy")
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]

    dns_records = resource_properties["dns_records"]
    dns_zone_domain = resource_properties["dns_zone_domain"]
    dns_zone_role_arn = (
        resource_properties["dns_zone_role_arn"] if "dns_zone_role_arn" in resource_properties.keys() else None
    )
    route53_hosted_zone_id = resource_properties["route53_hosted_zone_id"]
    record_ttl = int(resource_properties["record_ttl"]) if "record_ttl" in resource_properties.keys() else 60
    removal_policy = (
        True
        if "removal_policy" in resource_properties.keys() and resource_properties["removal_policy"] == "destroy"
        else False
    )

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": dns_zone_domain.replace(".", "") + "_records",
    }

    print(f"- Request type: {request_type}")
    print(f"- DNS zone domain: {dns_zone_domain}")
    print(f"- Route53 Hosted Zone ID: {route53_hosted_zone_id}")
    print(f"- DNS zone role ARN: {dns_zone_role_arn}")
    print(f"- Record TTL: {record_ttl}")
    print(f"- Delete records on destroy: {removal_policy}")

    if request_type == "Delete" and removal_policy == "retain":
        print(f"- Removal policy is set to retain. Returning success.")
        ret["status"] = SUCCESS
        send_cfn_signal(**ret)
        return

    if len(dns_records) == 0:
        ret["reason"] = "ERROR: No records were provided"
        send_cfn_signal(**ret)
        raise CustomError(ret["reason"])

    record_errors = []
    record_number = 0
    for record in dns_records:
        record_error = []
        if "name" not in record.keys():
            record_error.append(f'Record {record_number}: Has no key "name"')
        elif not isinstance(record["name"], str):
            record_error.append(
                f"Record {record_number}: Value of \"name\" must be a string. Type provided: {type(record['name'])}"
            )
        if "type" not in record.keys():
            record_error.append(f'Record {record_number}: Has no key "type"')
        elif not isinstance(record["type"], str):
            record_error.append(
                f"Record {record_number}: Value of \"type\" must be a string. Type provided: {type(record['type'])}"
            )
        elif record["type"] not in valid_types:
            record_error.append(f'Record {record_number}: Value of "type" must be a valid DNS type.')
        if "resource_records" in record.keys() and "alias_target" in record.keys():
            record_error.append(
                f"Record {record_number}: Must specify either resource_records or alias_target, not both."
            )
        elif "resource_records" not in record.keys() and "alias_target" not in record.keys():
            record_error.append(f"Record {record_number}: Must specify either resource_records or alias_target.")

        if "resource_records" in record.keys():
            if not isinstance(record["resource_records"], list):
                record_error.append(
                    f"Record {record_number}: Value of \"resource_records\" must be a list. Type provided: {type(record['resource_records'])}"
                )

            resource_record_number = 0
            for resource_record in record["resource_records"]:
                if not isinstance(resource_record, dict):
                    record_error.append(
                        f"Record {record_number} - Resource Record {resource_record}: Resource record must be a dictionary. Type provided: {type(resource_record)}"
                    )
                elif "value" not in resource_record.keys():
                    record_error.append(
                        f'Record {record_number} - Resource Record {resource_record}: Has no key "value"'
                    )
                if not isinstance(resource_record["value"], str):
                    record_error.append(
                        f"Record {record_number} - Resource Record {resource_record}: Value of \"value\" must be a string. Type provided: {type(resource_record['value'])}"
                    )
                if not record_error:
                    resource_record["Value"] = resource_record["value"]
                    resource_record.pop("value")
                resource_record_number += 1

        if "alias_target" in record.keys():
            if not isinstance(record["alias_target"], dict):
                record_error.append(
                    f"Record {record_number}: Value of \"alias_target\" must be a dictionary. Type provided: {type(record['alias_target'])}"
                )
            elif "hosted_zone_id" not in record["alias_target"].keys():
                record_error.append(f'Record {record_number}: Value of "alias_target" has no key "hosted_zone_id"')
            if not isinstance(record["alias_target"]["hosted_zone_id"], str):
                record_errors.append(
                    f"Record {record_number}: Value of \"hosted_zone_id\" must be a string. Type provided: {type(record['alias_target']['hosted_zone_id'])}"
                )
            elif "dns_name" not in record["alias_target"].keys():
                record_error.append(f'Record {record_number}: Value of "alias_target" has no key "hosted_zone_id"')
            if not isinstance(record["alias_target"]["dns_name"], str):
                record_error.append(
                    f"Record {record_number}: Value of \"dns_name\" must be a string. Type provided: {type(record['alias_target']['dns_name'])}"
                )
            if not record_error:
                record["alias_target"]["HostedZoneId"] = record["alias_target"]["hosted_zone_id"]
                record["alias_target"]["DNSName"] = record["alias_target"]["dns_name"]
                record["alias_target"].pop("hosted_zone_id")
                record["alias_target"].pop("dns_name")
            else:
                record_errors += record_error
        record_number += 1

    if record_errors:
        ret["reason"] = "\n".join(record_errors)
        send_cfn_signal(**ret)
        raise CustomError(print(ret["reason"]))

    route53_config = Config(
        region_name="us-east-1", signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
    )

    try:
        if dns_zone_role_arn:
            print("- Setting DNS client to use different account")
            route53_client = get_boto3_client_with_assumed_role("route53", dns_zone_role_arn, route53_config)
        else:
            print("- Setting DNS client to use current account")
            route53_client = boto3.client("route53", config=route53_config)

        print(f"- Parsing the {len(dns_records)} provided records")

        recordset_errors = []
        recordset_changes = []
        for record in dns_records:
            record_name = record["name"]
            record_type = record["type"]

            print(f"- Record name: {record_name} - Record type: {record_type}")

            resource_records = record["resource_records"] if "resource_records" in record.keys() else []
            alias_target = record["alias_target"] if "alias_target" in record.keys() else {}

            recordset_response = route53_client.list_resource_record_sets(
                HostedZoneId=route53_hosted_zone_id,
                StartRecordName=record_name,
                MaxItems="1",
            )

            perform_change = False
            alias_targets_match = False
            resource_records_match = False

            if len(recordset_response["ResourceRecordSets"]) != 0:
                existing_record_name = recordset_response["ResourceRecordSets"][0]["Name"].rstrip(".")
                existing_record_type = recordset_response["ResourceRecordSets"][0]["Type"].rstrip(".")
            else:
                existing_record_name = existing_record_type = ""

            names_match = existing_record_name == record_name.rstrip(".")
            types_match = existing_record_type == record_type.rstrip(".")

            # if both the provided record and the returned records match in type and value...
            if names_match and types_match:
                print(
                    f"-- Found matching record - existing record name: {existing_record_name} - existing record type: {existing_record_type}"
                )
                # and they both ARE NOT alias records, evaluate if the record values match
                if resource_records and "ResourceRecords" in recordset_response["ResourceRecordSets"][0].keys():
                    print("-- New and existing records ARE NOT aliases")
                    response_resource_record = recordset_response["ResourceRecordSets"][0]["ResourceRecords"]
                    resource_records_intersection = set(resource_records.values()).intersection(
                        response_resource_record.values()
                    )
                    resource_records_match = len(resource_records_intersection) == len(
                        response_resource_record.values()
                    )
                    if resource_records_match:
                        print("-- New and existing record values match")
                    else:
                        print("-- New and existing record values do not match")
                # and they both ARE alias records, evaluate if the record values match
                elif alias_target and "AliasTarget" in recordset_response["ResourceRecordSets"][0].keys():
                    print("New and existing records ARE aliases")
                    record_hosted_zone_id = record["alias_target"]["HostedZoneId"]
                    response_hosted_zone_id = recordset_response["ResourceRecordSets"][0]["AliasTarget"]["HostedZoneId"]
                    record_dns_name = record["alias_target"]["DNSName"].rstrip(".")
                    response_dns_name = recordset_response["ResourceRecordSets"][0]["AliasTarget"]["DNSName"].rstrip(
                        "."
                    )
                    alias_targets_match = (
                        record_hosted_zone_id == response_hosted_zone_id and record_dns_name == response_dns_name
                    )
                    if alias_targets_match:
                        print("-- New and existing record values match")
                    else:
                        print("-- New and existing record values do not match")
            elif names_match and not types_match:
                if record_type in record_conflicts[existing_record_type]:
                    message = f'ERROR: A record with the name "{record_name}" already exists, but it is of type {existing_record_type}.'
                    print(f"-- {message}")
                    recordset_errors.append(message)
                    continue

            print(f"-- Names match: {names_match}")
            print(f"-- Types match: {types_match}")
            print(f"-- Resource records match: {resource_records_match}")
            print(f"-- Alias targets match: {alias_targets_match}")
            if (
                request_type == "Delete"
                and names_match
                and types_match
                and (resource_records_match or alias_targets_match)
            ):
                print("-- Adding record to batch for deletion")
                perform_change = True
            elif request_type != "Delete":
                if (
                    len(recordset_response["ResourceRecordSets"]) == 0
                    or not names_match
                    or (names_match and types_match and (not resource_records_match or not alias_targets_match))
                    or (names_match and not types_match and record_type not in record_conflicts[existing_record_type])
                ):
                    print("-- Adding record to batch for creation/updating")
                    perform_change = True

            if perform_change:
                action = "DELETE" if request_type == "Delete" else "UPSERT"
                recordset_change = {
                    "Action": action,
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": record_type,
                    },
                }
                if "resource_records" in record.keys():
                    recordset_change["ResourceRecordSet"]["TTL"] = record_ttl
                    recordset_change["ResourceRecordSet"]["ResourceRecords"] = record["resource_records"]
                else:
                    recordset_change["ResourceRecordSet"]["AliasTarget"] = record["alias_target"]
                    recordset_change["ResourceRecordSet"]["AliasTarget"]["EvaluateTargetHealth"] = False
                recordset_changes.append(recordset_change)

        if recordset_errors:
            raise CustomError("\n".join(recordset_errors))

        if recordset_changes:
            print("- Performing Route53 batch change")
            dns_response = route53_client.change_resource_record_sets(
                HostedZoneId=route53_hosted_zone_id, ChangeBatch={"Changes": recordset_changes}
            )

            change_id = dns_response["ChangeInfo"]["Id"]

            max_attempts = 100
            attempt_wait_time = 3
            attempts = 0
            while attempts < max_attempts:
                change_response = route53_client.get_change(Id=change_id)
                change_status = change_response["ChangeInfo"]["Status"]
                print(f"- Attempt {attempts + 1} - Change status: {change_status}")
                if change_status == "INSYNC":
                    break

                attempts += 1
                sleep(attempt_wait_time)

            if attempts == max_attempts:
                raise CustomError("Certificate never gave domain information for validation")

            print("- Change succeeded")

        ret["status"] = SUCCESS
        send_cfn_signal(**ret)

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
