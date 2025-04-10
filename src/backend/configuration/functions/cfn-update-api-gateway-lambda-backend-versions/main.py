from common.common import SUCCESS, FAILED, get_latest_lambda_function_version, send_cfn_signal

import boto3
import importlib

from botocore.config import Config
from botocore.exceptions import ClientError
from enum import Enum
from json import dumps
from traceback import format_exc


class Method(Enum):
    DELETE = "DELETE"
    GET = "GET"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    PATCH = "PATCH"
    POST = "POST"
    PUT = "PUT"


api_client = None
lambda_clients = {}


def handler(event, context):
    """
    Updates an API gateway's lambda proxy integration backend URI

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the update criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "rest_api_id": str,
                "rest_api_region": str,
                "deployment_stages": [
                    str (The name of the deployment stage)
                ],
                "updates": [
                    {
                        "resource_id": str,
                        "method": str, (One of: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT)
                        "lambda_arn": str,
                        "lambda_version": str, (optional) (can be either the exact version number or LATEST)
                        "lambda_alias": str (optional) (lambda_version and lambda_alias are mutually exclusive)
                    },
                    {
                        ...
                    }
                ]
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    api_id = resource_properties["rest_api_id"]
    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": api_id,
    }

    print(f"- Request type: {request_type}")
    print(f"- API ID: {api_id}")
    print(event)

    if request_type == "Delete":
        ret["status"] = SUCCESS
        send_cfn_signal(**ret)
    else:
        api_region = resource_properties["rest_api_region"]
        deployment_stages = resource_properties["deployment_stages"]
        updates = resource_properties["updates"]
        invalid_updates = []
        for update in updates:
            invalid_update = {}
            resource_id = update["resource_id"] if "resource_id" in update.keys() else None
            method = update["method"] if "method" in update.keys() else None
            lambda_arn = update["lambda_arn"] if "lambda_arn" in update.keys() else None
            lambda_version = update["lambda_version"] if "lambda_version" in update.keys() else None
            lambda_alias = update["lambda_alias"] if "lambda_alias" in update.keys() else None

            if not resource_id:
                invalid_update["resource_id"] = "REQUIRED ITEM MISSING"
            elif not isinstance(resource_id, str):
                invalid_update["resource_id"] = (
                    f"{resource_id} - GOT TYPE {type(resource_id).__name__} - MUST BE TYPE str"
                )

            if not method:
                invalid_update["method"] = "REQUIRED ITEM MISSING"
            elif not isinstance(method, str):
                invalid_update["method"] = f"{method} - GOT TYPE {type(method).__name__} - MUST BE TYPE str"
            else:
                try:
                    _ = getattr(Method, method)
                except AttributeError:
                    invalid_update["method"] = f"MUST BE ONE OF DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT - {method}"

            if not lambda_arn:
                invalid_update["lambda_arn"] = "REQUIRED ITEM MISSING"
            elif not isinstance(lambda_arn, str):
                invalid_update["lambda_arn"] = f"{lambda_arn} - GOT TYPE {type(lambda_arn).__name__} - MUST BE TYPE str"

            if lambda_version and lambda_alias:
                invalid_update["lambda_version"] = f"MUTUALLY EXCLUSIVE vv - {lambda_version}"
                invalid_update["lambda_alias"] = f"MUTUALLY EXCLUSIVE   ^^ - {lambda_alias}"

            if lambda_version and not isinstance(lambda_version, str):
                invalid_update["lambda_version"] = (
                    f"{lambda_version} - GOT TYPE {type(lambda_version).__name__} - MUST BE TYPE str"
                )

            if lambda_alias and not isinstance(lambda_alias, str):
                invalid_update["lambda_alias"] = (
                    f"{lambda_alias} - GOT TYPE {type(lambda_alias).__name__} - MUST BE TYPE str"
                )

            if invalid_update:
                invalid_updates.append(update | invalid_update)

        if invalid_updates:
            message = f"The following items to update are invalid:\n{dumps(invalid_updates, indent=4)}"
            ret["reason"] = message
            send_cfn_signal(**ret)
            raise ValueError(message)

        api_config = Config(
            region_name=api_region, signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"}
        )
        api_client = boto3.client("apigateway", config=api_config)

        try:
            api_changed = False
            for update in updates:
                resource_id = update["resource_id"]
                method = update["method"]
                lambda_arn = update["lambda_arn"]
                lambda_version = update["lambda_version"] if "lambda_version" in update.keys() else None
                lambda_alias = update["lambda_alias"] if "lambda_alias" in update.keys() else None

                qualifier = None
                if lambda_version:
                    if lambda_version == "LATEST":
                        lambda_region = lambda_arn.split(":")[3]
                        if lambda_region not in lambda_clients.keys():
                            lambda_config = Config(
                                region_name=lambda_region,
                                signature_version="v4",
                                retries={"max_attempts": 5, "mode": "adaptive"},
                            )
                            lambda_clients[lambda_region] = boto3.client("lambda", config=lambda_config)

                        qualifier = get_latest_lambda_function_version(lambda_arn, lambda_clients[lambda_region])[
                            "Version"
                        ]
                    else:
                        qualifier = lambda_version
                elif lambda_alias:
                    qualifier = lambda_alias

                uri_value = f"arn:aws:apigateway:{api_region}:lambda:path/2015-03-31/functions/{lambda_arn}"
                if qualifier:
                    uri_value = f"{uri_value}:{qualifier}"
                uri_value += "/invocations"

                current_integration = api_client.get_integration(
                    restApiId=api_id,
                    resourceId=resource_id,
                    httpMethod=method,
                )

                if current_integration["uri"] == uri_value:
                    continue

                patch_operations = [{"op": "replace", "path": "/uri", "value": uri_value}]

                print(f"- Updaing API resource {resource_id} method {method} integration to {uri_value}")
                _ = api_client.update_integration(
                    restApiId=api_id,
                    resourceId=resource_id,
                    httpMethod=method,
                    patchOperations=patch_operations,
                )
                api_changed = True

            if api_changed:
                for stage in deployment_stages:
                    print(f"- Creating deploy with stage name {stage}")
                    _ = api_client.create_deployment(
                        restApiId=api_id,
                        stageName=stage,
                    )
            else:
                print("- No changes made to API. Not deploying.")
            ret["status"] = SUCCESS

        except ClientError as e:
            ret["reason"] = e.response["Error"]["Message"]
            print(f"Error creating deployment. Error: {ret['reason']}")
        except Exception as e:
            err_type = type(e)
            ret["reason"] = f"Error type: {err_type.__qualname__}\nError message: {', '.join(e.args)}"
            print(format_exc())

        send_cfn_signal(**ret)
