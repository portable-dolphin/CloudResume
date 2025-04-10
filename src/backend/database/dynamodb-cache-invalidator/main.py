from ast import literal_eval
from base64 import urlsafe_b64encode
from botocore.config import Config
from botocore.exceptions import ClientError
from time import sleep, time
from traceback import format_exc

import boto3

with open("./config.json", "r") as f:
    config = literal_eval(f.read())

dynamodb_resource = None
resumes_table = None
cloudfront_client = None
cloudfront_config = Config(signature_version="v4")


def handler(event, context) -> dict:
    resume_ids = list(set([record["dynamodb"]["Keys"]["id"]["S"] for record in event["Records"]]))
    print(f"- Received the following resumes IDs to invalidate: {', '.join(resume_ids)}")
    try:
        globals()["dynamodb_resource"] = boto3.resource("dynamodb", region_name=config["dynamodb_table_region"])
        globals()["resumes_table"] = dynamodb_resource.Table(config["dynamodb_table_name"])
    except Exception:
        print(f"Could not open DynamoDB table. Error {format_exc()}")
        return

    try:
        globals()["cloudfront_client"] = boto3.client("cloudfront", config=cloudfront_config)
    except Exception:
        print(f"Could not get CloudFront client. Error {format_exc()}")
        return

    max_invalidation_files = 500
    for chunk_num in range(0, len(resume_ids), max_invalidation_files):
        min_range = chunk_num
        max_range = min(len(resume_ids), chunk_num + max_invalidation_files)
        chunk = resume_ids[min_range:max_range]
        print(f"- {min_range + 1}-{max_range}/{len(resume_ids)}: Attempting to invalidate")
        try:
            invalidation = cloudfront_client.create_invalidation(
                DistributionId=config["cloudfront_distribution_id"],
                InvalidationBatch={
                    "Paths": {
                        "Quantity": len(chunk),
                        "Items": [f"{config['resume_path']}/{resume_id}.html" for resume_id in chunk],
                    },
                    "CallerReference": str(time()).replace(".", ""),
                },
            )
            invalidation_id = invalidation["Invalidation"]["Id"]
        except ClientError as e:
            print(
                f"Could not invalidate the following resume_ids due to {e.response['Error']['Message']}: {', '.join(chunk)}"
            )
            batch_update_items(resume_ids=chunk, message="cache_invalidation_error")
            continue
        print(f"- {min_range + 1}-{max_range}/{len(resume_ids)}: Invalidation submitted. Awaiting completion")
        invalidated = False
        wait_time = 5
        while not invalidated:
            try:
                status = cloudfront_client.get_invalidation(
                    DistributionId=config["cloudfront_distribution_id"],
                    Id=invalidation_id,
                )
            except ClientError as e:
                print(f"Error getting invalidation status: {e.response['Error']['Message']}")
                return
            if status["Invalidation"]["Status"] != "Completed":
                sleep(wait_time)
            else:
                invalidated = True
        print(f"- {min_range + 1}-{max_range}/{len(resume_ids)}: Invalidation completed. Updating DynamoDB database.")
        batch_update_items(resume_ids=chunk)
    print("- All updates complete")


def batch_update_items(*, resume_ids: list, message=None):
    """
    Batch updates all items to remove invalidate_cache attribute and set the resume_url attribute
    to its proper url

    Parameters
    ----------
    resume_id : list
        A list of the resume IDs to update within DynamoDB
    """
    ids_to_update = resume_ids.copy()
    i = 0
    max_items = 25
    while i * max_items < len(ids_to_update):
        min_range = max_items * i
        max_range = min(max_items * (i + 1), len(ids_to_update))
        i += 1
        id_chunk = ids_to_update[min_range:max_range]
        print(
            f"-- {min_range + 1}-{max_range}/{len(ids_to_update)}: Attempting to update resume_url and invalidate_cache attributes."
        )
        retry = 0
        max_retries = 3
        while id_chunk:
            sleep(retry**2)
            statements = [
                'UPDATE "' + config["dynamodb_table_name"] + '" SET resume_url=? REMOVE invalidate_cache WHERE id=?',
            ] * len(resume_ids)
            parameters = [
                [f"{config['resumes_url_base']}/{resume_id}.html" if not message else message, resume_id]
                for resume_id in resume_ids
            ]
            try:
                response = dynamodb_resource.meta.client.batch_execute_statement(
                    Statements=[
                        {"Statement": statement, "Parameters": params}
                        for statement, params in zip(statements, parameters)
                    ]
                )
            except ClientError as e:
                print(
                    f"-- {min_range + 1}-{max_range}/{len(ids_to_update)}: Error updating resume_ids. Error: {format_exc()}"
                )
                raise e

            id_chunk = []
            errors = []
            for item in response["Responses"]:
                if "Error" in item.keys():
                    resume_id = item["Error"]["Item"]["resume_id"]["S"]
                    if item["Error"]["Code"] in ["ProvisionedThroughputExceeded", "RequestLimitExceeded"]:
                        id_chunk.append(resume_id)
                    else:
                        errors.append(f"{resume_id}: {item['Error']['Code']}")
            if errors:
                print(f"-- {min_range + 1}-{max_range}/{len(ids_to_update)}: The following items returned an error:")
                print("\n".join(errors))
                raise RuntimeError("Error updating items")
            if id_chunk:
                if retry == max_retries:
                    print(
                        f"-- {min_range + 1}-{max_range}/{len(ids_to_update)}: Could not update the following resumes:"
                    )
                    print(", ".join(id_chunk))
                    raise RuntimeError("Maximum retries exceeded")
                retry += 1
        print(f"{min_range + 1}-{max_range}/{len(ids_to_update)}: Update complete")
