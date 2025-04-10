import os
import pathlib

from aws_cdk import Duration, Fn, RemovalPolicy, Stack
from aws_cdk.aws_apigateway import (
    AuthorizationType,
    CfnAuthorizer,
    ContentHandling,
    Deployment,
    DomainName,
    EndpointConfiguration,
    EndpointType,
    GatewayResponse,
    IAuthorizer,
    IdentitySource,
    IntegrationResponse,
    JsonSchema,
    JsonSchemaType,
    JsonSchemaVersion,
    LambdaIntegration,
    MethodLoggingLevel,
    MethodResponse,
    MockIntegration,
    Model,
    PassthroughBehavior,
    RequestValidator,
    Resource,
    ResponseType,
    RestApi,
    Stage,
    TokenAuthorizer,
)
from aws_cdk.aws_certificatemanager import Certificate
from aws_cdk.aws_iam import Role
from aws_cdk.aws_lambda import Function
from benedict import benedict
from enum import Enum
from hashlib import sha256
from json import dumps
from typing import Any, List, Dict

from src.backend.configuration.common import (
    create_dns_records,
    format_export_name,
    generate_lambda_custom_resource_api_gateway_integration_updater,
    get_deploy_region,
    get_lambda_function,
    get_api_config,
    get_cdk_config,
    get_lambda_config,
    get_iam_role,
    get_resource_by_logical_name,
    get_ssl_certificate,
    replace_placeholders_in_string,
)


class CreateApi:
    class Method(Enum):
        DELETE = "DELETE"
        GET = "GET"
        HEAD = "HEAD"
        OPTIONS = "OPTIONS"
        POST = "POST"
        PUT = "PUT"
        PATCH = "PATCH"

    def __init__(self, stack: Stack, deploy_type: str) -> benedict:
        self.cdk_config = get_cdk_config()
        self.deploy_type = deploy_type
        self.stack = stack
        self.api_config = get_api_config()
        self.lambda_config = get_lambda_config()

    def create_apis(self) -> None:
        for api_name, api_config in self.api_config.items():
            endpoint_types = [EndpointType(api_config.configuration.endpoint_type)]
            endpoint_configuration = EndpointConfiguration(types=endpoint_types)

            rest_api = RestApi(
                self.stack,
                api_config.logical_name,
                endpoint_configuration=endpoint_configuration,
                default_cors_preflight_options=None,
                default_method_options=None,
                cloud_watch_role=True,
                cloud_watch_role_removal_policy=(
                    RemovalPolicy.RETAIN
                    if api_config.configuration.retain_default_cloudwatch_role
                    else RemovalPolicy.DESTROY
                ),
                deploy=False,
                description=api_config.configuration.description,
                endpoint_export_name=format_export_name(api_config.configuration.endpoint_export_name),
            )

            self.stack.resources.api[api_name] = {}

            for gateway_response_name in api_config.gateway_responses.keys():
                _ = self._get_api_gateway_response(rest_api=rest_api, gateway_response_name=gateway_response_name)

            for resource_name, resource_config in api_config.resources.items():
                self._create_api_resource(
                    rest_api=rest_api,
                    resource_name=resource_name,
                    resource_config=resource_config,
                    parent=rest_api.root,
                )

            self.stack.resources.api[api_name].rest_api = rest_api

            if api_config.configuration.stages:
                for stage_name, stage_config in api_config.configuration.stages.items():
                    api_config_hash = sha256(dumps(api_config).encode("utf-8"))
                    deployment_revision = api_config_hash.hexdigest()
                    deployment = Deployment(
                        self.stack,
                        f"{stage_config.deployment.logical_name}{deployment_revision}",
                        api=rest_api,
                        retain_deployments=(
                            stage_config.deployment.retain_deployments
                            if stage_config.deployment.retain_deployments
                            else False
                        ),
                    )

                    stage = Stage(
                        self.stack,
                        stage_config.logical_name,
                        deployment=deployment,
                        stage_name=stage_name,
                        cache_data_encrypted=(
                            stage_config.cache_data_encrypted if "cache_data_encrypted" in stage_config.keys() else None
                        ),
                        cache_ttl=(
                            stage_config.cache_ttl
                            if "caching_enabled" in stage_config.keys()
                            and stage_config.caching_enabled is True
                            and "stage_config" in stage_config.keys()
                            else None
                        ),
                        caching_enabled=(
                            stage_config.caching_enabled if "caching_enabled" in stage_config.keys() else None
                        ),
                        logging_level=(
                            getattr(MethodLoggingLevel, stage_config.logging_level)
                            if stage_config.logging_level
                            else MethodLoggingLevel.OFF
                        ),
                        metrics_enabled=(stage_config.metrics_enabled if stage_config.metrics_enabled else None),
                        throttling_burst_limit=(
                            stage_config.throttling_burst_limit if stage_config.throttling_burst_limit else None
                        ),
                        throttling_rate_limit=(
                            stage_config.throttling_rate_limit if stage_config.throttling_rate_limit else None
                        ),
                        tracing_enabled=(
                            stage_config.tracing_enabled if "tracing_enabled" in stage_config.keys() else None
                        ),
                        variables=(stage_config.stage_variables if stage_config.stage_variables else None),
                    )

                    if not self.stack.resources.api[api_name].deployments:
                        self.stack.resources.api[api_name].deployments = {}

                    if not self.stack.resources.api[api_name].deployments:
                        self.stack.resources.api[api_name].stages = {}

                    self.stack.resources.api[api_name].deployments[stage_name] = deployment
                    self.stack.resources.api[api_name].stages[stage_name] = stage

                if api_config.configuration.custom_domains and not self.stack.resources.api[api_name].custom_domains:
                    self.stack.resources.api[api_name].custom_domains = []
                for custom_domain_config in api_config.configuration.custom_domains:
                    certificate = get_ssl_certificate(self.stack, custom_domain_config.certificate)
                    domain = DomainName(
                        self.stack,
                        custom_domain_config.logical_name,
                        certificate=certificate,
                        domain_name=replace_placeholders_in_string(self.stack, custom_domain_config.domain_host),
                    )

                    base_path = custom_domain_config.path if custom_domain_config.path else None

                    domain.add_base_path_mapping(
                        rest_api,
                        base_path=base_path,
                        stage=self.stack.resources.api[api_name].stages[custom_domain_config.stage],
                    )

                    create_dns_records(self.stack, custom_domain_config.dns_recordset)

                    for dependency_logical_name in custom_domain_config.depends_on:
                        dependency = get_resource_by_logical_name(self.stack, dependency_logical_name)
                        domain.node.add_dependency(dependency)
                    self.stack.resources.api[api_name].custom_domains.append(domain)

    def _create_api_resource(
        self,
        rest_api: RestApi,
        resource_name: str,
        resource_config: benedict,
        parent: Resource,
    ) -> None:
        resource = parent.add_resource(resource_name, default_cors_preflight_options=None, default_method_options=None)
        if "resources" in resource_config.keys():
            for child_resource_name, child_resource_config in resource_config.resources.items():
                self._create_api_resource(
                    rest_api=rest_api,
                    resource_name=child_resource_name,
                    resource_config=child_resource_config,
                    parent=resource,
                )
        if "methods" in resource_config.keys():
            for method, method_config in resource_config.methods.items():
                self._create_api_method(
                    rest_api=rest_api, http_method=self.Method[method], method_config=method_config, resource=resource
                )

    def _create_api_method(
        self,
        rest_api: RestApi,
        http_method: Method,
        method_config: benedict,
        resource: Resource,
    ) -> None:
        if method_config.integration_request.integration_type == "lambda":
            if method_config.integration_request.lambda_proxy:
                integration = self._get_api_lambda_proxy_integration(
                    rest_api=rest_api,
                    function_name=method_config.integration_request.lambda_function.name,
                    function_alias=(
                        method_config.integration_request.lambda_function.alias
                        if method_config.integration_request.lambda_function.alias
                        else None
                    ),
                    integration_request_parameters=(
                        method_config.integration_request.request_parameters
                        if method_config.integration_request.request_parameters
                        else None
                    ),
                    timeout=(
                        method_config.integration_request.timeout
                        if method_config.integration_request.timeout
                        else "PT29S"
                    ),
                )
            else:
                integration = self._get_api_lambda_integration(
                    rest_api=rest_api,
                    function_name=method_config.integration_request.lambda_function.name,
                    integration_request=(method_config.integration_request),
                    integration_response=(method_config.integration_response),
                )
        elif method_config.integration_request.integration_type == "mock":
            integration = self._create_api_mock_integration(
                rest_api=rest_api,
                integration_request=method_config.integration_request,
                integration_response=method_config.integration_response,
            )
        elif method_config.integration_request.integration_type:
            raise NotImplementedError(
                f"Integration type of {method_config.integration_request.integration_type} has not yet been implemented"
            )
        else:
            integration = None

        authorizer_name = method_config.method_request.authorization
        if authorizer_name:
            if method_config.method_request.authorization_type == "CUSTOM":
                authorization_type = getattr(AuthorizationType, method_config.method_request.authorization_type)
                authorizer = self._get_api_cfn_lambda_token_authorizer(
                    rest_api=rest_api, authorizer_name=authorizer_name
                )
            else:
                raise NotImplementedError(
                    f"API Gateway authorizer type of {method_config.method_request.authorization_type} has not yet been implemented"
                )

        else:
            authorizer = None
            authorization_type = AuthorizationType.NONE

        method_responses = []
        for response in method_config.method_response.responses:
            models = {}
            for content_type, model in response.body.models.items():
                if model == "EMPTY":
                    models[content_type] = Model.EMPTY_MODEL
                elif model == "ERROR":
                    models[content_type] = Model.ERROR_MODEL
                else:
                    models[content_type] = self._get_api_model(rest_api=rest_api, model_name=model)
            parameters = {}
            for header in response.headers:
                parameters[f"method.response.header.{header}"] = True

            method_responses.append(
                MethodResponse(status_code=response.status_code, response_models=models, response_parameters=parameters)
            )

        request_models = {}
        for content_type, model in method_config.method_request.body_validation.items():
            request_models[content_type] = self._get_api_model(rest_api=rest_api, model_name=model)

        request_parameters = {}
        for header in method_config.method_request.header_validation:
            request_parameters[f"method.request.header.{header.header}"] = header.required

        for query_string in method_config.method_request.query_string_validation:
            request_parameters[f"method.request.querystring.{query_string.query_string}"] = header.required

        if method_config.method_request.request_validator:
            validate_body = (
                method_config.method_request.request_validator.validate_body
                if method_config.method_request.request_validator.validate_body
                else False
            )
            validate_parameters = (
                method_config.method_request.request_validator.validate_parameters
                if method_config.method_request.request_validator.validate_parameters
                else False
            )

            request_validator_name = "Validate"
            if validate_body:
                request_validator_name += " Body"
            if validate_parameters:
                if request_validator_name.endswith("Validate"):
                    request_validator_name += "Parameters and Headers"
                else:
                    request_validator_name += ", Parameters, and Headers"

            request_validator_logical_name = (
                f"{rest_api.rest_api_name}{request_validator_name.replace(',', '').replace(' ', '')}"
            )

            if (
                request_validator_logical_name
                not in self.stack.resources.api[rest_api.rest_api_name].request_validators.keys()
            ):
                request_validator = RequestValidator(
                    self.stack,
                    request_validator_logical_name,
                    rest_api=rest_api,
                    request_validator_name=request_validator_name,
                    validate_request_body=validate_body,
                    validate_request_parameters=validate_parameters,
                )
                self.stack.resources.api[rest_api.rest_api_name].request_validators[
                    request_validator_logical_name
                ] = request_validator
            else:
                request_validator = self.stack.resources.api[rest_api.rest_api_name].request_validators[
                    request_validator_logical_name
                ]
        else:
            request_validator = None

        _ = resource.add_method(
            http_method.value,
            integration,
            authorization_type=authorization_type,
            authorizer=authorizer,
            method_responses=method_responses,
            request_models=(request_models if request_models else None),
            request_parameters=(request_parameters if request_parameters else None),
            request_validator=request_validator,
        )

        if (
            method_config.integration_request.post_deployment_custom_resources
            and method_config.integration_request.integration_type == "lambda"
        ):
            for (
                custom_resource_name,
                custom_resource_config,
            ) in method_config.integration_request.post_deployment_custom_resources.items():
                if custom_resource_config.resource_type == "Custom::ApiGatewayIntegrationUpdater":
                    lambda_function_name = method_config.integration_request.lambda_function.name

                    generate_lambda_custom_resource_api_gateway_integration_updater(
                        stack=self.stack,
                        rest_api_name=rest_api.rest_api_name,
                        resource_id=resource.resource_id,
                        method=http_method.value,
                        lambda_function_name=lambda_function_name,
                        custom_resource_name=custom_resource_name,
                        custom_resource_logical_name=custom_resource_config.logical_name,
                        custom_resource_type=custom_resource_config.resource_type,
                        custom_resource_provider=custom_resource_config.provider,
                        lambda_function_version=(
                            "LATEST" if not method_config.integration_request.lambda_function.alias else None
                        ),
                        lambda_function_alias=(
                            method_config.integration_request.lambda_function.alias
                            if method_config.integration_request.lambda_function.alias
                            else None
                        ),
                        custom_resource_dependencies=(
                            custom_resource_config.depends_on if custom_resource_config.depends_on else []
                        ),
                    )
                else:
                    raise NotImplementedError(
                        f"Custom resources of type {custom_resource_config.resource_type} has not yet been implemented"
                    )

    def _create_json_schema_object(self, schema: Any) -> JsonSchema:
        def key_in_schema(key: str, schema: Any) -> bool:
            return key in schema.keys() if isinstance(schema, dict) else False

        additional_items = (
            ([self._create_json_schema_object(item) for item in schema["additional_items"]])
            if key_in_schema("additional_items", schema)
            else None
        )
        additional_properties = (
            (
                schema["additional_properties"]
                if isinstance(schema["additional_properties"], bool)
                else self._create_json_schema_object(schema["additional_properties"])
            )
            if key_in_schema("additional_properties", schema)
            else None
        )
        all_of = (
            ([self._create_json_schema_object(item) for item in schema["all_of"]])
            if key_in_schema("all_of", schema)
            else None
        )
        any_of = (
            ([self._create_json_schema_object(item) for item in schema["any_of"]])
            if key_in_schema("any_of", schema)
            else None
        )
        contains = (
            (
                [self._create_json_schema_object(item) for item in schema["contains"]]
                if isinstance(schema["contains"], list)
                else self._create_json_schema_object(schema["contains"])
            )
            if key_in_schema("contains", schema)
            else None
        )
        definitions = (
            ({key: self._create_json_schema_object(value) for key, value in schema["definitions"].items()})
            if key_in_schema("definitions", schema)
            else None
        )
        dependencies = (
            (
                [item for item in schema["dependencies"]]
                if isinstance(schema["dependencies"], list)
                else self._create_json_schema_object(schema["dependencies"])
            )
            if key_in_schema("dependencies", schema)
            else None
        )
        description = schema["description"] if key_in_schema("description", schema) else None
        exclusive_maximum = schema["exclusive_maximum"] if key_in_schema("exclusive_maximum", schema) else None
        exclusive_minimum = schema["exclusive_minimum"] if key_in_schema("exclusive_minimum", schema) else None
        format_keyword = schema["format"] if key_in_schema("format", schema) else None
        id_keyword = schema["id"] if key_in_schema("id", schema) else None
        items = (
            (
                ([item for item in schema["items"]] if len(schema["items"]) > 0 else [None])
                if isinstance(schema["items"], list)
                else self._create_json_schema_object(schema["items"])
            )
            if key_in_schema("items", schema)
            else None
        )
        maximum = schema["maximum"] if key_in_schema("maximum", schema) else None
        max_items = schema["max_items"] if key_in_schema("max_items", schema) else None
        max_length = schema["max_length"] if key_in_schema("max_length", schema) else None
        max_properties = schema["max_properties"] if key_in_schema("max_properties", schema) else None
        minimum = schema["minimum"] if key_in_schema("minimum", schema) else None
        min_items = schema["min_items"] if key_in_schema("min_items", schema) else None
        min_length = schema["min_length"] if key_in_schema("min_length", schema) else None
        min_properties = schema["min_properties"] if key_in_schema("min_properties", schema) else None
        multiple_of = schema["multiple_of"] if key_in_schema("multiple_of", schema) else None
        not_keyword = self._create_json_schema_object(schema["not"]) if key_in_schema("not", schema) else None
        one_of = (
            [self._create_json_schema_object(item) for item in schema["one_of"]]
            if key_in_schema("one_of", schema)
            else None
        )
        pattern = schema["pattern"] if key_in_schema("pattern", schema) else None
        pattern_properties = (
            ({key: self._create_json_schema_object(value) for key, value in schema["pattern_properties"].items()})
            if key_in_schema("pattern_properties", schema)
            else None
        )
        properties = (
            ({key: self._create_json_schema_object(value) for key, value in schema["properties"].items()})
            if key_in_schema("properties", schema)
            else None
        )
        property_names = (
            ({key: self._create_json_schema_object(value) for key, value in schema["property_names"].items()})
            if key_in_schema("property_names", schema)
            else None
        )
        ref = schema["ref"] if key_in_schema("ref", schema) else None
        required = [item for item in schema["required"]] if key_in_schema("required", schema) else None
        title = schema["title"] if key_in_schema("title", schema) else None
        type_keyword = (
            (
                [getattr(JsonSchemaType, schema["type"].upper())]
                if isinstance(schema["type"], list)
                else getattr(JsonSchemaType, schema["type"].upper())
            )
            if key_in_schema("type", schema)
            else None
        )
        unique_items = schema["unique_items"] if key_in_schema("unique_items", schema) else None

        return JsonSchema(
            additional_items=additional_items,
            additional_properties=additional_properties,
            all_of=all_of,
            any_of=any_of,
            contains=contains,
            definitions=definitions,
            dependencies=dependencies,
            description=description,
            exclusive_maximum=exclusive_maximum,
            exclusive_minimum=exclusive_minimum,
            format=format_keyword,
            id=id_keyword,
            items=items,
            maximum=maximum,
            max_items=max_items,
            max_length=max_length,
            max_properties=max_properties,
            minimum=minimum,
            min_items=min_items,
            min_length=min_length,
            min_properties=min_properties,
            multiple_of=multiple_of,
            not_=not_keyword,
            one_of=one_of,
            pattern=pattern,
            pattern_properties=pattern_properties,
            properties=properties,
            property_names=property_names,
            ref=ref,
            required=required,
            title=title,
            type=type_keyword,
            unique_items=unique_items,
        )

    def _get_api_model(self, rest_api: RestApi, model_name: str) -> Model:
        model_config = self.api_config[rest_api.rest_api_name].models[model_name]
        if model_name not in self.stack.resources.api[rest_api.rest_api_name].models.keys():
            self.stack.resources.api[rest_api.rest_api_name].models[model_name] = self._create_api_model(
                rest_api=rest_api,
                model_name=model_name,
                model_config=model_config,
            )

        return self.stack.resources.api[rest_api.rest_api_name].models[model_name]

    def _create_api_model(self, rest_api: RestApi, model_name: str, model_config: benedict) -> Model:
        model = Model(
            self.stack,
            f"{self.api_config[rest_api.rest_api_name].logical_name}{model_config.logical_name}",
            rest_api=rest_api,
            schema=self._create_json_schema_object(model_config.model),
            content_type=model_config.content_type,
            model_name=model_name,
            description=(model_config.description if model_config.description else None),
        )

        return model

    def _get_api_gateway_response(self, rest_api: RestApi, gateway_response_name: str) -> GatewayResponse:
        gateway_response_config = self.api_config[rest_api.rest_api_name].gateway_responses[gateway_response_name]
        if gateway_response_name not in self.stack.resources.api[rest_api.rest_api_name].gateway_responses.keys():
            self.stack.resources.api[rest_api.rest_api_name].gateway_responses[gateway_response_name] = (
                self._create_api_gateway_response(
                    rest_api=rest_api,
                    gateway_response_name=gateway_response_name,
                    gateway_response_config=gateway_response_config,
                )
            )

        return self.stack.resources.api[rest_api.rest_api_name].gateway_responses[gateway_response_name]

    def _create_api_gateway_response(
        self, rest_api: RestApi, gateway_response_name: str, gateway_response_config: benedict
    ) -> GatewayResponse:
        response_type = getattr(ResponseType, gateway_response_name)
        response_headers = {}
        for header, value in gateway_response_config.headers.items():
            response_headers[header] = replace_placeholders_in_string(self.stack, value)
        return GatewayResponse(
            self.stack,
            f"{self.api_config[rest_api.rest_api_name].logical_name}{gateway_response_config.logical_name}",
            rest_api=rest_api,
            type=response_type,
            response_headers=response_headers,
            status_code=(gateway_response_config.status_code if gateway_response_config.status_code else None),
            templates=gateway_response_config.templates,
        )

    def _get_api_cfn_lambda_token_authorizer(
        self, rest_api: RestApi, authorizer_name: str, assume_role: Role = None
    ) -> CfnAuthorizer:
        authorizer_config = self.api_config[rest_api.rest_api_name].authorizers[authorizer_name]
        if authorizer_config.authorizer_type == "TOKEN":
            if authorizer_name not in self.stack.resources.api[rest_api.rest_api_name].authorizers.keys():
                self.stack.resources.api[rest_api.rest_api_name].authorizers[authorizer_name] = (
                    self._create_api_cfn_lambda_token_authorizer(
                        rest_api=rest_api,
                        authorizer_name=authorizer_name,
                        authorizer_config=authorizer_config,
                        assume_role=assume_role,
                    )
                )
        else:
            raise NotImplementedError(
                f"Authorizer types of {authorizer_config.authorizer_type} have not yet been implemented"
            )

        return self.stack.resources.api[rest_api.rest_api_name].authorizers[authorizer_name]

    def _create_api_cfn_lambda_token_authorizer(
        self, rest_api: RestApi, authorizer_name: str, authorizer_config: benedict, assume_role: Role = None
    ) -> CfnAuthorizer:
        function_name = authorizer_config.lambda_function
        function_dict = get_lambda_function(self.stack, function_name)
        if authorizer_config.function_alias:
            function = function_dict.aliases[authorizer_config.function_alias]
        else:
            function = function_dict.function

        results_cache_ttl = int(authorizer_config.cache_ttl) if authorizer_config.cache_ttl else 0

        authorizer = TokenAuthorizer(
            self.stack,
            f"{self.api_config[rest_api.rest_api_name].logical_name}{authorizer_config.logical_name}",
            identity_source=f"method.request.header.{authorizer_config.token_source}",
            validation_regex=authorizer_config.token_validation,
            handler=function,
            authorizer_name=authorizer_name,
            results_cache_ttl=Duration.seconds(results_cache_ttl),
        )

        return authorizer

    def _get_api_lambda_proxy_integration(
        self,
        rest_api: RestApi,
        function_name: str,
        function_alias: str = None,
        integration_request_parameters: benedict = None,
        timeout: str = "PT29S",
    ) -> LambdaIntegration:
        if function_name not in self.stack.resources.api[rest_api.rest_api_name].integrations.keys():
            self.stack.resources.api[rest_api.rest_api_name].integrations[function_name] = (
                self._create_api_lambda_proxy_integration(
                    function_name=function_name,
                    function_alias=function_alias,
                    integration_request_parameters=integration_request_parameters,
                    timeout=timeout,
                )
            )

        return self.stack.resources.api[rest_api.rest_api_name].integrations[function_name]

    def _create_api_lambda_proxy_integration(
        self,
        function_name: str,
        function_alias: str = None,
        integration_request_parameters: benedict = None,
        timeout: str = "PT29S",
    ) -> LambdaIntegration:
        function_dict = get_lambda_function(self.stack, function_name)
        if function_alias:
            function = function_dict.aliases[function_alias]
        else:
            function = function_dict.function

        request_parameters = (
            self._create_request_parameters(integration_request_parameters) if integration_request_parameters else None
        )

        return LambdaIntegration(
            function,
            proxy=True,
            request_parameters=(request_parameters if request_parameters else None),
            timeout=Duration.parse(timeout),
        )

    def _get_api_lambda_integration(
        self,
        rest_api: RestApi,
        function_name: str,
        integration_request: benedict = benedict({}, keyattr_dynamic=True),
        integration_response: benedict = benedict({}, keyattr_dynamic=True),
    ) -> LambdaIntegration:
        if function_name not in self.stack.resources.api[rest_api.rest_api_name].integrations.keys():
            self.stack.resources.api[rest_api.rest_api_name].integrations[function_name] = (
                self._create_api_lambda_integration(
                    rest_api=rest_api,
                    function_name=function_name,
                    integration_request=integration_request,
                    integration_response=integration_response,
                )
            )

        return self.stack.resources.api[rest_api.rest_api_name].integrations[function_name]

    def _create_api_lambda_integration(
        self,
        function_name: str,
        rest_api: RestApi,
        integration_request: benedict = benedict({}, keyattr_dynamic=True),
        integration_response: benedict = benedict({}, keyattr_dynamic=True),
    ) -> LambdaIntegration:
        function_dict = get_lambda_function(self.stack, function_name)
        if integration_request.lambda_function.alias:
            function = function_dict.aliases[integration_request.lambda_function.alias]
        else:
            function = function_dict.function

        content_handling = (
            getattr(ContentHandling, integration_request.content_handling)
            if integration_request.content_handling
            else None
        )

        request_parameters = (
            self._create_request_parameters(integration_request.request_parameters)
            if integration_request.request_parameters
            else None
        )

        passthrough_behavior = (
            integration_request.request_parameters.passthrough_behavior
            if integration_request.request_parameters.passthrough_behavior
            else "WHEN_NO_MATCH"
        )

        integration_responses = []
        for integration_response in integration_response.responses:
            integration_responses.append(self._create_integration_response(integration_response, rest_api))

        return LambdaIntegration(
            function,
            proxy=False,
            content_handling=content_handling,
            integration_responses=integration_responses,
            passthrough_behavior=(getattr(PassthroughBehavior, passthrough_behavior)),
            request_parameters=request_parameters,
            request_templates=(
                integration_request.request_templates if integration_request.request_templates else None
            ),
            timeout=Duration.parse(integration_request.timeout if integration_request.timeout else "PT29S"),
        )

    def _create_api_mock_integration(
        self,
        rest_api: RestApi,
        integration_request: benedict = benedict({}, keyattr_dynamic=True),
        integration_response: benedict = benedict({}, keyattr_dynamic=True),
    ) -> MockIntegration:
        content_handling = (
            getattr(ContentHandling, integration_request.content_handling)
            if integration_request.content_handling
            else None
        )

        request_parameters = (
            self._create_request_parameters(integration_request.request_parameters)
            if integration_request.request_parameters
            else None
        )

        passthrough_behavior = (
            integration_request.request_parameters.passthrough_behavior
            if integration_request.request_parameters.passthrough_behavior
            else "WHEN_NO_MATCH"
        )

        integration_responses = []
        for integration_response in integration_response.responses:
            integration_responses.append(self._create_integration_response(integration_response, rest_api))

        return MockIntegration(
            content_handling=content_handling,
            integration_responses=integration_responses,
            passthrough_behavior=getattr(PassthroughBehavior, passthrough_behavior),
            request_parameters=request_parameters,
            request_templates=(
                integration_request.request_templates if integration_request.request_templates else None
            ),
            timeout=Duration.parse(integration_request.timeout if integration_request.timeout else "PT29S"),
        )

    def _create_integration_response(
        self, integration_response: Dict[str, Any], rest_api: RestApi
    ) -> IntegrationResponse:
        error_regex = integration_response.error_regex if integration_response.error_regex else None

        content_handling = (
            getattr(ContentHandling, integration_response.content_handling)
            if integration_response.content_handling
            else None
        )

        response_parameters = {} if integration_response.header_mappings else None
        for header_mapping in integration_response.header_mappings:
            response_parameters[header_mapping.name] = replace_placeholders_in_string(self.stack, header_mapping.value)

        response_templates = {} if integration_response.response_templates else None
        for response_template in integration_response.response_templates:
            models = self.api_config[rest_api.rest_api_name].models
            model_content = None
            for model in models:
                if model.name == response_template.model:
                    model_content = dumps(model.model)
            response_templates[response_template.content_type] = model_content

        return IntegrationResponse(
            status_code=integration_response.status_code,
            content_handling=content_handling,
            response_parameters=response_parameters,
            response_templates=response_templates,
            selection_pattern=error_regex,
        )

    def _create_request_parameters(self, request_parameters: benedict) -> Dict:
        ret = {}
        for parameter in request_parameters.query_strings.items():
            valid_parameter_locations = ["querystring", "path", "header"]
            if parameter.location not in valid_parameter_locations:
                raise ValueError(
                    f"The parameter location {parameter.location} is invalid. It must be one of {', '.join(valid_parameter_locations)}"
                )
            ret[f"integration.request.{parameter.location}.{parameter.name}"] = parameter.value
        return ret
