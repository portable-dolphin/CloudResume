from common.common import (
    SUCCESS,
    FAILED,
    get_latest_lambda_function_version,
    get_latest_lambda_function_version_arn,
    send_cfn_signal,
)

import boto3
import re
import requests
import zipfile

from botocore.config import Config
from botocore.exceptions import ClientError
from base64 import b64encode
from hashlib import sha256
from json import loads, dumps
from io import BytesIO
from time import sleep
from traceback import format_exc


lambda_client = None
function_check_wait_time = 1
max_check_attempts = 10


def handler(event, context):
    """
    Replaces a published Lambda function's placeholders within given files with the provided replacements

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the replacement criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "function_arn": "function's arn",
                "function_region": "region",
                "function_code_files": [
                    "list",
                    "of",
                    "files/containing/placeholders"
                ],
                "replacement_values": {
                    "[STRING_PLACEHOLDER]": "replacement_value"
                },
                "version_description": "string", (optional),
                "create_new_version": str ("true" or "false"),
                "create_alias": str ("true" or "false"),
                "alias_name": "name of new alias (required if create_alias is True)"
            }
    context : dict
        Provided by AWS
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    function_arn = resource_properties["function_arn"]

    print(f"- Request type: {request_type}")
    print(f"- Function ARN: {function_arn}")

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": function_arn,
    }

    if request_type == "Delete":
        ret["status"] = SUCCESS
    else:
        load_lambda_client(region=resource_properties["function_region"])
        try:
            print(dumps(resource_properties, indent=4))
            ret = ret | update_lambda_function(
                request_type=request_type,
                function_arn=function_arn,
                code_files_to_replace=resource_properties["function_code_files"],
                replacements=resource_properties["replacement_values"],
                create_new_version=(True if resource_properties["create_new_version"] == "true" else False),
                version_description=(
                    resource_properties["version_description"]
                    if "version_description" in resource_properties.keys()
                    else ""
                ),
                create_alias=(True if resource_properties["create_alias"] == "true" else False),
                alias_name=(
                    resource_properties["alias_name"] if "create_alias" in resource_properties.keys() else None
                ),
            )
        except ClientError as e:
            ret["reason"] = e.response["Error"]["Message"]
        except:
            ret["reason"] = format_exc()

    try:
        send_cfn_signal(**ret)
    except ClientError as e:
        print(f"Error sending signal to CFN. Error: {e.response['Error']['Message']}")
    except:
        print(format_exc())


def load_lambda_client(*, region: str) -> None:
    """
    Loads the Lambda Boto3 client

    Parameters
    ----------
    region : str
        The region the client should connect to
    """
    lambda_config = Config(region_name=region, signature_version="v4", retries={"max_attempts": 5, "mode": "adaptive"})
    globals()["lambda_client"] = boto3.client("lambda", config=lambda_config)


def get_lambda_function_zip(*, function_arn: str) -> BytesIO:
    """
    Downloads and returns the Lambda functions code zip file

    Parameters
    ----------
    function_arn : str
        The ARN of the Lambda function

    Returns
    ----------
    BytesIO
        An object containing a zip of the Lambda function's code
    """
    lambda_function = lambda_client.get_function(FunctionName=function_arn)

    function_zip = requests.get(lambda_function["Code"]["Location"]).content
    return BytesIO(function_zip)


def update_lambda_function(
    *,
    request_type: str,
    function_arn: str,
    code_files_to_replace: list,
    replacements: dict,
    create_new_version: bool = True,
    version_description: str = "",
    create_alias: bool = False,
    alias_name: str = None,
) -> dict:
    """
    Replaces all config placeholder strings with their provided values

    Parameters
    ----------
    request_type : str
        The kind of request for the custom resource
    function_arn : str
        The ARN of the Lambda function
    code_files_to_replace : list
        List of files in the Lambda function's code which contain placeholders
    replacements : dict
        A dictionary containing the placeholder string as the key and the replacement string as the value
    create_new_version : bool
        Whether a new version of the Lambda function should be created
    version_description : str
        Description for the version
    create_alias: bool
        Whether an alias should be created (alias_name must be provided if true, else an alias will not be created)
    alias_name : str
        The name of an alias to point the new version at

    Returns
    ----------
    dict
        A dictionary containing the status of the request and the reason for the status
    """
    alias_pattern = re.compile("(?!^[0-9]+$)([a-zA-Z0-9-_]+)")
    function_changed = False
    function_config = None

    if not code_files_to_replace:
        return {"status": SUCCESS}

    if len(replacements) == 0:
        return {"status": SUCCESS}

    if alias_name and not alias_pattern.match(alias_name):
        return {"reason": f"Alias name {alias_name} is invalid"}

    function_zip = zipfile.ZipFile(get_lambda_function_zip(function_arn=function_arn))
    missing_code_files = [name for name in code_files_to_replace if name not in function_zip.namelist()]

    if missing_code_files:
        return {
            "reason": f"ERROR: Missing the following files in function code: {', '.join(missing_code_files)}",
        }

    ret_bytes = BytesIO()
    ret_zip = zipfile.ZipFile(ret_bytes, "w")
    pattern = re.compile("\[[A-Z0-9_]*?_PLACEHOLDER\]")
    for filename in function_zip.namelist():
        contents = function_zip.read(filename)
        if filename in code_files_to_replace:
            print(f"- File: {filename}")
            contents = contents.decode("utf-8")
            placeholders_to_replace = [placeholder for placeholder in pattern.findall(contents)]
            print(f"Placeholders in file: {', '.join(placeholders_to_replace)}")
            missing_placeholders = [
                placeholder for placeholder in placeholders_to_replace if placeholder not in replacements.keys()
            ]
            if missing_placeholders:
                return {
                    "reason": f'ERROR: The following placeholders inside the Lambda function code file "{filename}"'
                    + f"could not be found in the replacements provided: {', '.join(missing_placeholders)}",
                }
            for placeholder in placeholders_to_replace:
                print(f"- Found placeholder {placeholder} in file {filename}")
                contents = contents.replace(placeholder, replacements[placeholder])
                function_changed = True

            contents = contents.encode("utf-8")

        ret_zip.writestr(filename, contents)

    if function_changed:
        ret_zip.close()

        sha256_hash_parts = sha256()
        read_chunk_size = 65536
        ret_bytes.seek(0)
        while True:
            chunk = ret_bytes.read(read_chunk_size)
            if len(chunk):
                sha256_hash_parts.update(chunk)
            else:
                break

        code_sha256 = b64encode(sha256_hash_parts.digest()).decode("utf-8")

        print("- Updating function")
        function_config = lambda_client.update_function_code(FunctionName=function_arn, ZipFile=ret_bytes.getvalue())
        attempts = 0
        bad_state = False
        LastUpdateStatus = ""
        while attempts < max_check_attempts:
            attempts += 1
            wait_time = function_check_wait_time * attempts
            sleep(wait_time)
            function_config = lambda_client.get_function_configuration(FunctionName=function_arn)
            LastUpdateStatus = function_config["LastUpdateStatus"]
            if LastUpdateStatus == "InProgress":
                print(f"- Check after {wait_time} second{'s' if wait_time > 1 else ''}: InProgress")
                continue
            elif LastUpdateStatus == "Successful":
                print(f"- Check after {wait_time} second{'s' if wait_time > 1 else ''}: Successful")
                break
            else:
                print(f"- Check after {wait_time} second{'s' if wait_time > 1 else ''}: {LastUpdateStatus}")
                bad_state = True
                break
        if attempts == max_check_attempts:
            bad_state = True

        if bad_state:
            return {
                "reason": f"Function {function_arn} configuration LastUpdateStatus became {LastUpdateStatus}",
            }
    else:
        print("- No placeholders to replace. Not updating function.")

    if create_new_version or (create_alias and alias_name):
        if not function_changed:
            function_config = lambda_client.get_function_configuration(FunctionName=function_arn)
            latest_version = get_latest_lambda_function_version(function_arn, lambda_client)
            if latest_version["CodeSha256"] == function_config["CodeSha256"]:
                print("- Latest version of function already matches the active code. Not creating a new version.")
                create_new_version = False
            if create_alias and alias_name:
                aliases = lambda_client.list_aliases(
                    FunctionName=function_arn, FunctionVersion=latest_version["Version"]
                )

                for alias in aliases["Aliases"]:
                    if alias["Name"] == alias_name:
                        if alias["FunctionVersion"] != latest_version["Version"]:
                            alias_version = lambda_client.get_function_configuration(
                                FunctionName=function_arn, Qualifier=alias["FunctionVersion"]
                            )
                        else:
                            alias_version = latest_version
                        if alias_version["CodeSha256"] == function_config["CodeSha256"]:
                            print(
                                f"- Alias name {alias_name} already points to a version matching the current revision. Not creating or updating the alias."
                            )
                            create_alias = False

        if create_new_version:
            print("- Creating new function version")
            function_config = lambda_client.publish_version(
                FunctionName=function_config["FunctionArn"],
                CodeSha256=code_sha256,
                Description=version_description,
                RevisionId=function_config["RevisionId"],
            )

        if create_alias:
            try:
                print(f'- Attempting to create new alias with name "{alias_name}"')
                _ = lambda_client.create_alias(
                    FunctionName=function_arn, Name=alias_name, FunctionVersion=function_config["Version"]
                )
            except ClientError as e:
                print(f"- Alias already exists. Pointing alias to a version matching the current revision.")
                if e.response["Error"]["Code"] == "ResourceConflictException":
                    _ = lambda_client.update_alias(
                        FunctionName=function_arn, Name=alias_name, FunctionVersion=function_config["Version"]
                    )
                else:
                    raise e

    return {"status": SUCCESS}
