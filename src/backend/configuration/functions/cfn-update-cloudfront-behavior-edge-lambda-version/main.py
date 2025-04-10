from common.common import SUCCESS, FAILED, get_latest_lambda_function_version_arn, send_cfn_signal
from time import sleep
from traceback import format_exc

import boto3

from botocore.config import Config

cloudfront_config = Config(
    region_name="us-east-1", signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
)
cloudfront_client = boto3.client("cloudfront", config=cloudfront_config)


def handler(event, context):
    """
    Updates a CloudFront distribution behavior's Lambda@Edge function

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the distribution, behavior, and Lambda@Edge information. The dictionary's
        ResourceProperties field should contain the following items:
            "ResourceProperties": {
                "distribution_id": str,
                "updates": [
                    {
                        "is_default_cache_behavior": str, ("true" or "false")
                        "path_pattern": str, (required if is_default_cache_behavior is true)
                        "function_arn": str,
                        "function_version": str, (can be either the exact version number or LATEST)
                        "event_type": str, (origin-response, origin-request, viewer-request, viewer-response)
                        "include_body": str ("true" or "false")
                    }
                ]
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    distribution_id = resource_properties["distribution_id"]

    print(f"- Request type: {request_type}")
    print(f"- Distribution ID: {distribution_id}")

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": distribution_id,
    }
    if request_type != "Delete":
        distribution = cloudfront_client.get_distribution(Id=distribution_id)
        for update in resource_properties["updates"]:
            is_default_cache_behavior = True if update["is_default_cache_behavior"] == "true" else False
            cache_behavior = None
            if is_default_cache_behavior:
                cache_behavior = distribution["Distribution"]["DistributionConfig"]["DefaultCacheBehavior"]
                behavior_type = "default behavior"
            else:
                cache_behavior_number = 0
                cache_behavior_path_pattern = update["path_pattern"]
                behavior_type = f"behavior with path pattern {cache_behavior_path_pattern}"
                for behavior in distribution["Distribution"]["DistributionConfig"]["CacheBehaviors"]["Items"]:
                    if behavior["PathPattern"] == cache_behavior_path_pattern:
                        cache_behavior = behavior
                        break
                    cache_behavior_number += 1
                if not cache_behavior:
                    ret["reason"] = f"Unable to find cache behavior with the path pattern {cache_behavior_path_pattern}"
                    send_cfn_signal(**ret)
                    raise ValueError(f"Cache behavior not found")

            print(f"- Updating {behavior_type}")
            if request_type == "Delete":
                num_starting_lambda_functions = cache_behavior["LambdaFunctionAssociations"]["Quantity"]
                association_items = (
                    cache_behavior["LambdaFunctionAssociations"]["Items"]
                    if "Items" in cache_behavior["LambdaFunctionAssociations"].keys()
                    else []
                )
                for i in range(0, len(association_items)):
                    lambda_function_association = association_items[i]
                    if (
                        lambda_function_association["LambdaFunctionARN"].startswith(update["function_arn"])
                        and lambda_function_association["EventType"] == update["event_type"]
                    ):
                        cache_behavior["LambdaFunctionAssociations"]["Quantity"] -= 1
                        _ = association_items.pop(i)
                        break
                if cache_behavior["LambdaFunctionAssociations"]["Quantity"] == num_starting_lambda_functions:
                    ret["reason"] = (
                        f"Unable to find function association for arn {update['function_arn']} in behavior with path pattern {cache_behavior_path_pattern}"
                    )
                    send_cfn_signal(**ret)
                    raise ValueError(f"Function association not found")

            else:
                latest_functions = {}

                event_type = update["event_type"]
                include_body = True if update["include_body"] == "true" else False

                print(f"-- Updating event type {event_type}")
                if include_body:
                    print(f"-- Including body")

                if update["function_version"] == "LATEST":
                    if update["function_arn"] not in latest_functions.keys():
                        lambda_client = boto3.client("lambda", config=cloudfront_config)
                        latest_functions[update["function_arn"]] = get_latest_lambda_function_version_arn(
                            update["function_arn"], lambda_client
                        )
                    function_version_arn = latest_functions[update["function_arn"]]
                else:
                    function_version_arn = f"{update['function_arn']}:{update['function_version']}"

                print(f"-- Updating behavior with function {function_version_arn}")

                if "Items" not in cache_behavior["LambdaFunctionAssociations"].keys():
                    cache_behavior["LambdaFunctionAssociations"]["Items"] = []

                item_to_update = None
                for i in range(0, len(cache_behavior["LambdaFunctionAssociations"]["Items"])):
                    if cache_behavior["LambdaFunctionAssociations"]["Items"][i]["EventType"] == event_type:
                        item_to_update = cache_behavior["LambdaFunctionAssociations"]["Items"].pop(i)

                if not item_to_update:
                    cache_behavior["LambdaFunctionAssociations"]["Quantity"] += 1
                    item_to_update = {
                        "LambdaFunctionARN": function_version_arn,
                        "EventType": event_type,
                        "IncludeBody": include_body,
                    }
                else:
                    item_to_update["LambdaFunctionARN"] = function_version_arn

                cache_behavior["LambdaFunctionAssociations"]["Items"].append(item_to_update)

        try:
            _ = cloudfront_client.update_distribution(
                DistributionConfig=distribution["Distribution"]["DistributionConfig"],
                Id=distribution_id,
                IfMatch=distribution["ETag"],
            )

            max_attempts = 150
            attempt_wait_time = 5
            attempts = 0
            while attempts < max_attempts:
                distribution_response = cloudfront_client.get_distribution(Id=distribution_id)
                distribution_status = distribution_response["Distribution"]["Status"]
                print(f"- Attempt {attempts + 1} - Distribution status: {distribution_status}")
                if distribution_status == "Deployed":
                    break

                attempts += 1
                sleep(attempt_wait_time)

            if attempts == max_attempts:
                raise RuntimeError("Cloudfront Function never finished deploying")

            print("- Distribution successfully deployed")
        except cloudfront_client.exceptions.InvalidLambdaFunctionAssociation as e:
            ret["reason"] = e.response["Error"]["Message"]
            send_cfn_signal(**ret)
            raise ValueError("Edge function cannot be associated with behavior")

    ret["status"] = SUCCESS
    send_cfn_signal(**ret)
