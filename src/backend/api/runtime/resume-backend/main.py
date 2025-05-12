from datetime import datetime
from hashlib import sha256
from json import loads, dumps
from re import match
from traceback import format_exc
import ast
import boto3

with open("./config.json", "r") as f:
    config = ast.literal_eval(f.read())

try:
    dynamodb = boto3.resource("dynamodb", region_name=config["dynamodb_table_region"])
    resumes_table = dynamodb.Table(config["dynamodb_resumes_table"])
    viewers_table = dynamodb.Table(config["dynamodb_resume_viewers_table"])
    views_table = dynamodb.Table(config["dynamodb_resume_views_table"])
    error_loading_tables = False
except Exception as e:
    print(f"Error loading table. Stack trace: {format_exc()}")
    error_loading_tables = True

ret_headers = {
    "Access-Control-Allow-Credentials": config["Access_Control_Allow_Credentials"],
    "Access-Control-Allow-Origin": config["Access_Control_Allow_Origin"],
    "Content-Type": "application/json",
}

internal_server_error_response = {"statusCode": "500", "body": "Internal server error", "headers": ret_headers}

bad_resume_id_response = {"statusCode": "404", "body": "ID not found", "headers": ret_headers}


def handler(event, context):
    # if loading the tables errored out or the uri doesn't match content/[12 character alphanumeric].html format
    # then return response with no further processing
    if error_loading_tables:
        return internal_server_error_response

    response = resume_id = resume_exists = None
    try:
        body = loads(event["body"])

        source_ip = event["headers"]["X-Forwarded-For"]
        resume_id = body["id"]
        query_strings = event["queryStringParameters"] if "queryStringParameters" in event.keys() else {}
        no_increment_id = ""
        if query_strings:
            if "noIncrement" not in query_strings.keys():
                raise RuntimeError("Query string noIncrement not found")
            else:
                no_increment_id = query_strings["noIncrement"]
    except Exception:
        print(f"Error getting event data. Stack trace: {format_exc()}")
        response = internal_server_error_response

    print(f"- Resume ID: {resume_id}")
    print(f"- No Increment ID: {no_increment_id}")

    if resume_id:
        try:
            resume_exists = check_if_resume_exists(resume_id)
        except Exception:
            print(f"Error getting item from resumes table. Stack trace: {format_exc()}")
            response = internal_server_error_response

    if resume_exists:
        if no_increment_id and check_if_no_increment_id_matches(resume_id, no_increment_id):
            print("- No Increment ID matches")
            view_count = get_view_count()
        else:
            print("- No Increment ID does not match")
            view_count = increase_view_count(resume_id=resume_id)

        try:
            add_item_to_viewers_table(clientIp=source_ip)

            response = {
                "statusCode": "200",
                "body": dumps({"views": f"{view_count}"}),
                "headers": ret_headers,
            }
        except Exception:
            print(f"Error updating or getting data from DynamoDB. Stack trace: {format_exc()}")
            response = internal_server_error_response
    else:
        print("- Resume ID does not exist")
        response = bad_resume_id_response

    print(f"- Sending response: {dumps(response, indent=4)}")

    return response


def check_if_resume_exists(resume_id: str) -> bool:
    response = resumes_table.get_item(Key={"id": resume_id}, AttributesToGet=["id"])

    if "Item" in response.keys():
        return True
    else:
        return False


def check_if_no_increment_id_matches(resume_id: str, no_increment_id: str) -> bool:
    response = resumes_table.get_item(Key={"id": resume_id}, AttributesToGet=["id", "no_increment_id"])

    if "Item" not in response.keys():
        raise ValueError(f"resume ID {resume_id} could not be found")
    if "no_increment_id" not in response["Item"].keys():
        return False
    return response["Item"]["no_increment_id"] == no_increment_id


def add_item_to_viewers_table(*, clientIp: str) -> None:
    viewer_hash = sha256(bytes(clientIp, "UTF-8")).hexdigest()
    now = datetime.now()
    current_datetime = now.strftime("%Y-%m-%d - %H:%M:%S.%f")
    _ = viewers_table.put_item(Item={"viewer": viewer_hash, "datetime": current_datetime})


def increase_view_count(*, resume_id: str) -> int:
    _ = resumes_table.update_item(
        Key={"id": resume_id},
        UpdateExpression="ADD view_count :newtotal",
        ExpressionAttributeValues={
            ":newtotal": 1,
        },
        ReturnValues="NONE",
    )

    views = views_table.update_item(
        Key={"id": "all_resumes"},
        UpdateExpression="ADD view_count :newtotal",
        ExpressionAttributeValues={":newtotal": 1},
        ReturnValues="UPDATED_NEW",
    )

    return views["Attributes"]["view_count"]


def get_view_count() -> int:
    response = views_table.get_item(Key={"id": "all_resumes"}, AttributesToGet=["id", "view_count"])

    if "Item" not in response.keys():
        raise ValueError(f"all_resumes could not be found in views table")

    return response["Item"]["view_count"]
