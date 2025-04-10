#!/usr/bin/env python3
from argparse import ArgumentParser
from benedict import benedict
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from json import loads
from os import chdir, environ, walk
from pathlib import Path
from shutil import which
from subprocess import PIPE, STDOUT, Popen, TimeoutExpired
from time import sleep
from typing import Dict

import boto3
import re

from destroy_app import destroy_cdk

from vars import check_env_vars, env

_placeholder_regex = re.compile(r"(\[[A-Z_0-9]*?(?:ARN|PLACEHOLDER|STRING)\])")

infrastructure_error_options = ["destroy", "retain", "rollback"]

s3_bucket_ready_timeout = 60 * 60
s3_bucket_ready_retry_wait = 15


def create_cdk(
    aws_profile: str = "",
    deploy_infrastructure: bool = True,
    infrastructure_error_action: str = "destroy",
    deploy_website: bool = True,
    wait_for_s3_bucket_ready: bool = True,
    save_view_counter_path: str = None,
    set_view_counter: int = None,
):
    if infrastructure_error_action not in infrastructure_error_options:
        raise ValueError(f"infrastructure_error_action must be one of {', '.join(infrastructure_error_options)}")

    if not save_view_counter_path is None and not set_view_counter is None:
        raise ValueError("save_view_counter_path and set_view_counter cannot be used at the same time")

    if not save_view_counter_path is None:
        if not isinstance(save_view_counter_path, str):
            raise ValueError("save_view_counter_path must be a str")

        view_counter_path = Path(save_view_counter_path)
        if view_counter_path.exists():
            raise ValueError(f"{save_view_counter_path} provided already exists")
        try:
            view_counter_path.touch()
        except PermissionError:
            raise ValueError(f"unable to write to {save_view_counter_path}")

    if not set_view_counter is None and not isinstance(set_view_counter, int):
        raise ValueError("save_view_counter_path must be an int")

    check_env_vars()

    globals()["env"] = {f"[{key}_PLACEHOLDER]": value for key, value in env.items()}

    config_file = f"{Path(__file__).parent.resolve()}/src/backend/configuration/config/cdk.json"
    cdk_config = benedict(config_file, format="json", keyattr_dynamic=True)
    if not cdk_config.static_variables.deploy_region:
        raise RuntimeError(f'Variable "static_variables.deploy_region" could not be found in {config_file}')

    deploy_region = cdk_config.static_variables.deploy_region
    if deploy_infrastructure:
        args = [
            "cdk deploy --require-approval never --all --rollback "
            f"{'false' if infrastructure_error_action == 'retain' else 'true'}"
            f"{' --profile ' + aws_profile if aws_profile else ''}"
        ]

        print(f"- Executing \"{' '.join(args)}\"")

        proc = Popen(args, stdout=PIPE, stderr=STDOUT, env=environ, universal_newlines=True, shell=True)
        return_code = None

        while True:
            return_code = proc.poll()
            if proc.stdout.readable():
                for line in iter(proc.stdout.readline, ""):
                    print(line, end="")
            if return_code != None:
                print(f"return_code: {return_code}")
                break

            sleep(0.5)

        if return_code != 0:
            print(f"Process failed with return code {return_code}")
            if infrastructure_error_options == "destroy":
                destroy_cdk()
            exit(return_code)

    if aws_profile:
        boto3.setup_default_session(profile_name=aws_profile)
    deploy_region_config = Config(
        region_name=deploy_region, signature_version="v4", retries={"max_attempts": 2, "mode": "adaptive"}
    )

    cfn_client = boto3.client("cloudformation", config=deploy_region_config)
    stacks = cfn_client.describe_stacks(StackName=f"{env['[APP_STACK_PREFIX_PLACEHOLDER]']}P5")

    placeholders = {output["Description"]: output["OutputValue"] for output in stacks["Stacks"][0]["Outputs"]} | env

    s3_bucket_webpage = placeholders["[S3_BUCKET_WEBPAGE_PLACEHOLDER]"]
    s3_bucket_documents = placeholders["[S3_BUCKET_DOCUMENTS_PLACEHOLDER]"]

    s3_client = boto3.client("s3", config=deploy_region_config)

    resume_views_table = placeholders["[DYNAMODB_RESUME_VIEWS_TABLE_PLACEHOLDER]"]
    dynamodb_client = boto3.client("dynamodb", config=deploy_region_config)
    dynamodb_resource = boto3.resource("dynamodb", config=deploy_region_config)
    dynamodb_table = dynamodb_resource.Table(resume_views_table)

    if deploy_website:
        cognito_client = boto3.client("cognito-idp", config=deploy_region_config)

        cognito_user_pool_id = placeholders["[COGNITO_USERPOOL_ID_PLACEHOLDER]"]

        website_dir = f"{Path(__file__).parent.resolve()}/src/frontend/website/"

        files_to_upload = {}
        files_missing_placeholders = {}
        for subdir, _, files in walk(website_dir, followlinks=True):
            file_location = subdir[len(website_dir) :]
            for filename in files:
                print(filename)
                contents = ""
                read_type = "r" if filename.split(".")[-1] in ["html", "js", "css"] else "rb"
                with open(f"{subdir}/{filename}", read_type) as f:
                    contents = f.read()

                key = f"{file_location}/{filename}" if file_location else filename

                if read_type == "r":
                    placeholders_to_replace = [placeholder for placeholder in _placeholder_regex.findall(contents)]
                    missing_placeholders = [
                        placeholder for placeholder in placeholders_to_replace if placeholder not in placeholders.keys()
                    ]

                    if missing_placeholders:
                        files_missing_placeholders[key] = ", ".join(missing_placeholders)
                        continue

                    contents = _replace_placeholders_in_string(contents, placeholders)
                    contents = contents.encode("utf-8")

                files_to_upload[key] = contents

        if files_missing_placeholders:
            for key, placeholders in files_missing_placeholders.items():
                print(f"Missing placeholders - {key}:\n{placeholders}")
            return

        for key, file_binary in files_to_upload.items():
            print(f"Uploading file {key}")
            _ = s3_client.put_object(Body=file_binary, Bucket=s3_bucket_webpage, Key=key)

        try:
            item = {"id": "all_resumes", "view_count": 0}
            dynamodb_table.put_item(
                Item=item,
                ConditionExpression=Attr("id").not_exists(),
            )
            print(f"Put item into dyamodb table {resume_views_table}: {item}")
        except dynamodb_client.exceptions.ConditionalCheckFailedException:
            pass

        cognito_username = placeholders["[APP_COGNITO_INITIAL_USERNAME_PLACEHOLDER]"]
        cognito_user_given_name = placeholders["[APP_COGNITO_INITIAL_USER_GIVEN_NAME_PLACEHOLDER]"]
        cognito_user_email = placeholders["[APP_COGNITO_INITIAL_USER_EMAIL_PLACEHOLDER]"]
        cognito_user_password = placeholders["[APP_COGNITO_INITIAL_USER_PASSWORD_PLACEHOLDER]"]

        print("Getting existing users")

        response = cognito_client.list_users(
            UserPoolId=cognito_user_pool_id, Filter=f'given_name = "{cognito_user_given_name}"'
        )

        if not response["Users"]:
            print("Initial user not yet created... creating")
            response = cognito_client.admin_create_user(
                UserPoolId=cognito_user_pool_id,
                Username=cognito_username,
                UserAttributes=[
                    {"Name": "given_name", "Value": cognito_user_given_name},
                    {"Name": "email", "Value": cognito_user_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
            )

            user_status = response["User"]["UserStatus"]
            if user_status != "FORCE_CHANGE_PASSWORD":
                raise RuntimeError(f"Cognito user status is not in a good state. State: {user_status}")

            print("Setting initial user password")
        else:
            print("Initial user already created")

        print("Setting initial user password")
        _ = cognito_client.admin_set_user_password(
            UserPoolId=cognito_user_pool_id,
            Username=cognito_username,
            Password=cognito_user_password,
            Permanent=True,
        )

        response = cognito_client.admin_get_user(
            UserPoolId=cognito_user_pool_id,
            Username=cognito_username,
        )

        user_status = response["UserStatus"]
        if user_status != "CONFIRMED":
            raise RuntimeError(f"Cognito user status is not in a good state. State: {user_status}")

        response = cognito_client.admin_list_groups_for_user(
            UserPoolId=cognito_user_pool_id,
            Username=cognito_username,
        )

        authorized_groups = loads(placeholders["[COGNITO_AUTHORIZED_GROUPS_ARRAY_PLACEHOLDER]"])

        existing_groups = [item["GroupName"] for item in response["Groups"]]

        for authorized_group in authorized_groups:
            if authorized_group not in existing_groups:
                print("Adding initial user to admin groups")
                _ = cognito_client.admin_add_user_to_group(
                    UserPoolId=cognito_user_pool_id, Username=cognito_username, GroupName=authorized_group
                )
            else:
                print("Initial user already in admin groups")

    if wait_for_s3_bucket_ready:
        for bucket_friendly_name, bucket in {
            "Webpage Bucket": s3_bucket_webpage,
            "Documents Bucket": s3_bucket_documents,
        }.items():
            print(f"Waiting for {bucket_friendly_name} to propagate to us-east-1")
            s3_get_url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": "no_object_here"},
                ExpiresIn=s3_bucket_ready_timeout,
                HttpMethod="GET",
            )
            while True:
                # Using curl because requests cannot get the presigned URL signature correct
                args = ['curl -s -o /dev/null -w "%{http_code}" ' + f"'{s3_get_url}'"]
                proc = Popen(args, stdout=PIPE, stderr=STDOUT, env=environ, universal_newlines=True, shell=True)
                return_code = None
                response = ""

                while True:
                    return_code = proc.poll()
                    if return_code != None:
                        response += proc.stdout.read()
                        break

                    sleep(0.5)

                if return_code != 0:
                    print(f"Process failed with return code {return_code}")
                    exit(return_code)

                if response != "307":
                    print(f"{bucket_friendly_name} ready!")
                    break

                sleep(s3_bucket_ready_retry_wait)

    if save_view_counter_path:
        {"id": "all_resumes", "view_count": 0}
        item = dynamodb_table.get_item(Key={"id": "all_resumes"}, ProjectionExpression="id,view_count")
        if "Item" in item.keys():
            view_count = int(item["Item"]["view_count"])
        else:
            raise RuntimeError("Unable to find view_count to save")

        with open(view_counter_path.as_posix(), "w") as f:
            f.write(str(view_count))
        print(f"Save view count of {view_count} to {save_view_counter_path}")

    if not set_view_counter is None:
        item = {"id": "all_resumes", "view_count": set_view_counter}
        dynamodb_table.put_item(Item=item)
    print("Done!")


def _replace_placeholders_in_string(source_string: str, placeholders: Dict[str, str]) -> str:
    placeholders_in_string = _placeholder_regex.findall(source_string)
    replacement_string = source_string
    for placeholder in set(placeholders_in_string):
        placeholder_value = placeholders[placeholder]
        replacement_string = replacement_string.replace(placeholder, placeholder_value)
    return replacement_string


if __name__ == "__main__":
    parser = ArgumentParser(prog="Resume App Deployer")
    parser.add_argument("--aws-profile", action="store", default="")
    infrastructure_group = parser.add_mutually_exclusive_group()
    infrastructure_group.add_argument("--skip-deploy-infrastructure", action="store_true")
    infrastructure_group.add_argument(
        "--infrastructure-error-action", choices=infrastructure_error_options, default="destroy"
    )
    parser.add_argument("--skip-deploy-website", action="store_true")
    parser.add_argument("--skip-bucket-ready-wait", action="store_true")
    view_counter_group = parser.add_mutually_exclusive_group()
    view_counter_group.add_argument("--save-view-counter-path", action="store", default=None)
    view_counter_group.add_argument("--set-view-counter", action="store", default=None, type=int)

    parser_args = parser.parse_args()
    args = vars(parser_args)
    create_cdk(
        aws_profile=args["aws_profile"],
        deploy_infrastructure=not args["skip_deploy_infrastructure"],
        infrastructure_error_action=args["infrastructure_error_action"],
        deploy_website=not args["skip_deploy_website"],
        wait_for_s3_bucket_ready=not args["skip_bucket_ready_wait"],
        save_view_counter_path=args["save_view_counter_path"],
        set_view_counter=args["set_view_counter"],
    )
