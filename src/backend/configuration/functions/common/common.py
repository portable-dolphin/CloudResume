from botocore.config import Config
from enum import Enum
from json import dumps
from pathlib import Path
from time import sleep
from typing import Any, Dict

import boto3
import sys

sys.path.insert(0, f"{Path(__file__).parent}/packages")

import requests

FAILED = "FAILED"
SUCCESS = "SUCCESS"


class CustomError(Exception):
    def __init__(self, message):
        super().__init__(message)


def get_latest_lambda_function_version_arn(function_arn: str, lambda_client) -> str:
    """
    Retrieves the function's latest version number

    Parameters
    ----------
    function_arn : str
        The ARN of the Lambda function
    lambda_client : boto3.client
        The boto3 client to use

    Returns
    ----------
    str
       Latest version number
    """
    latest_version = get_latest_lambda_function_version(function_arn, lambda_client)
    if latest_version:
        return latest_version["FunctionArn"]
    return latest_version


def get_latest_lambda_function_version(function_arn: str, lambda_client: boto3.client) -> dict:
    """
    Retrieves the function's latest version configuration

    Parameters
    ----------
    function_arn : str
        The ARN of the Lambda function
    lambda_client : boto3.client
        The boto3 client object to use

    Returns
    ----------
    str
       The latest version dictionary from the boto3 lambda client function "list_versions_by_function"
    """
    versions = []

    response = lambda_client.list_versions_by_function(FunctionName=function_arn)
    versions += response["Versions"]
    while "NextMarker" in response.keys():
        response = lambda_client.list_versions_by_function(FunctionName=function_arn, Marker=response["NextMarker"])
        versions += response["Versions"]

    latest_version = versions[0]
    last_version = 0

    for version in versions[-1::-1]:
        if version["Version"] != "$LATEST" and int(version["Version"]) > last_version:
            last_version = int(version["Version"])
            latest_version = version

    return latest_version


def get_boto3_client_with_assumed_role(service: str, role_arn: str, client_config: Config) -> boto3.client:
    """
    Creates a boto3 client with non-default credentials

    Parameters
    ----------
    service : str
        The boto3 name of the AWS service
    role_arn : str
        The role to assume (Must have trust relationship in destination account)
    client_config : botocore.config.Config
        The boto3 client configuration object to use

    Returns
    ----------
    boto3.client
       An instantiated client using the credentials of the provided role
    """
    sts_client = boto3.client("sts", config=client_config)
    assumed_role = sts_client.assume_role(RoleArn=role_arn, RoleSessionName=f"{service}_session")
    access_key_id = assumed_role["Credentials"]["AccessKeyId"]
    secret_access_key = assumed_role["Credentials"]["SecretAccessKey"]
    aws_session_token = assumed_role["Credentials"]["SessionToken"]
    return boto3.client(
        service,
        config=client_config,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=aws_session_token,
    )


def send_cfn_signal(
    *,
    response_url: str,
    request_id: str,
    stack_id: str,
    status: str,
    reason: str = None,
    logical_resource_id: str,
    physical_resource_id: str,
    no_echo: bool = False,
    data: dict = {},
) -> None:
    """
    Sends a response to CloudFormation for the provided custom resource

    Keyword Parameters
    ----------
    response_url : str
        The URL provided in the custom resource's event parameter
    request_id : str
        The request ID provided in the custom resource's event parameter
    stack_id : str
        The stack ID provided in the custom resource's event parameter
    status : str
        The return status. Must be either "SUCCESS" or "FAILED"
    reason : str
        The reason for the status
    logical_resource_id : str
        The logical resource ID provided in the custom resource's event parameter
    physical_resource_id : str
        The physical resource ID to send back to CloudFormation. Must be the same every time or the
        previous instance of the custom resource will get sent a Delete event in attempt to replace it
    no_echo: bool
        Whether the response fields should be masked in the CloudFormation API output
    data : Dict
        Any data to send with the response
    """
    headers = {"Content-Type": "application/json"}
    body_json = {
        "RequestId": request_id,
        "StackId": stack_id,
        "Status": status,
        "LogicalResourceId": logical_resource_id,
        "PhysicalResourceId": physical_resource_id,
        "NoEcho": no_echo,
    }
    if reason:
        body_json["Reason"] = reason
    if data:
        body_json["Data"] = data

    print(f"Sending response to CloudFormation.")
    print(f"URL: {response_url}")
    print(f"Body JSON:\n{dumps(body_json, indent=4)}")

    response = requests.put(url=response_url, headers=headers, json=body_json)
    if response.status_code > 299:
        raise RuntimeError(
            f"Unable to send CFN response. StatusCode: {response.status_code}, Reason: {response.reason}, Text: {response.text}"
        )
    # Wait to allow the response to go through
    sleep(10)
