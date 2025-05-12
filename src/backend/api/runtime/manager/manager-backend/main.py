from ast import literal_eval
from base64 import urlsafe_b64encode
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from datetime import datetime
from hashlib import sha256
from json import loads, dumps
from random import randrange
from traceback import format_exc
from time import sleep
import boto3
import os
import re

with open("./config.json", "r") as f:
    config = literal_eval(f.read())

dynamodb_resource = None
resumes_table = None
s3 = None

s3_config = Config(signature_version="v4", region_name=config["s3_region"])

clients_loaded = False
error_loading = False

ret_headers = {
    "Access-Control-Allow-Credentials": config["Access_Control_Allow_Credentials"],
    "Access-Control-Allow-Origin": config["Access_Control_Allow_Origin"],
    "Content-Type": "application/json",
}

ret_bad_request_message = {"statusCode": 500, "body": dumps("Internal Server Error")}
# Based on HTML4's allowed URL character listing
invalid_id_characters_regex = re.compile(r"[^a-zA-Z0-9_-]")


def handler(event, context) -> dict:
    load_table_and_s3()

    ret_status_code = 500
    ret_body = None

    try:
        resource = event["resource"].split("/")[-1]
        body = loads(event["body"])

        resume_id = body["id"] if "id" in body.keys() else None
        company = body["company"] if "company" in body.keys() else None
        job_title = body["job_title"] if "job_title" in body.keys() else None
        job_posting = body["job_posting"] if "job_posting" in body.keys() else None

        print(f"Job type: {resource}")

        resume_id_invalid_characters = []
        if resume_id:
            resume_id_invalid_characters = set(invalid_id_characters_regex.findall(resume_id))

        if resume_id_invalid_characters:
            message = (
                f"ERROR: Resume id contains the following invalid characters: {', '.join(resume_id_invalid_characters)}"
            )
            print(message)
            ret_status_code = 400
            ret_body = dumps(message)

        elif resource == "generate-presigned-url":
            print(f"Attempting to generate presigned url with resume id of {resume_id}")
            item = generate_resume_presigned_url(resume_id=resume_id)
            ret_status_code = item["statusCode"]
            ret_body = item["body"]
            print(
                f"generated the following URL: {item['body']['url']} with the content type {item['body']['headers']['Content-Type']}"
            )

        elif resource == "add-resume":
            print(
                f'Attempting to add resume - ID: "{resume_id}", company: "{company}", job title: "{job_title}", job posting: "{job_posting}"'
            )
            item = add_resume(resume_id=resume_id, company=company, job_title=job_title, job_posting=job_posting)
            if item["statusCode"] == 200:
                ret_body = item["attributes"]
            ret_status_code = item["statusCode"]

        elif resource == "update-resume":
            # At least one of the values must have been provided to update the DynamoDB item
            if company or job_title or job_posting:
                print(
                    f'Attempting to update resume - ID: "{resume_id}", company: "{company}", job title: "{job_title}", job posting: "{job_posting}"'
                )
                item = update_resume(resume_id=resume_id, company=company, job_title=job_title, job_posting=job_posting)
                ret_status_code = item["statusCode"]
                if item["statusCode"] == 404:
                    ret_body = f"Resume ID {resume_id} either not found or has been deleted"
                elif item["statusCode"] == 500:
                    ret_body = f"There was an internal server error. Please try again later."
                else:
                    ret_body = item["attributes"]

        elif resource == "list-all-resumes":
            ret_body = list_resumes(deleted=("deleted" in body.keys()))
            ret_status_code = 200

        elif resource == "get-resume":
            print(f"Attempting to get resume with id of {resume_id}")
            item = get_item_from_table(
                resume_id=resume_id,
                projection_expression="id,company,job_title,job_posting,resume_url,date_created,view_count,resume_state,no_increment_id",
            )
            ret_body = {}
            if "resume_state" in item.keys():
                resume_state = "deleted" if "deleted" in body.keys() else "normal"
                if item["resume_state"] == resume_state:
                    print("Found item: ", end=" - ")
                    ret_body = {key: value for (key, value) in item.items() if key != "resume_state"}
                    print(", ".join([f'{key}: "{value}"' for (key, value) in ret_body.items()]))

            ret_status_code = 200

        elif resource == "delete-resume":
            print(f"Attempting to delete resume with id of {resume_id}")
            item = delete_resume(resume_id=resume_id)
            ret_status_code = item["statusCode"]
            if item["statusCode"] == 404:
                ret_body = f"Resume ID {resume_id} either not found or has already been deleted"
            else:
                ret_body = item["attributes"]

        elif resource == "undelete-resume":
            print(f"Attempting to undelete resume with id of {resume_id}")
            item = undelete_resume(resume_id=resume_id)
            ret_status_code = item["statusCode"]
            if item["statusCode"] == 404:
                ret_body = f"Resume ID {resume_id} either not found or has not been deleted"
            else:
                ret_body = item["attributes"]

        elif resource == "permanently-delete-resume":
            print(f"Attempting to permanently delete resume with id of {resume_id}")
            ret_status_code = permanently_delete_resume(resume_id=resume_id)

        elif resource == "get-dangling-resumes":
            print(f"Attempting to get all DynamoDB items and S3 objects that do not have valid associations")
            ret_status_code = 200
            dangling_resumes = get_dangling_resumes()
            if len(dangling_resumes["items"]) > 0:
                dangling_items = "\n  ".join(dangling_resumes["items"])
                print(f"Found the following dangling DynamoDB item IDs:\n  {dangling_items}")
            if len(dangling_resumes["html_objects"]) > 0:

                dangling_objects = "\n  ".join(
                    set(
                        [
                            f'{dangling_object["Key"]} - {len([obj for obj in dangling_resumes["html_objects"] if obj["Key"] == dangling_object["Key"]])} versions'
                            for dangling_object in dangling_resumes["html_objects"]
                        ]
                    )
                )
                print(f"Found the following dangling html objects:\n  {dangling_objects}")
            if len(dangling_resumes["unparsed_document_objects"]) > 0:
                dangling_objects = "\n  ".join(
                    set(
                        [
                            f'{dangling_object["Key"]} - {len([obj for obj in dangling_resumes["unparsed_document_objects"] if obj["Key"] == dangling_object["Key"]])} versions'
                            for dangling_object in dangling_resumes["unparsed_document_objects"]
                        ]
                    )
                )
                print(f"Found the following dangling unparsed document objects:\n  {dangling_objects}")
            if len(dangling_resumes["parsed_document_objects"]) > 0:
                dangling_objects = "\n  ".join(
                    set(
                        [
                            f'{dangling_object["Key"]} - {len([obj for obj in dangling_resumes["parsed_document_objects"] if obj["Key"] == dangling_object["Key"]])} versions'
                            for dangling_object in dangling_resumes["parsed_document_objects"]
                        ]
                    )
                )
                print(f"Found the following dangling parsed document objects:\n  {dangling_objects}")
            ret_body = {
                "items": len(dangling_resumes["items"]),
                "objects": len(dangling_resumes["html_objects"])
                + len(dangling_resumes["unparsed_document_objects"])
                + len(dangling_resumes["parsed_document_objects"]),
            }

        elif resource == "delete-dangling-resumes":
            ret_status_code = 200
            dangling_resumes = delete_dangling_resumes()
            ret_body = {
                "items": len(dangling_resumes["items"]),
                "objects": len(dangling_resumes["html_objects"])
                + len(dangling_resumes["unparsed_document_objects"])
                + len(dangling_resumes["parsed_document_objects"]),
            }

    except Exception:
        print(f"ERROR: {resource} - Stack Trace: {format_exc()}")
        ret_status_code = ret_bad_request_message["statusCode"]
        ret_body = ret_bad_request_message["body"]

    ret = {"statusCode": ret_status_code, "headers": ret_headers}
    if ret_body == None:
        ret_body = ""

    try:
        ret["body"] = dumps(ret_body)
    except:
        ret["body"] = '""'
    print(f"Returning with code {ret_status_code}")
    return ret


def load_table_and_s3() -> None:
    """
    Loads the S3 and DynamoDB objects into global variables
    """
    if not clients_loaded:
        try:
            globals()["s3"] = boto3.client("s3", config=s3_config)
            _ = s3.list_objects_v2(Bucket=config["s3_bucket_webpage"], Prefix="nothing")
            _ = s3.list_objects_v2(Bucket=config["s3_bucket_documents"], Prefix="nothing")
        except Exception as e:
            print(f"Cound not open s3 client. Error: {e}")
            raise e

        try:
            globals()["dynamodb_resource"] = boto3.resource("dynamodb", region_name=config["dynamodb_table_region"])
            globals()["resumes_table"] = dynamodb_resource.Table(config["dynamodb_table"])
            _ = get_item_from_table(resume_id="no_item")
        except Exception as e:
            print(f"Could not open DynamoDB table. Error {e}")
            raise e

        print("S3 clients and DynamoDB table loaded")

        globals()["clients_loaded"] = True


def generate_resume_presigned_url(*, resume_id: str = None) -> str:
    """
    Generates a signed URL to put a file into the bucket provided in the config.json file

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB
    company : str
        The resume's intended recipient
        Required if resume ID is not provided
    job_title : str
        The position's title
        Required if resume ID is not provided
    job_posting : str
        The url of the job's posting
        Required if resume ID is not provided

    Raises
    ------
    ValueError
        If the required parameters are provided, depending on if resume_id is set or not.

    Returns
    ------
    dict {statusCode, body}
        statusCode : int
            200 - Success
            409 - A cache invalidation is currently in progress
        body : dict
            url : str
                A pre-signed url to PUT a file to
            headers : dict {key, value}
                A dict of key: value pairs for headers that must accompany the PUT request
    """
    object_key = None
    if resume_id:
        object_key = f"{resume_id}"
        existing_item = get_item_from_table(resume_id=resume_id, projection_expression="id,invalidate_cache")
        if existing_item and "invalidate_cache" in existing_item.keys():
            return {"statusCode": 409, "ret_body": {}}
    else:
        existing_objects = list_item_names_from_s3(
            bucket=config["s3_bucket_webpage"],
            prefix=config["s3_bucket_webpage_resumes_location"],
            file_extension=".html",
        )
        existing_objects = [".".join(key.split(".")[0:-1]) for key in existing_objects]

        while not object_key or object_key in existing_objects:
            random_string = generate_random_string(config["id_length"])
            object_key = f"{random_string}"

    object_key = f"{config['s3_bucket_documents_upload_location']}/{object_key}"
    object_key += ".docx"
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": config["s3_bucket_documents"], "Key": object_key, "ContentType": content_type},
        HttpMethod="PUT",
    )

    return {"statusCode": 200, "body": {"url": url, "headers": {"Content-Type": content_type}}}


def add_resume(*, resume_id: str, company: str, job_title: str, job_posting: str) -> dict:
    """
    Updates the DynamoDB item with the provided arguments

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB
    company : str
        The resume's intended recipient
    job_title : str
        The position's title
    job_posting : str
        The url of the job's posting

    Returns
    ------
    dict {statusCode, attributes}
        statusCode : int
            409 - The item already exists in the DynamoDB table
            200 - Success
        attributes : dict
            id : str
            company : str
            job_title : str
            job_posting : str
            resume_url : str
            date_created : str
            view_count : int
            no_increment_id : str
    """
    ConditionalCheckFailedException = dynamodb_resource.meta.client.exceptions.ConditionalCheckFailedException

    initial_resume_url = "RESUME_PARSE_PENDING"
    current_datetime = datetime.utcnow()
    today = current_datetime.strftime("%Y-%m-%d")
    current_time = current_datetime.strftime("%H:%M")

    no_increment_id = generate_random_string(64)

    item = {
        "id": resume_id,
        "company": company,
        "job_title": job_title,
        "job_posting": job_posting,
        "view_count": 0,
        "resume_state": "normal",
        "resume_url": initial_resume_url,
        "date_created": f"{today} - {current_time}",
        "no_increment_id": no_increment_id,
    }

    try:
        _ = resumes_table.put_item(Item=item, ConditionExpression=Attr("id").not_exists())
    except ConditionalCheckFailedException:
        return {"statusCode": 409}

    return {"statusCode": 200, "attributes": {key: value for (key, value) in item.items() if key != "resume_state"}}


def update_resume(*, resume_id: str, company: str = None, job_title: str = None, job_posting: str = None) -> dict:
    """
    Updates the DynamoDB item with the provided arguments

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB
    company : str
        The resume's intended recipient
    job_title : str
        The position's title
    job_posting : str
        The url of the job's posting

    Returns
    ------
    dict {statusCode, attributes}
        statusCode : int
            500 - Internal server error - Something wrong with DynamoDB or the call to it
            404 - The item could not be found in the table
            400 - At least one of the params company, job_title, or job_posting were not provided
            200 - Success
        attributes : dict
            id : str
            company : str
            job_title : str
            job_posting : str
            resume_url : str
            date_created : str
            view_count : int
            no_increment_id : str
    """
    ret_attributes = {}
    existing_item = get_item_from_table(resume_id=resume_id, projection_expression="id,company,job_title,job_posting")
    if not existing_item:
        return {"statusCode": 404, "attributes": ret_attributes}

    if not (company or job_title or job_posting):
        return {"statusCode": 400, "attributes": ret_attributes}

    update_expression = "SET "
    expression_attribute_values = {}
    if company:
        update_expression += "company = :newcompany"
        expression_attribute_values[":newcompany"] = company
        if "company" in existing_item.keys():
            print(f'company from {existing_item["company"]} to {company}')
    if job_title:
        if update_expression != "SET ":
            update_expression += ", "
        update_expression += "job_title = :newjob_title"
        expression_attribute_values[":newjob_title"] = job_title
        if "job_title" in existing_item.keys():
            print(f'job_title from {existing_item["job_title"]} to {job_title}')
    if job_posting:
        if update_expression != "SET ":
            update_expression += ", "
        update_expression += "job_posting = :newjob_posting"
        expression_attribute_values[":newjob_posting"] = job_posting
        if "job_posting" in existing_item.keys():
            print(f'job_posting from {existing_item["job_posting"]} to {job_posting}')

    item = resumes_table.update_item(
        Key={"id": resume_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues=expression_attribute_values,
        ReturnValues="ALL_NEW",
    )
    required_attributes = [
        "id",
        "company",
        "job_title",
        "job_posting",
        "resume_url",
        "date_created",
        "view_count",
        "no_increment_id",
    ]
    if "Attributes" in item.keys() and (
        len(set(item["Attributes"].keys()).intersection(required_attributes)) == len(required_attributes)
    ):
        ret_status = 200
        ret_attributes = {
            "id": item["Attributes"]["id"],
            "company": item["Attributes"]["company"],
            "job_title": item["Attributes"]["job_title"],
            "job_posting": item["Attributes"]["job_posting"],
            "resume_url": item["Attributes"]["resume_url"],
            "date_created": item["Attributes"]["date_created"],
            "view_count": int(item["Attributes"]["view_count"]),
            "no_increment_id": item["Attributes"]["no_increment_id"],
        }
    else:
        ret_status = 500

    return {"statusCode": ret_status, "attributes": ret_attributes}


def delete_resume(*, resume_id) -> dict:
    """
    Creates a DeleteMarker version in S3 and changes the delete_marker_version and resume_state attributes in DynamoDB

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB

    Returns
    ------
    dict {statusCode, attributes}
        statusCode : int
            404 - The item could not be found in the table
            200 - Success
        attributes : dict
            id : str
            company : str
            job_title : str
            job_posting : str
            resume_url : str
            date_created : str
            view_count : int
            no_increment_id : str
    """
    item = get_item_from_table(
        resume_id=resume_id,
        projection_expression="id,company,job_title,job_posting,resume_url,date_created,view_count,resume_state,no_increment_id,invalidate_cache",
    )
    if item and "resume_state" in item.keys() and item["resume_state"] == "normal":
        s3_object = f'{config["s3_bucket_webpage_resumes_location"]}/{resume_id}.html'
        s3_response = s3.delete_object(Bucket=config["s3_bucket_webpage"], Key=s3_object)
        print(f"Deleted S3 object {s3_object}")

        delete_marker_id = s3_response["VersionId"]

        update_expression = "SET resume_state = :newresume_state, delete_marker_id = :delete_marker_id"
        expression_attribute_values = {
            ":newresume_state": "deleted",
            ":delete_marker_id": delete_marker_id,
        }
        invalidate_cache_message = ""
        if "invalidate_cache" not in item.keys():
            update_expression = f"{update_expression}, invalidate_cache = :invalidate_cache"
            expression_attribute_values[":invalidate_cache"] = 1
            invalidate_cache_message = ", invalidate_cache = 1"

        _ = resumes_table.update_item(
            Key={"id": resume_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
        )
        print(
            f"Set DynamoDB item ID {resume_id} attributes: resume_state = deleted, delete_marker_id = {delete_marker_id}{invalidate_cache_message}"
        )
        return_attributes = {
            key: value for (key, value) in item.items() if key not in ["resume_state", "invalidate_cache"]
        }
        if "view_count" in return_attributes.keys():
            return_attributes["view_count"] = int(return_attributes["view_count"])
        return {"statusCode": 200, "attributes": return_attributes}
    else:
        return {"statusCode": 404, "attributes": {}}


def undelete_resume(*, resume_id) -> int:
    """
    Deletes the DeleteMarker from S3 and updates the DynamoDB table's delete_marker_id and resume_state attributes.

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB

    Returns
    ------
    dict {statusCode, attributes}
        statusCode : int
            404 - The item could not be found within the DynamoDB table or did not have the attribute "delete_marker_id"
            200 - Success
        attributes : dict
            id : str
            company : str
            job_title : str
            job_posting : str
            resume_url : str
            date_created : str
            view_count : int
            no_increment_id : str
    """
    item = get_item_from_table(
        resume_id=resume_id,
        projection_expression="id,company,job_title,job_posting,resume_url,date_created,view_count,resume_state,delete_marker_id,no_increment_id,invalidate_cache",
    )
    if (
        item
        and "resume_state" in item.keys()
        and item["resume_state"] == "deleted"
        and "delete_marker_id" in item.keys()
    ):
        object_name = f'{config["s3_bucket_webpage_resumes_location"]}/{resume_id}.html'
        _ = s3.delete_object(Bucket=config["s3_bucket_webpage"], Key=object_name, VersionId=item["delete_marker_id"])
        print(f"Deleted delete marker for S3 object {object_name}")

        _ = resumes_table.update_item(
            Key={"id": resume_id},
            UpdateExpression="REMOVE delete_marker_id SET resume_state = :newresume_state",
            ExpressionAttributeValues={":newresume_state": "normal"},
        )
        print(f"Removed attribute delete_marker_id with value of {item['delete_marker_id']}")
        return_attributes = {
            key: value
            for (key, value) in item.items()
            if key not in ["resume_state", "delete_marker_id", "invalidate_cache"]
        }
        if "view_count" in return_attributes.keys():
            return_attributes["view_count"] = int(return_attributes["view_count"])
        return {"statusCode": 200, "attributes": return_attributes}
    else:
        return {"statusCode": 404, "attributes": {}}


def permanently_delete_resume(*, resume_id) -> int:
    """
    Permanently deletes the S3 object and DynamoDB item with the key of resume_id

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB

    Returns
    ------
    int
        500 - There was an issue deleting S3 objects
        404 - The item could not be found within the DynamoDB table
        200 - Success
    """
    # Check if item exists in table
    item = get_item_from_table(resume_id=resume_id, projection_expression="id,resume_state")

    keys = ["Versions", "DeleteMarkers"]

    s3_resume_html_object_name = f'{config["s3_bucket_webpage_resumes_location"]}/{resume_id}.html'
    s3_resume_html_object_version = s3.list_object_versions(
        Bucket=config["s3_bucket_webpage"], Prefix=s3_resume_html_object_name, MaxKeys=1
    )
    s3_resume_html_object_exists = len(set(s3_resume_html_object_version.keys()).intersection(keys)) > 0

    s3_resume_document_object_name = f'{config["s3_bucket_documents_upload_location"]}/{resume_id}.docx'
    s3_resume_document_object_version = s3.list_object_versions(
        Bucket=config["s3_bucket_documents"], Prefix=s3_resume_document_object_name, MaxKeys=1
    )
    s3_resume_document_object_exists = len(set(s3_resume_document_object_version.keys()).intersection(keys)) > 0

    s3_resume_parsed_document_object_name = f'{config["s3_bucket_documents_parsed_location"]}/{resume_id}.docx'
    s3_resume_parsed_document_object_version = s3.list_object_versions(
        Bucket=config["s3_bucket_documents"], Prefix=s3_resume_parsed_document_object_name, MaxKeys=1
    )
    s3_resume_parsed_document_object_exists = (
        len(set(s3_resume_parsed_document_object_version.keys()).intersection(keys)) > 0
    )

    if s3_resume_html_object_exists:
        num_deleted_versions = delete_all_s3_object_versions(
            bucket=config["s3_bucket_webpage"], object_name=s3_resume_html_object_name
        )
        print(
            f"Deleted {num_deleted_versions} versions of S3 object in bucket {config['s3_bucket_webpage']} with key {s3_resume_html_object_name} "
        )
    else:
        print(f"Could not find S3 object in bucket {config['s3_bucket_webpage']} with key {s3_resume_html_object_name}")

    if s3_resume_document_object_exists:
        num_deleted_versions = delete_all_s3_object_versions(
            bucket=config["s3_bucket_documents"], object_name=s3_resume_document_object_name
        )
        print(
            f"Deleted {num_deleted_versions} versions of S3 object in bucket {config['s3_bucket_documents']} with key {s3_resume_document_object_name} "
        )
    else:
        print(
            f"Could not find S3 object in bucket {config['s3_bucket_documents']} with key {s3_resume_document_object_name}"
        )

    if s3_resume_parsed_document_object_exists:
        num_deleted_versions = delete_all_s3_object_versions(
            bucket=config["s3_bucket_documents"], object_name=s3_resume_parsed_document_object_name
        )
        print(
            f"Deleted {num_deleted_versions} versions of S3 object in bucket {config['s3_bucket_documents']} with key {s3_resume_parsed_document_object_name} "
        )
    else:
        print(
            f"Could not find S3 object in bucket {config['s3_bucket_documents']} with key {s3_resume_parsed_document_object_name}"
        )

    if item:
        item = delete_item_from_table(resume_id=resume_id)
        if item != 200:
            print(f"Unable to delete item with id {resume_id} from DynamoDB table")
            return 500
        print(f"Deleted item with id {resume_id} from DynamoDB table")
    else:
        print(f"Could not find DynamoDB item with id of {resume_id}")

    if not (
        item
        or s3_resume_html_object_exists
        or s3_resume_document_object_exists
        or s3_resume_parsed_document_object_exists
    ):
        return 404

    return 200


def list_resumes(deleted: bool = False) -> tuple:
    """
    Returns all items within DynamoDB with the provided value of deleted

    Parameters
    ----------
    deleted : bool
        Whether deleted or normal items should be returned

    Returns
    ------
    tuple
        A Tuple of dicts containing all items with the desired state
        Each dict will contain up to the following: id, company, job_title, job_posting, resume_url, date_created, view_count, and no_increment_id
    """
    items = []
    resume_state = "deleted" if deleted else "normal"
    projection_expression = "id,company,job_title,job_posting,resume_url,date_created,view_count,no_increment_id"
    item = resumes_table.query(
        IndexName=config["dynamodb_status_index"],
        ProjectionExpression=projection_expression,
        KeyConditionExpression=Key("resume_state").eq(resume_state),
    )
    has_more_items = item["Count"] > 0

    # Pagination
    while has_more_items:
        items += item["Items"]
        if "LastEvaluatedKey" in item.keys():
            item = resumes_table.query(
                IndexName=config["dynamodb_status_index"],
                ProjectionExpression=projection_expression,
                KeyConditionExpression=Key("resume_state").eq(resume_state),
                ExclusiveStartKey=item["LastEvaluatedKey"],
            )
        else:
            has_more_items = False
    print(f'Found {len(items)} DynamoDB items with the {"deleted" if deleted else "normal"} state')

    for i in range(0, len(items)):
        if "view_count" in items[i].keys():
            items[i]["view_count"] = int(items[i]["view_count"])

    return tuple(items)


def delete_dangling_resumes() -> dict:
    """
    Deletes all dangling resumes

    Returns
    ------
    dict
        A dict containing items and objects that were deleted in the following format:
        {
            items: (id1, id2, ...),
            html_objects: ({version1Dict}, {version2Dict}, ...),
            unparsed_document_objects: ({version1Dict}, {version2Dict}, ...),
            parsed_document_objects: ({version1Dict}, {version2Dict}, ...)
        }
        Each version dict will have the following format:
        {
            "Key": "KeyName",
            "VersionId": "string"
        }
    """
    dangling_resumes = get_dangling_resumes()
    ret_dict = {"items": [], "html_objects": [], "unparsed_document_objects": [], "parsed_document_objects": []}
    for item in dangling_resumes["items"]:
        deleted_item = resumes_table.delete_item(Key={"id": item}, ReturnValues="ALL_OLD")
        ret_dict["items"].append(deleted_item["Attributes"]["id"])

    if len(dangling_resumes["html_objects"]) > 0:
        dangling_html_objects = [
            {key: value for key, value in html_object.items() if key in ["Key", "VersionId"]}
            for html_object in dangling_resumes["html_objects"]
        ]
        deleted_html_objects = s3.delete_objects(
            Bucket=config["s3_bucket_webpage"], Delete={"Objects": dangling_html_objects}
        )
        if "Errors" in deleted_html_objects.keys():
            raise Exception(
                f"There was an error deleting the following objects from bucket {config['s3_bucket_webpage']}: {dumps(deleted_html_objects['Errors'])}"
            )
        print(deleted_html_objects)
        ret_dict["html_objects"] += [
            {"Key": deleted_object["Key"], "VersionId": deleted_object["VersionId"]}
            for deleted_object in deleted_html_objects["Deleted"]
        ]

    if len(dangling_resumes["unparsed_document_objects"]) > 0:
        dangling_unparsed_documents_objects = [
            {key: value for key, value in html_object.items() if key in ["Key", "VersionId"]}
            for html_object in dangling_resumes["unparsed_document_objects"]
        ]
        deleted_unparsed_document_objects = s3.delete_objects(
            Bucket=config["s3_bucket_documents"], Delete={"Objects": dangling_unparsed_documents_objects}
        )
        if "Errors" in deleted_unparsed_document_objects.keys():
            raise Exception(
                f"There was an error deleting the following objects from bucket {config['s3_bucket_documents']}: {dumps(deleted_unparsed_document_objects['Errors'])}"
            )
        ret_dict["unparsed_document_objects"] += [
            {"Key": deleted_object["Key"], "VersionId": deleted_object["VersionId"]}
            for deleted_object in deleted_unparsed_document_objects["Deleted"]
        ]

    if len(dangling_resumes["parsed_document_objects"]) > 0:
        dangling_parsed_document_objects = [
            {key: value for key, value in html_object.items() if key in ["Key", "VersionId"]}
            for html_object in dangling_resumes["parsed_document_objects"]
        ]
        deleted_parsed_document_objects = s3.delete_objects(
            Bucket=config["s3_bucket_documents"], Delete={"Objects": dangling_parsed_document_objects}
        )
        if "Errors" in deleted_parsed_document_objects.keys():
            raise Exception(
                f"There was an error deleting the following objects from bucket {config['s3_bucket_documents']}: {dumps(deleted_parsed_document_objects['Errors'])}"
            )
        ret_dict["parsed_document_objects"] += [
            {"Key": deleted_object["Key"], "VersionId": deleted_object["VersionId"]}
            for deleted_object in deleted_parsed_document_objects["Deleted"]
        ]

    return ret_dict


def get_dangling_resumes() -> dict:
    """
    Gets all DynamoDB items and S3 objects that do not have a respective associated object or item

    For a DynamoDB item to have an associated object, there must be an object in the resumes folder of the website
    bucket, the object must have at least one non-delete-marker version, and if a delete marker is the first version,
    a non-delete marker version must follow it. All of its attributes must also be filled in.

    For a website S3 object to have an associated item, the S3 object must have at least one non-delete-marker version,
    if a delete marker is the first version, a non-delete marker version must follow it, and there must be an item
    within the DynamoDB database with an ID matching the base object name.

    For an unparsed document S3 object, it must not have existed for more than 30 seconds, otherwise its parse is
    considered failed and dangling.

    For a parsed document S3 object to have an associated item, the S3 object must have at least one non-delete-marker
    version and it must have an associated dynamodb item.

    Returns
    ------
    dict
        A dict containing items and objects in the following format:
        {
            items: (id1, id2, ...),
            html_objects: ({version1Dict}, {version2Dict}, ...),
            unparsed_document_objects: ({version1Dict}, {version2Dict}, ...),
            parsed_document_objects: ({version1Dict}, {version2Dict}, ...)
        }
        Each version dict will have the following format:
        {
            "Key": "KeyName",
            "VersionId": "string",
            "is_delete_marker": boolean,
            "LastModifiedTime": datetime
        }
    """
    required_attributes = [
        "id",
        "company",
        "job_title",
        "job_posting",
        "view_count",
        "resume_state",
        "resume_url",
        "date_created",
    ]

    all_resume_ids = get_all_ids_from_table()
    all_html_objects = list_all_s3_object_versions(
        bucket=config["s3_bucket_webpage"], prefix=config["s3_bucket_webpage_resumes_location"], file_extension=".html"
    )
    all_unparsed_document_objects = list_all_s3_object_versions(
        bucket=config["s3_bucket_documents"],
        prefix=config["s3_bucket_documents_upload_location"],
        file_extension=".docx",
    )
    all_parsed_document_objects = list_all_s3_object_versions(
        bucket=config["s3_bucket_documents"],
        prefix=config["s3_bucket_documents_parsed_location"],
        file_extension=".docx",
    )

    html_ids_to_objects = {".".join(key.split("/")[-1].split(".")[0:-1]): key for key in all_html_objects.keys()}
    parsed_document_ids_to_objects = {
        ".".join(key.split("/")[-1].split(".")[0:-1]): key for key in all_parsed_document_objects.keys()
    }

    dangling_items = list()
    dangling_html_objects = list()
    dangling_parsed_document_objects = list()
    dangling_unparsed_document_objects = list()

    for resume_id in all_resume_ids:
        if resume_id in html_ids_to_objects.keys():
            item = get_item_from_table(resume_id=resume_id)
            intersection = set(item.keys()).intersection(required_attributes)
            if len(intersection) < len(required_attributes):
                dangling_items.append(resume_id)
                dangling_html_objects + s3_object_versions
                continue
            s3_object_versions = all_html_objects[html_ids_to_objects[resume_id]]
            num_versions = len([True for version in s3_object_versions if not version["is_delete_marker"]])
            is_delete_marker_first_version = s3_object_versions[0]["is_delete_marker"]
            is_delete_marker_second_version = (
                True if len(s3_object_versions) > 1 and s3_object_versions[1]["is_delete_marker"] else False
            )
            if num_versions == 0 or (is_delete_marker_first_version and is_delete_marker_second_version):
                dangling_items.append(resume_id)
                dangling_html_objects += [s3_object_versions[html_ids_to_objects[resume_id]]]
        else:
            dangling_items.append(resume_id)

    html_objects_to_ids = {value: key for key, value in html_ids_to_objects.items()}
    parsed_document_objects_to_ids = {value: key for key, value in parsed_document_ids_to_objects.items()}

    for key, versions in all_html_objects.items():
        if not html_objects_to_ids[key] in all_resume_ids:
            dangling_html_objects += versions

    for key, versions in all_unparsed_document_objects.items():
        now = datetime.now().replace(tzinfo=None)
        dangling_unparsed_document_objects += [
            version for version in versions if (now - version["LastModifiedTime"].replace(tzinfo=None)).seconds >= 30
        ]

    for key, versions in all_parsed_document_objects.items():
        num_versions = len([True for version in versions if not version["is_delete_marker"]])
        if parsed_document_objects_to_ids[key] not in all_resume_ids or num_versions == 0:
            dangling_parsed_document_objects += versions

    ret_dict = dict()
    ret_dict["items"] = tuple(dangling_items)
    ret_dict["html_objects"] = tuple(dangling_html_objects)
    ret_dict["unparsed_document_objects"] = tuple(dangling_unparsed_document_objects)
    ret_dict["parsed_document_objects"] = tuple(dangling_parsed_document_objects)

    return ret_dict


def get_all_ids_from_table() -> tuple:
    """
    Gets all ids from the DynamoDB table's ID index

    Returns
    ------
    tuple
        A tuple of all the item ids within the DynamoDB table
    """
    ret_list = []
    more_items = True
    while more_items:
        if type(more_items) is bool:
            response = resumes_table.scan(IndexName=config["dynamodb_id_index"])
        else:
            response = resumes_table.scan(IndexName=config["dynamodb_id_index"], ExclusiveStartKey=more_items)

        if "Items" in response.keys():
            for item in response["Items"]:
                ret_list.append(item["id"])
        more_items = response["LastEvaluatedKey"] if "LastEvaluatedKey" in response.keys() else False

    return tuple(ret_list)


def get_item_from_table(
    *,
    resume_id: str,
    projection_expression: str = "id,company,job_title,job_posting,resume_url,resume_state,date_created,view_count,no_increment_id",
) -> dict:
    """
    Gets an item with the key of resume_id from the DynamoDB table

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB
    projection_expression : str
        A comma separated list of attributes that should be retrieved when getting the item

    Returns
    ------
    dict
        A dictionary of the returned item attributes from DynamoDB
    None
        If no item was found
    """
    item = resumes_table.get_item(Key={"id": resume_id}, ProjectionExpression=projection_expression)
    ret = {}
    if "Item" in item.keys():
        ret = item["Item"]
        if "view_count" in item["Item"].keys():
            ret["view_count"] = int(ret["view_count"])

    return ret


def delete_item_from_table(*, resume_id: str) -> int:
    """
    Deletes an item with the key of resume_id from the DynamoDB table

    Parameters
    ----------
    resume_id : str
        The ID of the resume within DynamoDB

    Returns
    ------
    int
        200 - Item deleted successfully
        500 - Item deletion did not succeed
    """
    item = resumes_table.delete_item(Key={"id": resume_id}, ReturnValues="ALL_OLD")
    if "Attributes" in item.keys() and "id" in item["Attributes"].keys() and resume_id == item["Attributes"]["id"]:
        return 200
    else:
        return 500


def list_item_names_from_s3(
    *,
    bucket: str,
    prefix: str,
    file_extension: str = "",
    recurse: bool = False,
    objects_with_delete_markers_only: bool = False,
) -> tuple:
    """
    Returns all items within S3 under the provided prefix

    Parameters
    ----------
    prefix : str
        The path in the S3 bucket. Should be in the format:
        folder1/subfolder1
        folder1/object.ext
    file_extension : str
        The extension to filter items to
    recurse : bool
        Whether or not subfolders and objects should be fetched

    Returns
    ------
    tuple
        A Tuple of string paths and objects with paths
    """
    s3_objects = []
    s3_object = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    has_more_objects = s3_object["KeyCount"] > 0

    # Pagination
    while has_more_objects:
        if recurse:
            s3_objects += [s3_object["Key"] for s3_object in s3_object["Contents"] if s3_object["Key"] != f"{prefix}/"]
        else:
            s3_objects += [
                s3_object["Key"]
                for s3_object in s3_object["Contents"]
                if len(s3_object["Key"].split("/")) == 2 and s3_object["Key"] != f"{prefix}/"
            ]
        if "NextContinuationToken" in s3_object.keys():
            s3_object = s3.list_objects_v2(
                Bucket=bucket,
                Prefix=prefix,
                ContinuationToken=s3_object["NextContinuationToken"],
            )
        else:
            has_more_objects = False

    if len(s3_objects) > 0 and file_extension != "":
        s3_objects = [s3_object for s3_object in s3_objects if s3_object.endswith(file_extension)]

    return tuple(s3_objects)


def list_all_s3_object_versions(
    *, bucket: str, prefix: str, file_extension: str = "", recursive: bool = False
) -> tuple:
    """
    Lists all versions of a specified prefix in the given S3 bucket

    Parameters
    ----------
    bucket : str
        The bucket to list versions from
    prefix : str
        The prefix to list
    file_extension : str
        The extension to filter items to

    Returns
    ----------
    dict
        A dictionary of dictionaries with the following format:
        {
            "KeyName1": [
                {"Key": "KeyName1", VersionId": "string", "is_delete_marker": boolean, "LastModifiedTime": datetime},
                {"Key": "KeyName1", "VersionId": "string", "is_delete_marker": boolean, "LastModifiedTime": datetime}
            ],
            "KeyName2": [
                {"Key": "KeyName2", "VersionId": "string", "is_delete_marker": boolean, "LastModifiedTime": datetime}
            ]
        }
    """
    levels = len(prefix.split("/"))
    s3_objects = s3.list_object_versions(Bucket=bucket, Prefix=prefix)
    has_more_objects = len(set(s3_objects.keys()).intersection(["Versions", "DeleteMarkers"]))
    objects = dict()
    # Pagination
    while has_more_objects:
        # Delete the versions per page
        if "DeleteMarkers" in s3_objects.keys():
            for version in s3_objects["DeleteMarkers"]:
                if not recursive and len(version["Key"].split("/")) - 1 != levels:
                    continue
                if version["Key"] not in objects.keys():
                    objects[version["Key"]] = list()
                objects[version["Key"]] += [
                    {
                        "Key": version["Key"],
                        "VersionId": version["VersionId"],
                        "LastModifiedTime": version["LastModified"],
                        "is_delete_marker": True,
                    }
                ]

        if "Versions" in s3_objects.keys():
            for version in s3_objects["Versions"]:
                if not recursive and len(version["Key"].split("/")) - 1 != levels:
                    continue
                if version["Key"] not in objects.keys():
                    objects[version["Key"]] = list()
                objects[version["Key"]] += [
                    {
                        "Key": version["Key"],
                        "VersionId": version["VersionId"],
                        "LastModifiedTime": version["LastModified"],
                        "is_delete_marker": False,
                    }
                ]

        if "NextVersionIdMarker" in s3_objects.keys():
            s3_objects = s3.list_object_versions(
                Bucket=bucket, Prefix=prefix, VersionIdMarker=s3_objects["NextVersionIdMarker"]
            )
        else:
            has_more_objects = False

    # Filter the objects to the file extension
    if file_extension != "":
        objects = {key: value for key, value in objects.items() if key.endswith(file_extension)}
        if len(objects) == 0:
            return {}

    # Sort the objects by date, newest first
    objects = {
        key: sorted(value, key=lambda obj: obj["LastModifiedTime"], reverse=True) for key, value in objects.items()
    }

    return objects


def delete_all_s3_object_versions(*, bucket: str, object_name: str) -> int:
    """
    Deletes all versions of an object in the given S3 bucket

    Parameters
    ----------
    bucket : str
        The bucket to delete the object versions from
    object_name : str
        The object key to delete

    Returns
    ----------
    int
        Number of deleted versions
    """
    num_deleted_versions = 0
    s3_object = s3.list_object_versions(Bucket=bucket, Prefix=object_name)
    has_more_objects = len(set(s3_object.keys()).intersection(["Versions", "DeleteMarkers"]))

    # Pagination
    while has_more_objects:
        # Delete the versions per page
        if "DeleteMarkers" in s3_object.keys():
            delete_marker_versions = [
                {"Key": version["Key"], "VersionId": version["VersionId"]} for version in s3_object["DeleteMarkers"]
            ]

            # Delete the delete markers first to allow those s3 event notifications to go through
            _ = s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_marker_versions})
            if "Errors" in s3_object.keys():
                print(f"Error deleting some delete marker versions. Errors:\n{dumps(s3_object['Errors'])}")
                raise ConnectionError("Error deleting versions")
            else:
                num_deleted_versions += len(delete_marker_versions)

        if "Versions" in s3_object.keys():
            object_versions = [
                {"Key": version["Key"], "VersionId": version["VersionId"]} for version in s3_object["Versions"]
            ]
            _ = s3.delete_objects(Bucket=bucket, Delete={"Objects": object_versions})
            if "Errors" in s3_object.keys():
                print(f"Error deleting some versions. Errors:\n{dumps(s3_object['Errors'])}")
                raise ConnectionError("Error deleting versions")
            else:
                num_deleted_versions += len(object_versions)

        if "NextVersionIdMarker" in s3_object.keys():
            s3_object = s3.list_object_versions(
                Bucket=bucket, Prefix=object_name, VersionIdMarker=s3_object["NextVersionIdMarker"]
            )
        else:
            has_more_objects = False

    return num_deleted_versions


def generate_random_string(length: int) -> str:
    """
    Returns a randomly generated string of [A-Za-z1-9] characters with the provided length

    Parameters
    ----------
    length : int
        The number of characters to generate

    Returns
    ------
    str
        A string of characters
    """
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    string = ""
    for _ in range(0, length):
        string += chars[randrange(0, len(chars) - 1, 1)]
    return string
