from common.common import SUCCESS, FAILED, send_cfn_signal

import boto3
import re

from botocore.config import Config
from json import dumps, loads
from traceback import format_exc

region = "us-east-1"
cloudfront_config = Config(region_name=region, signature_version="v4", retries={"max_attempts": 2, "mode": "adaptive"})
cloudfront_client = boto3.client("cloudfront", config=cloudfront_config)
domain_name_placeholder_string = "[BASE_DOMAIN_NAME_PLACEHOLDER]"
domain_uri_placeholder_string = "[BASE_DOMAIN_URL_PLACEHOLDER]"
test_uri_placeholder_string = "[TEST_URI_PLACEHOLDER]"
test_event_filename = "test-event.json"
placeholder_regex = re.compile("(\[[A-Z_0-9]*?(?:PLACEHOLDER)\])")


class TestFailError(Exception):
    pass


def handler(event, context):
    """
    Replaces a CloudFront function's placeholders

    Parameters
    ----------
    event : dict
        An AWS-provided dictionary containing the replacement criteria. The dictionary's ResourceProperties field should
        contain the following items:
            "ResourceProperties": {
                "cloudfront_function_name": str,
                "function_stage": str, (Must be either "DEVELOPMENT" or "LIVE")
                "domain_name": str, (A comma separated list of domain names to redirect from, ex. example.com, ww.example.com, etc.)
                "domain_uri": str (The uri to redirect to, ex. https://www.example.com)
            }
    context : dict
        Provided by AWS

    Raises
    ----------
    TestFailError - If a Cloudfront Test fails
    """
    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    function_name = resource_properties["cloudfront_function_name"]
    function_stage = resource_properties["function_stage"]
    function_etag = ""

    if function_stage not in ["DEVELOPMENT", "LIVE"]:
        raise RuntimeError(f'Function stage {function_stage} is invalid. Must be either "DEVELOPMENT" or "LIVE"')

    print(f"- Request type: {request_type}")
    print(f"- CloudFront function: {function_name}")
    print(f"- Function stage: {function_stage}")

    physical_resource_id = "".join([part.capitalize() for part in function_name.split("-")])

    ret = {
        "response_url": event["ResponseURL"],
        "request_id": event["RequestId"],
        "stack_id": event["StackId"],
        "status": FAILED,
        "logical_resource_id": event["LogicalResourceId"],
        "physical_resource_id": physical_resource_id,
    }

    try:
        if request_type != "Delete":
            function_etag = cloudfront_client.describe_function(Name=function_name)["ETag"]
            print(f"- Function ETag: {function_etag}")

            domain_name = resource_properties["domain_name"]
            domain_uri = resource_properties["domain_uri"]

            function_config = {"Comment": "", "Runtime": "cloudfront-js-2.0"}

            function = cloudfront_client.get_function(Name=function_name, Stage=function_stage)
            function_code = function["FunctionCode"].read().decode("utf-8")

            remaining_placeholders = placeholder_regex.findall(function_code)

            if remaining_placeholders:
                domain_names = '"' + '","'.join(domain_name.split(",")) + '"'
                print(f'- Substituting domain names with "{domain_names}" and domain redirect URI with "{domain_uri}"')
                function_code = function_code.replace(domain_name_placeholder_string, domain_names)
                function_code = function_code.replace(domain_uri_placeholder_string, domain_uri)

                remaining_placeholders = placeholder_regex.findall(function_code)
                if remaining_placeholders:
                    raise RuntimeError(f"The placeholders {', '.join(remaining_placeholders)} still remain!")

                function_bytes = function_code.encode("utf-8")

                response = cloudfront_client.update_function(
                    Name=function_name,
                    IfMatch=function_etag,
                    FunctionConfig=function_config,
                    FunctionCode=function_bytes,
                )
                function_etag = response["ETag"]
                print(f"- New function ETag: {function_etag}")

                print("- Running tests...")
                test = test_function(
                    name=function_name,
                    etag=function_etag,
                    test_uri=domain_name,
                    expected_location=domain_uri,
                    expected_status_code=301,
                )
                if test["result"] != "PASS":
                    raise TestFailError(test["reason"])

                test = test_function(
                    name=function_name, etag=function_etag, test_uri=domain_uri, expected_host=domain_uri
                )
                if test["result"] != "PASS":
                    raise TestFailError(test["reason"])

                print("- Publishing function")
                _ = cloudfront_client.publish_function(Name=function_name, IfMatch=function_etag)
            else:
                print("- Replacements were already completed")

        ret["status"] = SUCCESS
        send_cfn_signal(**ret)

    except TestFailError as e:
        ret["reason"] = ", ".join(e.args)
        send_cfn_signal(**ret)
    except Exception as e:
        print(format_exc())
        ret["reason"] = ", ".join(e.args)
        send_cfn_signal(**ret)


def test_function(
    *,
    name: str,
    etag: str,
    test_uri: str,
    expected_location: str = None,
    expected_host: str = None,
    expected_status_code: int = None,
) -> dict:
    """
    Tests a specified CloudFront function based on the provided, expected values

    Parameters
    ----------
    name : str
        The name of the CloudFront function
    etag : str
        The etag of the CloudFront function to be tested
    test_uri : str
        The simulated URI a request would be generated for
    expected_location : str
        The expected redirect location (full URI) that should be sent in response (will not be present if host is expected)
    expected_host : str
        The expected host header value that should be sent in response (will not be present if location is expected)
    expected_status_code : str
        The expected return HTTP status code

    Raises
    ----------
    ValueError - If expected_location, expected_host, or expected_status_code are not provided or if expected_location
        and expected_host are provided at the same time

    Returns
    ----------
    dict - A dict containing result (PASS or FAIL) and reason (if result == FAIL)
    """
    if not (expected_location or expected_host or expected_status_code):
        raise ValueError(
            "At least one of the parameters expected_location, expected_host, or expected_status_code must be provided"
        )
    elif expected_location and expected_host:
        raise ValueError("The parameters expected_location and expected_host cannot be both provided at the same time")

    with open(test_event_filename) as f:
        event = dumps(loads(f.read()))

    event = event.replace(test_uri_placeholder_string, test_uri).encode("utf-8")

    response = cloudfront_client.test_function(Name=name, IfMatch=etag, Stage="DEVELOPMENT", EventObject=event)

    ret_dict = {"result": None, "reason": ""}

    test_result_output_string = response["TestResult"]["FunctionOutput"]
    test_result_output = loads(test_result_output_string)

    error = False
    error_string = "-----TEST FAILED-----"
    if expected_location and not test_result_output["response"]["headers"]["location"]["value"].startswith(
        expected_location
    ):
        error = True
        error_string += f'\n- Expected location: {expected_location}, received location: {test_result_output["response"]["headers"]["location"]["value"]}'
    if expected_host and test_result_output["request"]["headers"]["host"]["value"] != expected_host:
        error = True
        error_string += f'\n- Expected host: {expected_host}, received host: {test_result_output["response"]["headers"]["host"]["value"]}'
    if expected_status_code and test_result_output["response"]["statusCode"] != expected_status_code:
        error = True
        error_string += f'\n- Expected status code: {expected_status_code}, received status code: {test_result_output["response"]["statusCode"]}'

    if error:
        print(error_string)
        ret_dict["result"] = "FAIL"
        ret_dict["reason"] = error_string
    else:
        ret_dict["result"] = "PASS"

    return ret_dict
