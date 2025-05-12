import os
import re

from aws_cdk import App, aws_lambda as Lambda, CustomResource, Duration, Fn, RemovalPolicy, Resource, Size, Stack, Stage
from aws_cdk.aws_certificatemanager import Certificate, CertificateValidation
from aws_cdk.aws_iam import (
    ArnPrincipal,
    CompositePrincipal,
    Effect,
    IPrincipal,
    ManagedPolicy,
    PolicyDocument,
    PolicyStatement,
    Role,
    ServicePrincipal,
)
from aws_cdk.aws_lambda import Alias, Function, Version, VersionOptions
from aws_cdk.aws_sns import Subscription, SubscriptionProtocol, Topic, TopicPolicy
from aws_cdk.custom_resources import Provider
from base64 import b64encode
from benedict import benedict
from enum import Enum
from hashlib import sha256
from json import loads, dumps
from json.decoder import JSONDecodeError
from pathlib import Path
from random import randrange
from typing import List, Dict, Any
from zipfile import ZipFile, ZipInfo, ZIP_STORED

from vars import env, root_dir

required_config_json_files = [
    "api.json",
    "cdk.json",
    "certificates.json",
    "cloudfront.json",
    "cognito.json",
    "dns.json",
    "dynamodb.json",
    "iam.json",
    "lambda.json",
    "monitoring.json",
    "s3.json",
    "sns.json",
]

config_dir = f"{Path(__file__).parent.resolve()}/config/"
_api_config = benedict({}, keyattr_dynamic=True)
_cdk_config = benedict({}, keyattr_dynamic=True)
_cloudfront_config = benedict({}, keyattr_dynamic=True)
_iam_config = benedict({}, keyattr_dynamic=True)
_lambda_config = benedict({}, keyattr_dynamic=True)
_sns_config = benedict({}, keyattr_dynamic=True)
_placeholder_regex = re.compile(r"(\[[A-Z_0-9]+?(?:ARN|PLACEHOLDER|STRING)\])")


class ResourceType(Enum):
    AAliasRecord = "AAliasRecord"
    CNAMERecord = "CnameRecord"
    HostedZone = "HostedZone"
    SSLCertificate = "SslCertificate"


def _get_config_file_object(config_file: str) -> benedict:
    return benedict(os.path.join(config_dir, config_file), format="json", keyattr_dynamic=True)


def get_api_config() -> benedict:
    return _get_config_file_object("api.json")


def get_cdk_config() -> benedict:
    return _get_config_file_object("cdk.json")


def get_certificates_config() -> benedict:
    return _get_config_file_object("certificates.json")


def get_cloudfront_config() -> benedict:
    return _get_config_file_object("cloudfront.json")


def get_cognito_config() -> benedict:
    return _get_config_file_object("cognito.json")


def get_dns_config() -> benedict:
    return _get_config_file_object("dns.json")


def get_dynamodb_config() -> benedict:
    return _get_config_file_object("dynamodb.json")


def get_eventbridge_config() -> benedict:
    return _get_config_file_object("eventbridge.json")


def get_iam_config() -> benedict:
    return _get_config_file_object("iam.json")


def get_lambda_config() -> benedict:
    return _get_config_file_object("lambda.json")


def get_monitoring_config() -> benedict:
    return _get_config_file_object("monitoring.json")


def get_s3_config() -> benedict:
    return _get_config_file_object("s3.json")


def get_sns_config() -> benedict:
    return _get_config_file_object("sns.json")


def test_all_json_config() -> None:
    for file in required_config_json_files:
        _ = _get_config_file_object(file)


def get_deploy_region() -> str:
    cdk_config = get_cdk_config()
    return cdk_config.static_variables.deploy_region


def get_iam_policy(stack: Stack, policy_name: str) -> ManagedPolicy:
    if not _iam_config:
        globals()["_iam_config"] = get_iam_config()
    policy_name_with_stack = _get_iam_name_with_stack(stack, policy_name)

    if policy_name_with_stack in stack.resources.iam.policies.keys():
        return stack.resources.iam.policies[policy_name_with_stack]
    else:
        policy_logical_name = _iam_config.policies[policy_name].logical_name + stack.stack_name
        policy = _iam_config.policies[policy_name].deepcopy()
        document = replace_iam_statements_placeholders(stack, policy.permissions)

        i = 0
        while i < len(document.Statement):
            if not document.Statement[i].Resource:
                document.Statement.pop(i)
            i += 1

        policy_document = PolicyDocument.from_json(document)

        managed_policy = ManagedPolicy(
            stack,
            policy_logical_name,
            document=policy_document,
            managed_policy_name=policy_name_with_stack,
        )
        stack.resources.iam.policies[policy_name_with_stack] = managed_policy
        return managed_policy


def get_iam_role(
    stack: Stack,
    role_name: str,
) -> Role:
    role_name_with_stack = _get_iam_name_with_stack(stack, role_name)

    if role_name_with_stack in stack.resources.iam.roles.keys():
        return stack.resources.iam.roles[role_name_with_stack]
    else:
        role_logical_name = _iam_config.roles[role_name].logical_name + stack.stack_name
        iam_policy_names = _iam_config.roles[role_name].policies
        iam_policies = []
        for iam_policy_name in iam_policy_names:
            iam_policy = get_iam_policy(stack, iam_policy_name)
            iam_policies.append(iam_policy)

        assumed_by = _iam_config.roles[role_name].assumed_by

        principals = get_iam_statement_principals(assumed_by)
        composite_principals = CompositePrincipal(*principals)

        role = Role(stack, role_logical_name, role_name=role_name_with_stack, assumed_by=composite_principals)
        for policy in iam_policies:
            role.add_managed_policy(policy)
        stack.resources.iam.roles[role_name_with_stack] = role
        return role


def _add_resources_to_managed_policy(
    stack: Stack, policy_name: str, statement_number: int, resources: List[str]
) -> None:
    iam_policy_config = _iam_config.policies[policy_name].permissions.deepcopy()

    managed_policy = get_iam_policy(stack, policy_name)

    statement = iam_policy_config.Statement[statement_number]
    statement.Resource += resources
    statement.Sid = f"Statement{managed_policy.document.statement_count + 1}"

    managed_policy.add_statements(PolicyStatement.from_json(statement))


def _get_iam_name_with_stack(stack: Stack, name: str) -> str:
    name_with_stack = format_logical_name_uppercase(name) + stack.stack_name
    if len(name_with_stack) > 64:
        name_with_stack = name_with_stack[-64::]
    return name_with_stack


def zip_lambda_function(function_dir_path: str, zip_destination: str, revision_id: str) -> str:
    zip_destination_file = Path(zip_destination)
    if zip_destination_file.is_file():
        zip_destination_file.unlink()

    with ZipFile(zip_destination, "w") as z:
        revision_info = ZipInfo(filename="__revision_id__.txt", date_time=(1980, 1, 1, 0, 0, 0))
        z.writestr(zinfo_or_arcname=revision_info, data=f"revision_id={revision_id}{env.APP_LAMBDA_FUNCTION_INCREMENT}")
        for subdir, dirnames, files in os.walk(function_dir_path, followlinks=True):
            dirnames.sort()
            files.sort()
            if "__pycache__" not in subdir:
                for file in files:
                    rel_file_path = os.path.relpath(f"{subdir}/{file}", start=function_dir_path)
                    # Set date_time to a constant so anything running it always ends up with the same zip
                    file_info = ZipInfo(filename=rel_file_path, date_time=(1980, 1, 1, 0, 0, 0))
                    with open(f"{subdir}/{file}", "rb") as f:
                        contents = f.read()
                    z.writestr(file_info, contents)

    sha256_hash = sha256()
    read_chunk_size = 65536
    with open(zip_destination, "rb") as f:
        while True:
            chunk = f.read(read_chunk_size)
            if len(chunk):
                sha256_hash.update(chunk)
            else:
                break

    return b64encode(sha256_hash.digest()).decode("utf-8")


def get_lambda_function(stack: Stack, function_name: str, create_version: bool = False) -> Function:
    if not _iam_config:
        globals()["_iam_config"] = get_iam_config()
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()

    function_config = _lambda_config[function_name]

    allow_cross_region_references = (
        function_config.allow_cross_stack_references
        if "allow_cross_stack_references" in function_config.keys()
        else False
    )
    stack_name = stack.stack_name
    function_logical_name = format_logical_name_uppercase(f"{function_config.logical_name}-{stack.region}-{stack_name}")

    functions_in_resources = (
        stack.resources.lambda_functions[function_name]
        if function_name in stack.resources.lambda_functions.keys()
        else []
    )
    functions_in_region = (
        [function for function in functions_in_resources if function.region == stack.region and function.original]
        if functions_in_resources
        else []
    )
    functions_in_stack = (
        [function for function in functions_in_region if function.stack == stack_name] if functions_in_region else []
    )

    if functions_in_stack:
        return functions_in_stack[0]
    if functions_in_region or (functions_in_resources and allow_cross_region_references):
        if functions_in_region:
            original_function_dict = [function for function in functions_in_region if function.original][0]
        else:
            original_function_dict = [function for function in functions_in_resources if function.original][0]
        function_dict = benedict(
            {
                "code_sha256": original_function_dict.code_sha256,
                "stack": stack_name,
                "region": stack.region,
                "original": False,
                "has_post_deployment_custom_resources": original_function_dict.has_post_deployment_custom_resources,
            },
            keyattr_dynamic=True,
        )
        function_dict.function = Function.from_function_arn(
            stack,
            function_logical_name,
            get_resource_attribute(stack, original_function_dict.function.node.id, "function_arn"),
        )

        new_versions = []
        for version in original_function_dict.versions:
            version_arn = get_resource_attribute(
                stack,
                version.node.id,
                "edge_arn",
                begin_resource=version,
                resource_parent_to_find=original_function_dict.function,
            )
            new_versions.append(Version.from_version_arn(function_dict.function, version.node.id, version_arn))

        function_dict.versions = new_versions

        new_aliases = {}
        for alias_name, alias in original_function_dict.aliases.items():
            version = [version for version in new_versions if version.node.id == alias.version.node.id][0]
            new_aliases[alias_name] = Alias.from_alias_attributes(
                function_dict.function, alias.node.id, alias_name=alias_name, alias_version=version
            )

        function_dict.aliases = new_aliases

        stack.resources.lambda_functions[function_name].append(function_dict)
        return function_dict

    else:
        code_path = Path.joinpath(root_dir, Path(function_config.code_directory))
        zip_path = f"{code_path.parent.as_posix()}/{function_name}.zip"
        function_logical_name = function_config.logical_name
        execution_role_name = function_config.configuration.permissions.execution_role

        iam_role = get_iam_role(stack, execution_role_name)

        zip_sha256 = zip_lambda_function(
            function_dir_path=code_path,
            zip_destination=zip_path,
            revision_id=function_config.revision_id,
        )

        if (
            function_config.version.create_version
            and function_config.version.version_options
            and "post_deployment_custom_resources" not in function_config.keys()
        ):
            current_version_config = VersionOptions(
                max_event_age=(
                    Duration.parse(function_config.version.version_options.max_event_age)
                    if function_config.version.version_options.max_event_age
                    else None
                ),
                on_failure=(
                    get_lambda_function(stack, function_config.version.version_options.max_event_age)
                    if function_config.version.version_options.max_event_age
                    else None
                ),
                on_success=(
                    get_lambda_function(stack, function_config.version.version_options.max_event_age)
                    if function_config.version.version_options.max_event_age
                    else None
                ),
                retry_attempts=(
                    function_config.version.version_options.retry_attempts
                    if function_config.version.version_options.retry_attempts
                    else None
                ),
                code_sha256=(
                    function_config.version.version_options.code_sha256
                    if function_config.version.version_options.code_sha256
                    else None
                ),
                description=(
                    function_config.version.version_options.description
                    if function_config.version.version_options.description
                    else None
                ),
                provisioned_concurrent_executions=(
                    function_config.version.version_options.provisioned_concurrent_executions
                    if function_config.version.version_options.provisioned_concurrent_executions
                    else None
                ),
                removal_policy=(
                    getattr(RemovalPolicy, function_config.version.version_options.removal_policy)
                    if function_config.version.version_options.removal_policy
                    else None
                ),
            )
        else:
            current_version_config = None

        function = Lambda.Function(
            stack,
            function_logical_name,
            description=(
                function_config.configuration.general.description
                if function_config.configuration.general.description
                else ""
            ),
            memory_size=function_config.configuration.general.memory,
            ephemeral_storage_size=Size.mebibytes(function_config.configuration.general.ephemeral_storage),
            timeout=Duration.parse(function_config.configuration.general.timeout),
            role=iam_role,
            runtime=getattr(Lambda.Runtime, function_config.runtime_settings.runtime),
            handler=function_config.runtime_settings.handler,
            code=Lambda.Code.from_asset(zip_path),
            current_version_options=current_version_config,
        )

        if function_config.removal_policy:
            function.apply_removal_policy(getattr(RemovalPolicy, function_config.removal_policy))

        function_dict = benedict(
            {
                "function": function,
                "code_sha256": zip_sha256,
                "stack": stack_name,
                "region": stack.region,
                "original": True,
                "versions": [],
                "aliases": {},
                "has_post_deployment_custom_resources": "post_deployment_custom_resources" in function_config.keys(),
            },
            keyattr_dynamic=True,
        )

        if function_config.post_deployment_custom_resources:
            for (
                custom_resource_name,
                custom_resource_config,
            ) in function_config.post_deployment_custom_resources.items():
                if custom_resource_config.resource_type == "Custom::LambdaPlaceholderReplacer":
                    generate_lambda_custom_resource_placeholder_replacer(
                        stack=stack,
                        lambda_function_name=function_name,
                        files_with_placeholders=custom_resource_config.files_with_placeholders,
                        custom_resource_name=custom_resource_name,
                        custom_resource_logical_name=custom_resource_config.logical_name,
                        custom_resource_type=custom_resource_config.resource_type,
                        custom_resource_provider=custom_resource_config.provider,
                        custom_resource_dependencies=(
                            custom_resource_config.depends_on if custom_resource_config.depends_on else []
                        ),
                    )
                else:
                    raise NotImplementedError(
                        f"Custom resources of type {custom_resource_config.resource_type} has not yet been implemented"
                    )
        if function_config.alias.create_alias and function_config.alias.name:
            function_dict.aliases[function_config.alias.name] = function_dict.function.add_alias(
                function_config.alias.name,
                description=(function_config.alias.description if function_config.alias.description else None),
            )

        if (
            create_version
            or function_config.version.create_version
            or (function_config.alias.create_alias and function_config.alias.name)
        ):
            function_dict.versions = [function_dict.function.current_version]

        if function_name not in stack.resources.lambda_functions:
            stack.resources.lambda_functions[function_name] = []
        stack.resources.lambda_functions[function_name].append(function_dict)

        return function_dict


def get_iam_statement_principals(principals: dict) -> List[IPrincipal]:
    ret_principals = []
    for key, value in principals.items():
        if isinstance(value, str):
            value = [value]
        for item in value:
            if key == "Service":
                ret_principals.append(ServicePrincipal(item))
            else:
                ret_principals.append(ArnPrincipal(item))

    return ret_principals


def replace_iam_statements_placeholders(stack: Stack, document: Dict[str, Any]) -> Dict[str, Any]:
    for i in range(0, len(document.Statement)):
        document.Statement[i] = loads(replace_placeholders_in_string(stack, dumps(document.Statement[i])))
    return document


def check_if_string_has_placeholder(source_string: str) -> bool:
    return True if _placeholder_regex.search(source_string) else False


def replace_placeholders_in_string(stack: Stack, source_string: str) -> str:
    placeholders = _placeholder_regex.findall(source_string)
    replacement_string = source_string
    for placeholder in set(placeholders):
        placeholder_value = get_placeholder_value(stack, placeholder)
        replacement_string = replacement_string.replace(placeholder, placeholder_value)
    return replacement_string


def get_placeholder_value(stack: Stack, placeholder_name: str):
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    if placeholder_name in stack.resources.placeholders.keys():
        return stack.resources.placeholders[placeholder_name].value

    if placeholder_name not in _cdk_config.cfn_variable_replacements.keys():
        raise ValueError(f"The placeholder {placeholder_name} could not be found in the placeholders dictionary")

    placeholder_config = _cdk_config.cfn_variable_replacements[placeholder_name]
    placeholder_environment_config = placeholder_config.environments[
        "ALL" if "ALL" in placeholder_config.environments.keys() else env.APP_DEPLOY_ENV
    ]

    prefixes = []
    suffixes = []
    for prefix in placeholder_environment_config.prefixes:
        prefixes.append(get_placeholder_source_value(stack, prefix, placeholder_name))
    for suffix in placeholder_environment_config.suffixes:
        suffixes.append(get_placeholder_source_value(stack, suffix, placeholder_name))

    placeholder_value = get_placeholder_source_value(stack, placeholder_environment_config, placeholder_name)

    if prefixes:
        placeholder_value = "".join(prefixes) + placeholder_value
    if suffixes:
        placeholder_value = placeholder_value + "".join(suffixes)

    stack.resources.placeholders[placeholder_name] = {
        "value": placeholder_value,
        "export": placeholder_config.export,
        "stack_name": stack.stack_name,
    }

    return placeholder_value


def get_placeholder_source_value(stack: Stack, source_config: benedict, placeholder_name: str) -> Any:
    error_message_partial = f"Error getting placeholder source value for {placeholder_name}: "
    if "source" not in source_config.keys():
        raise RuntimeError(f'{error_message_partial}the required key "source" is missing')

    error_message_missing_item_partial = (
        f"{error_message_partial}the source type of {source_config.source} requires the following key-value pairs"
    )
    if source_config.source == "cdk_cfn_variable_replacements":
        if "variable_name" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}variable_name: str")
        ret = get_placeholder_value(stack, source_config.variable_name)
    elif source_config.source == "cdk_static_variables":
        if "variable_name" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}variable_name: str")
        ret = _cdk_config.static_variables[source_config.variable_name]
    elif source_config.source == "env":
        if "variable_name" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}variable_name: str")
        ret = env[source_config.variable_name]
    elif source_config.source == "provided":
        if "value" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}value: str")
        ret = source_config.value
    elif source_config.source == "resource_attribute":
        if "resource_logical_name" not in source_config.keys() or "attribute" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}resource_logical_name: str, attribute: str")
        ret = get_resource_attribute(stack, source_config.resource_logical_name, source_config.attribute)
    elif source_config.source == "config_file":

        def _traverse_config_file(obj: Any, children: list):
            if len(children) == 0:
                raise RuntimeError(f"{error_message_partial}The path must not be empty")

            error_message_path_partial = f"the path /{'/'.join(source_config.path)} is invalid: "
            if obj is None:
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} parent is null"
                )
            if isinstance(obj, dict) and not isinstance(children[0], str):
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} "
                    f"is of type {type(children[0])}, but its parent is a dictionary"
                )
            elif isinstance(obj, dict) and children[0] not in obj.keys():
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} parent is a dictionary, "
                    f"but does not contain the corresponding key"
                )
            if isinstance(obj, list) and not isinstance(children[0], int):
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} "
                    f"is of type {type(children[0])}, but its parent is a list"
                )
            elif isinstance(obj, list) and children[0] >= len(obj):
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} "
                    f"would reference an index out of bounds object in its parent list"
                )
            if not isinstance(obj, dict) and not isinstance(obj, list):
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}item {children[0]} has a parent of type "
                    f"{type(obj)}, but expected dict or list"
                )

            children.reverse()
            child = children.pop()
            sub_obj = obj[child]
            children.reverse()

            if children:
                return _traverse_config_file(sub_obj, children)
            elif (
                not isinstance(sub_obj, str)
                and not isinstance(sub_obj, int)
                and not isinstance(sub_obj, float)
                and not isinstance(sub_obj, bool)
            ):
                raise RuntimeError(
                    f"{error_message_partial}{error_message_path_partial}the object at {child} is of {type(obj)}"
                    ", but expected str or list"
                )

            return sub_obj

        if "file" not in source_config.keys() or "path" not in source_config.keys():
            raise RuntimeError(f"{error_message_missing_item_partial}file: str, path: array[str/int]")
        if source_config.file not in required_config_json_files:
            raise RuntimeError(f"{error_message_partial}config file of {source_config.file} is not valid")
        config = _get_config_file_object(source_config.file)
        ret = _traverse_config_file(config, source_config.path.copy())
    else:
        raise RuntimeError(f"{error_message_partial}source of {source_config.source}")

    return ret


def get_resource_attribute(
    stack: Stack,
    resource_logical_name: str,
    attribute: str,
    *,
    begin_resource: Resource = None,
    resource_parent_to_find: Any = None,
) -> str:
    resource = get_resource_by_logical_name(
        stack=stack,
        resource_logical_name=resource_logical_name,
        begin_resource=begin_resource,
        resource_parent_to_find=resource_parent_to_find,
    )
    return getattr(resource, attribute)


def get_resource_by_logical_name(
    stack: Stack,
    resource_logical_name,
    *,
    begin_resource: Resource = None,
    resource_parent_to_find: Any = None,
    resource_expected_region: str = None,
):
    if begin_resource == None:
        begin_resource = stack

    parent = begin_resource
    while (
        type(parent) is not App
        if resource_parent_to_find == None
        else type(parent) is not type(resource_parent_to_find)
    ):
        if parent == None:
            raise ValueError(f"Unable to find parent for resource {begin_resource.node.id}")
        parent = parent.node.scope

    possible_resources = [
        child
        for child in parent.node.find_all()
        if child.node.id == resource_logical_name
        and (
            resource_expected_region is None
            or (
                not resource_expected_region is None
                and getattr(child, "stack", benedict({}, keyattr_dynamic=True)).region == resource_expected_region
            )
        )
    ]
    if not possible_resources:
        logical_name = format_logical_name_uppercase(f"{resource_logical_name}-{stack.region}")
        possible_resources = [child for child in parent.node.find_all() if child.node.id == logical_name]
        if not possible_resources:
            logical_name = format_logical_name_uppercase(f"{resource_logical_name}-{stack.region}-{stack.stack_name}")
            possible_resources = [child for child in parent.node.find_all() if child.node.id == logical_name]
            if not possible_resources:
                raise RuntimeError(f"Resource with logical id of {resource_logical_name} could not be found")
    if len(possible_resources) > 1:
        message = f"Multiple resources exist across stacks for logical id {resource_logical_name}\n\n"
        resource_paths = "\n".join([f"{resource.node.id} - {resource.node.path}" for resource in possible_resources])
        message += f"Resource paths: {resource_paths}"

        raise RuntimeError(message)

    return possible_resources[0]


def get_sns_topic(stack: Stack, sns_topic_name) -> None:
    if not _sns_config:
        globals()["_sns_config"] = get_sns_config()
    topic_config = _sns_config.topics[sns_topic_name]
    stack_name = stack.stack_name
    topic_logical_name = format_logical_name_uppercase(f"{topic_config.logical_name}-{stack.region}-{stack_name}")
    topics_in_resources = (
        stack.resources.sns.topics[sns_topic_name] if sns_topic_name in stack.resources.sns.topics.keys() else []
    )
    topics_in_region = (
        [topic for topic in topics_in_resources if topic.region == stack.region and topic.original]
        if topics_in_resources
        else []
    )
    topics_in_stack = [topic for topic in topics_in_region if topic.stack == stack_name] if topics_in_region else []

    if topics_in_stack:
        topic_dict = topics_in_stack[0]
    elif topics_in_region:
        original_topic_dict = [topic for topic in topics_in_region if topic.original][0]
        topic_dict = benedict(
            {
                "stack": stack_name,
                "region": stack.region,
                "original": False,
            },
            keyattr_dynamic=True,
        )
        topic_dict.topic = Topic.from_topic_arn(
            stack,
            topic_logical_name,
            get_resource_attribute(stack, original_topic_dict.topic.node.id, "topic_arn"),
        )
        stack.resources.sns.topics.append(topic_dict)
    else:
        topic = Topic(
            stack,
            topic_logical_name,
            display_name=topic_config.display_name,
            enforce_ssl=(topic_config.enforce_ssl if topic_config.enforce_ssl else True),
        )

        topic_dict = benedict(
            {
                "topic": topic,
                "stack": stack_name,
                "region": stack.region,
                "original": True,
            },
            keyattr_dynamic=True,
        )

        for subscription, subscription_config in topic_config.subscriptions.items():
            if subscription_config.protocol == "EMAIL":
                endpoints = replace_placeholders_in_string(stack, subscription_config.endpoint).split(",")
                for endpoint in endpoints:
                    replacement_regex = re.compile("[^a-zA-Z0-9_-]")
                    logical_endpoint_suffix = replacement_regex.sub("", endpoint)
                    stack.resources.sns.subscriptions[subscription] = Subscription(
                        stack,
                        f"{subscription_config.logical_name}{logical_endpoint_suffix}",
                        protocol=SubscriptionProtocol.EMAIL,
                        endpoint=endpoint,
                        topic=topic,
                    )
            else:
                raise NotImplementedError(
                    f"Subscriptions with protocols of {subscription_config.protocol} have not yet been implemented"
                )

        if topic_config.resource_policy:
            if not _iam_config:
                globals()["_iam_config"] = get_iam_config()

            policy = _iam_config.resource_based_policies.sns_policies[
                topic_config.resource_policy.policy_name
            ].policy.deepcopy()
            policy_document_dict = replace_iam_statements_placeholders(stack, policy)

            policy_document = PolicyDocument.from_json(policy_document_dict)

            topic_policy = TopicPolicy(
                stack,
                topic_config.resource_policy.logical_name,
                topics=[topic],
                policy_document=policy_document,
            )
            topic_dict["topic_policy"] = topic_policy

        if sns_topic_name not in stack.resources.sns.topics:
            stack.resources.sns.topics[sns_topic_name] = []
        stack.resources.sns.topics[sns_topic_name].append(topic_dict)

    return topic_dict


def get_ssl_certificate(stack: Stack, certificate_name: str) -> Certificate:
    certificate_configs = get_certificates_config()

    certificate_config = certificate_configs[certificate_name]

    certificate_logical_name = certificate_config.logical_name + stack.stack_name

    if (
        stack.stack_name in stack.resources.certificates.keys()
        and certificate_name in stack.resources.certificates[stack.stack_name].keys()
    ):
        certificate_arn = stack.resources.certificates[stack.stack_name][certificate_name]
    else:
        certificate_domain = replace_placeholders_in_string(stack, certificate_config.certificate_domain)
        subject_alternative_names = [
            replace_placeholders_in_string(stack, name) for name in certificate_config.subject_alternative_names
        ]
        certificate_region = replace_placeholders_in_string(stack, certificate_config.certificate_region)
        retain_certificate_on_in_use_failure = certificate_config.retain_certificate_on_in_use_failure
        dns_zone_domain = replace_placeholders_in_string(stack, certificate_config.dns_zone_domain)
        dns_zone_role_arn = (
            replace_placeholders_in_string(stack, certificate_config.dns_zone_role_arn)
            if "dns_zone_role_arn" in certificate_config.keys() and env.APP_DEPLOY_ACCOUNT != env.APP_DNS_ZONE_ACCOUNT
            else None
        )
        route53_hosted_zone_id = replace_placeholders_in_string(stack, certificate_config.route53_hosted_zone_id)
        certificate_record_ttl = certificate_config.record_ttl if "record_ttl" in certificate_config.keys() else "60"
        delete_dns_records_with_certificate = "false" if env.APP_DEPLOY_ENV == "PROD" else "true"

        for custom_resource_name, custom_resource_config in certificate_config.custom_resources.items():
            if custom_resource_config.resource_type == "Custom::ACMCertificateCreatorValidator":
                custom_resource = _create_lambda_custom_resource_create_and_validate_acm_certificates(
                    stack=stack,
                    custom_resource_name=custom_resource_name,
                    custom_resource_logical_name=custom_resource_config.logical_name,
                    custom_resource_type=custom_resource_config.resource_type,
                    custom_resource_provider=custom_resource_config.provider,
                    custom_resource_dependencies=(
                        custom_resource_config.depends_on if "depends_on" in custom_resource_config.keys() else []
                    ),
                    acm_domain_name=certificate_domain,
                    acm_subject_alternative_names=subject_alternative_names,
                    acm_region=certificate_region,
                    retain_certificate_on_in_use_failure=retain_certificate_on_in_use_failure,
                    dns_zone_domain=dns_zone_domain,
                    dns_zone_role_arn=dns_zone_role_arn,
                    route53_hosted_zone_id=route53_hosted_zone_id,
                    record_ttl=certificate_record_ttl,
                    delete_dns_records_with_certificate=delete_dns_records_with_certificate,
                )
            else:
                raise NotImplementedError(
                    f"Custom resources of type {custom_resource_config.resource_type} have not yet been implemented"
                )

            certificate_arn = custom_resource.get_att_string("certificate_arn")
            if stack.stack_name not in stack.resources.certificates.keys():
                stack.resources.certificates[stack.stack_name] = {}
            stack.resources.certificates[stack.stack_name][certificate_name] = certificate_arn

    return Certificate.from_certificate_arn(stack, certificate_logical_name, certificate_arn)


def create_dns_records(stack: Stack, record_name: str) -> None:
    dns_config = get_dns_config()
    record_set = dns_config.record_sets[record_name]

    dns_zone_domain = replace_placeholders_in_string(stack, record_set.dns_zone_domain)
    dns_zone_role_arn = (
        replace_placeholders_in_string(stack, record_set.dns_zone_role_arn)
        if env.APP_DEPLOY_ACCOUNT != env.APP_DNS_ZONE_ACCOUNT
        else None
    )
    route53_hosted_zone_id = replace_placeholders_in_string(stack, record_set.route53_hosted_zone_id)
    records = []
    for record_config in record_set.records:
        record = {
            "name": replace_placeholders_in_string(stack, record_config.name),
            "type": record_config.type,
        }
        if "alias_target" in record_config.keys() and "resource_records" in record_config.keys():
            raise RuntimeError("DNS Records may not have both alias_target and resource_records configurations")
        if "alias_target" in record_config.keys():
            alias_target = {
                "hosted_zone_id": get_placeholder_source_value(
                    stack, record_config["alias_target"]["hosted_zone_id"], record_name
                ),
                "dns_name": get_placeholder_source_value(stack, record_config["alias_target"]["dns_name"], record_name),
            }
            record["alias_target"] = alias_target
        elif "resource_records" in record_config.keys():
            resource_records = []
            for resource_record in record_config["resource_records"]:
                resource_records.append({"value": replace_placeholders_in_string(stack, resource_record)})
            record["resource_records"] = resource_records
        records.append(record)

    for custom_resource_name, custom_resource_config in record_set.custom_resources.items():
        if custom_resource_config.resource_type == "Custom::CreateDNSRecords":
            _ = create_lambda_custom_resource_create_dns_records(
                stack=stack,
                custom_resource_name=custom_resource_name,
                custom_resource_logical_name=custom_resource_config.logical_name,
                custom_resource_type=custom_resource_config.resource_type,
                custom_resource_provider=custom_resource_config.provider,
                custom_resource_dependencies=(
                    custom_resource_config.depends_on if "depends_on" in custom_resource_config.keys() else []
                ),
                dns_records=records,
                dns_zone_domain=dns_zone_domain,
                dns_zone_role_arn=dns_zone_role_arn,
                route53_hosted_zone_id=route53_hosted_zone_id,
                record_ttl=(record_set.record_ttl if "record_ttl" in record_set.keys() else "60"),
                removal_policy=("retain" if env.APP_DEPLOY_ENV == "PROD" else "destroy"),
            )
        else:
            raise NotImplementedError(
                f"Custom resources of type {custom_resource_config.resource_type} have not yet been implemented"
            )


def _get_custom_resource_provider(stack: Stack, provider_name: str) -> Provider:
    if stack.region not in stack.resources.custom_resources.providers:
        stack.resources.custom_resources.providers[stack.region] = {}
    if provider_name not in stack.resources.custom_resources.providers[stack.region].keys():
        stack.resources.custom_resources.providers[stack.region][provider_name] = _create_resource_provider(
            stack, provider_name
        )
    return stack.resources.custom_resources.providers[stack.region][provider_name]


def _create_resource_provider(stack: Stack, provider_name: str) -> Provider:
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    provider_config = _cdk_config.custom_resources.providers[provider_name]
    on_event_handler_name = provider_config.on_event_handler
    is_complete_handler_name = provider_config.is_complete_handler if provider_config.is_complete_handler else None
    on_event_handler = get_lambda_function(stack, on_event_handler_name).function
    is_complete_handler = (
        get_lambda_function(stack, is_complete_handler_name).function if is_complete_handler_name else None
    )

    role = get_iam_role(stack, provider_config.role) if provider_config.role else None

    provider = Provider(
        stack,
        provider_config.logical_name + format_logical_name_uppercase(stack.region),
        on_event_handler=on_event_handler,
        is_complete_handler=is_complete_handler,
        disable_waiter_state_machine_logging=(
            provider_config.disable_waiter_state_machine_logging
            if provider_config.disable_waiter_state_machine_logging
            else None
        ),
        query_interval=(Duration.parse(provider_config.query_interval) if provider_config.query_interval else None),
        role=role,
        total_timeout=(Duration.parse(provider_config.total_timeout) if provider_config.total_timeout else None),
    )
    return provider.service_token


def is_duration_string(duration) -> bool:
    # Taken from https://rgxdb.com/r/MD2234J
    duration_regex = re.compile(
        r"^(-?)P(?=\d|T\d)(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)([DW]))?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
    )
    return bool(duration_regex.match(duration))


def generate_lambda_custom_resource_api_gateway_integration_updater(
    stack: Stack,
    rest_api_name: str,
    resource_id: str,
    method: str,
    lambda_function_name: str,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    lambda_function_version: str = None,
    lambda_function_alias: str = None,
    custom_resource_dependencies: List[str] = [],
) -> None:
    if lambda_function_version and lambda_function_alias:
        raise ValueError(
            "Cannot provide custom resource API Gateway integration updater with both a function version and alias"
        )
    updates = {
        "resource_id": resource_id,
        "method": method,
        "lambda_arn": get_resource_attribute(stack, _lambda_config[lambda_function_name].logical_name, "function_arn"),
    }
    if lambda_function_version:
        updates["lambda_version"] = lambda_function_version
    if lambda_function_alias:
        updates["lambda_alias"] = lambda_function_alias

    if rest_api_name not in stack.resources.custom_resources.to_resolve[stack.region].keys():
        stack.resources.custom_resources.to_resolve[stack.region][rest_api_name] = {
            "function": _create_lambda_custom_resource_api_gateway_integration_updater,
            "props": {
                "rest_api_name": rest_api_name,
                "updates": [updates],
                "custom_resource_name": custom_resource_name,
                "custom_resource_logical_name": custom_resource_logical_name,
                "custom_resource_type": custom_resource_type,
                "custom_resource_provider": custom_resource_provider,
                "custom_resource_dependencies": custom_resource_dependencies,
            },
        }
    else:
        stack.resources.custom_resources.to_resolve[stack.region][rest_api_name].props.updates.append(updates)


def _create_lambda_custom_resource_api_gateway_integration_updater(
    stack: Stack,
    rest_api_name: str,
    updates: List[Dict[str, Any]],
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _api_config:
        globals()["_api_config"] = get_api_config()
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    api_config = _api_config[rest_api_name]

    rest_api_id = get_resource_attribute(stack, _api_config[rest_api_name].logical_name, "rest_api_id")
    rest_api_region = get_deploy_region()
    deployment_stages = [key for key in api_config.configuration.stages.keys()]

    resource_properties = {
        "rest_api_id": rest_api_id,
        "rest_api_region": rest_api_region,
        "deployment_stages": deployment_stages,
        "updates": updates,
    }

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    provider_config = _cdk_config.custom_resources.providers[custom_resource_provider]

    for update in updates:
        lambda_arn = update.lambda_arn
        resource_id = update.resource_id
        method = update.method
        for policy in provider_config.resources_iam_policies:
            if policy.service == "lambda":
                _add_resources_to_managed_policy(
                    stack,
                    policy.name,
                    policy.statement_number,
                    [lambda_arn],
                )
            if policy.service == "apigateway":
                resources = [
                    f"arn:aws:apigateway:{rest_api_region}::/restapis/{rest_api_id}/resources/{resource_id}/methods/{method}/integration",
                    f"arn:aws:apigateway:{rest_api_region}::/restapis/{rest_api_id}/deployments",
                ]
                for stage in deployment_stages:
                    resources.append(
                        f"arn:aws:apigateway:{rest_api_region}::/restapis/{rest_api_id}/deployments/{stage}",
                    )
                _add_resources_to_managed_policy(
                    stack,
                    policy.name,
                    policy.statement_number,
                    resources,
                )

    return custom_resource


def generate_lambda_custom_resource_placeholder_replacer(
    stack: Stack,
    lambda_function_name: str,
    files_with_placeholders: List[str],
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> None:
    stack.resources.custom_resources.to_resolve[stack.region][custom_resource_name] = {
        "function": _create_lambda_custom_resource_placeholder_replacer,
        "props": {
            "lambda_function_name": lambda_function_name,
            "files_with_placeholders": files_with_placeholders,
            "custom_resource_name": custom_resource_name,
            "custom_resource_logical_name": custom_resource_logical_name,
            "custom_resource_type": custom_resource_type,
            "custom_resource_provider": custom_resource_provider,
            "custom_resource_dependencies": custom_resource_dependencies,
        },
    }


def _create_lambda_custom_resource_placeholder_replacer(
    stack: Stack,
    lambda_function_name: str,
    files_with_placeholders: List[str],
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    lambda_function_config = _lambda_config[lambda_function_name]
    function_code_replacements = {}
    for rel_file_path in files_with_placeholders:
        file_path = f"{root_dir}/{lambda_function_config.code_directory}/{rel_file_path}"
        with open(file_path, "r") as f:
            placeholders = _placeholder_regex.findall(f.read())
        for placeholder in placeholders:
            function_code_replacements[placeholder] = get_placeholder_value(stack, placeholder)

    lambda_function = get_lambda_function(stack, lambda_function_name)
    function_arn = lambda_function.function.function_arn

    create_alias = (
        lambda_function_config.alias.create_alias if "create_alias" in lambda_function_config.alias.keys() else False
    )
    alias_name = ""
    if create_alias:
        alias_name = (
            lambda_function_config.alias.name
            if "name" in lambda_function_config.alias.keys()
            else lambda_function.code_sha256
        )

    resource_properties = {
        "function_arn": function_arn,
        "function_region": stack.region,
        "function_code_files": files_with_placeholders,
        "replacement_values": function_code_replacements,
        "create_new_version": (
            lambda_function_config.version.create_version
            if "create_version" in lambda_function_config.version.keys()
            else False
        ),
        "create_alias": create_alias,
        "alias_name": alias_name,
        "code_sha256": lambda_function.code_sha256,
    }

    if lambda_function_config.version.description:
        resource_properties["version_description"] = lambda_function_config.version.description

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    provider_config = _cdk_config.custom_resources.providers[custom_resource_provider]

    for policy in provider_config.resources_iam_policies:
        if policy.service == "lambda":
            _add_resources_to_managed_policy(
                stack,
                policy.name,
                policy.statement_number,
                [function_arn],
            )

    return custom_resource


def _create_lambda_custom_resource_create_and_validate_acm_certificates(
    stack: Stack,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: str,
    acm_domain_name: str,
    acm_subject_alternative_names: List[str],
    acm_region: str,
    retain_certificate_on_in_use_failure: bool,
    dns_zone_domain: str,
    route53_hosted_zone_id: str,
    dns_zone_role_arn: str = None,
    record_ttl: str = "60",
    delete_dns_records_with_certificate: str = "false",
) -> CustomResource:
    resource_properties = {
        "acm_domain_name": acm_domain_name,
        "acm_subject_alternative_names": acm_subject_alternative_names,
        "acm_region": acm_region,
        "retain_certificate_on_in_use_failure": "true" if retain_certificate_on_in_use_failure else "false",
        "dns_zone_domain": dns_zone_domain,
        "route53_hosted_zone_id": route53_hosted_zone_id,
        "record_ttl": record_ttl,
        "delete_dns_records_with_certificate": delete_dns_records_with_certificate,
    }
    if dns_zone_role_arn:
        resource_properties["dns_zone_role_arn"] = dns_zone_role_arn

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    return custom_resource


def create_lambda_custom_resource_create_dns_records(
    stack: Stack,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: str,
    dns_records: List[Dict[str, Any]],
    dns_zone_domain: str,
    route53_hosted_zone_id: str,
    dns_zone_role_arn: str = None,
    record_ttl: str = "60",
    removal_policy: str = "destroy",
) -> CustomResource:
    resource_properties = {
        "dns_records": dns_records,
        "dns_zone_domain": dns_zone_domain,
        "route53_hosted_zone_id": route53_hosted_zone_id,
        "record_ttl": record_ttl,
        "removal_policy": removal_policy,
    }
    if dns_zone_role_arn:
        resource_properties["dns_zone_role_arn"] = dns_zone_role_arn

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    return custom_resource


def generate_lambda_custom_resource_cloudfront_function_redirect_placeholders(
    stack: Stack,
    cloudfront_function_logical_name: str,
    cloudfront_function_name: str,
    function_stage: str,
    domain_name: str,
    domain_uri: str,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> None:
    if function_stage not in ["DEVELOPMENT", "LIVE"]:
        raise RuntimeError('Function stage must be either "DEVELOPMENT" or "LIVE"')
    stack.resources.custom_resources.to_resolve[stack.region][custom_resource_name] = {
        "function": _create_lambda_custom_resource_cloudfront_function_redirect_placeholders,
        "props": {
            "cloudfront_function_name": cloudfront_function_name,
            "cloudfront_function_logical_name": cloudfront_function_logical_name,
            "function_stage": function_stage,
            "domain_name": domain_name,
            "domain_uri": domain_uri,
            "custom_resource_name": custom_resource_name,
            "custom_resource_logical_name": custom_resource_logical_name,
            "custom_resource_type": custom_resource_type,
            "custom_resource_provider": custom_resource_provider,
            "custom_resource_dependencies": custom_resource_dependencies,
        },
    }


def _create_lambda_custom_resource_cloudfront_function_redirect_placeholders(
    stack: Stack,
    cloudfront_function_name: str,
    cloudfront_function_logical_name: str,
    function_stage: str,
    domain_name: str,
    domain_uri: str,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    resource_properties = {
        "cloudfront_function_name": cloudfront_function_name,
        "function_stage": function_stage,
        "domain_name": replace_placeholders_in_string(stack, domain_name),
        "domain_uri": replace_placeholders_in_string(stack, domain_uri),
    }

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    function_arn = get_resource_attribute(stack, cloudfront_function_logical_name, "function_arn")

    provider_config = _cdk_config.custom_resources.providers[custom_resource_provider]

    for policy in provider_config.resources_iam_policies:
        if policy.service == "lambda":
            _add_resources_to_managed_policy(
                stack,
                policy.name,
                policy.statement_number,
                [function_arn],
            )

    return custom_resource


def generate_lambda_custom_resource_cloudfront_behavior_edge_lambda(
    stack: Stack,
    distribution_name: str,
    distribution_id: str,
    path_pattern: str,
    function_name: str,
    function_version: str,
    event_type: str,
    include_body: bool,
    is_default_cache_behavior: bool,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> None:
    lambda_function = get_lambda_function(stack, function_name)

    updates = benedict(
        {
            "path_pattern": path_pattern,
            "function_name": function_name,
            "function_version": function_version,
            "event_type": event_type,
            "include_body": include_body,
            "is_default_cache_behavior": is_default_cache_behavior,
            "function_code_sha256": lambda_function.code_sha256,
        },
        keyattr_dynamic=True,
    )

    if distribution_name not in stack.resources.custom_resources.to_resolve[stack.region].keys():
        stack.resources.custom_resources.to_resolve[stack.region][distribution_name] = {
            "function": _create_lambda_custom_resource_cloudfront_behavior_edge_lambda,
            "props": {
                "distribution_id": distribution_id,
                "updates": [updates],
                "custom_resource_name": custom_resource_name,
                "custom_resource_logical_name": custom_resource_logical_name,
                "custom_resource_type": custom_resource_type,
                "custom_resource_provider": custom_resource_provider,
                "custom_resource_dependencies": custom_resource_dependencies,
            },
        }
    else:
        stack.resources.custom_resources.to_resolve[stack.region][distribution_name].props.updates.append(updates)


def _create_lambda_custom_resource_cloudfront_behavior_edge_lambda(
    stack: Stack,
    distribution_id: str,
    updates: List[Dict[str, Any]],
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    resource_properties_updates = []

    for update in updates:
        resource_properties_update = {
            "function_arn": get_resource_attribute(
                stack, _lambda_config[update.function_name].logical_name, "function_arn"
            ),
            "function_version": update.function_version,
            "event_type": update.event_type,
            "include_body": update.include_body,
            "is_default_cache_behavior": update.is_default_cache_behavior,
            "function_code_sha256": update.function_code_sha256,
        }
        if update.path_pattern:
            resource_properties_update["path_pattern"] = update.path_pattern
        resource_properties_updates.append(resource_properties_update)

    resource_properties = {"distribution_id": distribution_id, "updates": resource_properties_updates}

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    provider_config = _cdk_config.custom_resources.providers[custom_resource_provider]

    for policy in provider_config.resources_iam_policies:
        for update in resource_properties_updates:
            if policy.service == "lambda":
                _add_resources_to_managed_policy(
                    stack,
                    policy.name,
                    policy.statement_number,
                    [update["function_arn"], f"{update['function_arn']}:*"],
                )

    return custom_resource


def create_lambda_custom_resource_empty_bucket(
    stack: Stack,
    bucket_name: str,
    bucket_region: str,
    bucket_arn: str,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _cdk_config:
        globals()["_cdk_config"] = get_cdk_config()

    resource_properties = {"bucket_name": bucket_name, "bucket_region": bucket_region}

    custom_resource = _create_lambda_custom_resource(
        stack,
        custom_resource_name,
        custom_resource_logical_name,
        custom_resource_type,
        custom_resource_provider,
        resource_properties,
        custom_resource_dependencies,
    )

    provider_config = _cdk_config.custom_resources.providers[custom_resource_provider]

    for policy in provider_config.resources_iam_policies:
        if policy.service == "lambda":
            _add_resources_to_managed_policy(
                stack,
                policy.name,
                policy.statement_number,
                [bucket_arn, f"{bucket_arn}/*"],
            )

    return custom_resource


def _create_lambda_custom_resource(
    stack: Stack,
    custom_resource_name: str,
    custom_resource_logical_name: str,
    custom_resource_type: str,
    custom_resource_provider: str,
    custom_resource_properties: Dict[str, Any],
    custom_resource_dependencies: List[str] = [],
) -> CustomResource:
    if not _lambda_config:
        globals()["_lambda_config"] = get_lambda_config()
    if not _iam_config:
        globals()["_iam_config"] = get_iam_config()

    provider = _get_custom_resource_provider(stack, custom_resource_provider)

    custom_resource = CustomResource(
        stack,
        custom_resource_logical_name,
        properties=custom_resource_properties,
        service_token=provider,
    )

    for dependency in custom_resource_dependencies:
        try:
            resource = get_resource_by_logical_name(stack, dependency, resource_parent_to_find=stack)
        except RuntimeError as e:
            if not dependency.startswith("CustomResource") and e.args[0].startswith("Resource with logical id"):
                if len(stack.dependencies) == 1:
                    next_stack = stack.dependencies[0]
                else:
                    raise
                while True:
                    try:
                        resource = get_resource_by_logical_name(next_stack, dependency)
                        break
                    except RuntimeError:
                        if len(next_stack.dependencies) == 1:
                            next_stack = next_stack.dependencies[0]
                        else:
                            raise

            else:
                dependee_custom_resource = {}
                if e.args[0].startswith("Resource with logical id"):
                    for name, config in stack.resources.custom_resources.to_resolve[stack.region].items():
                        if config.props.custom_resource_logical_name == dependency:
                            dependee_custom_resource = config
                            stack.resources.custom_resources.to_resolve[stack.region].pop(name)
                            break

                if not dependee_custom_resource:
                    raise e
                else:
                    dependee_custom_resource.function(stack, **dependee_custom_resource.props)
                    resource = get_resource_by_logical_name(stack, dependency, resource_parent_to_find=stack)
        custom_resource.node.add_dependency(resource)

    return custom_resource


def create_logical_name_from_dns_name(dns_name: str, record_type: ResourceType) -> None:
    logical_name_unique_id = "".join([word.capitalize() for word in dns_name.split(".")])
    return record_type.value + logical_name_unique_id


def export_cdk_variables(stack: Stack) -> None:
    for placeholder, placeholder_config in _cdk_config.cfn_variable_replacements.items():
        if placeholder_config.export and placeholder not in stack.resources.placeholders.keys():
            _ = get_placeholder_value(stack, placeholder)
    for placeholder_name, placeholder_config in stack.resources.placeholders.items():
        if placeholder_config.export:
            if isinstance(placeholder_config.value, list) or isinstance(placeholder_config.value, dict):
                export_value = dumps(placeholder_config.value)
            else:
                export_value = placeholder_config.value
            stack.export_value(
                export_value,
                name=format_export_name(f"{placeholder_name}-{env.APP_STACK_PREFIX}"),
                description=placeholder_name,
            )


def format_export_name(export_name) -> str:
    allowed_chars = "abcdefghijklmnopqrstuvwxyz0123456789-:"
    subs = {"_": "-"}
    ret_name = ""
    for char in export_name:
        if char.upper() in allowed_chars.upper():
            ret_name += char
        elif char in subs.keys():
            ret_name += subs[char]

    return ret_name


def format_logical_name_uppercase(name: str) -> str:
    return "".join([word.capitalize() for word in format_export_name(name).split("-")])
