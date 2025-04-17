from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk.aws_certificatemanager import Certificate
from aws_cdk.aws_cloudfront import (
    AllowedMethods,
    BehaviorOptions,
    CacheCookieBehavior,
    CacheHeaderBehavior,
    CacheQueryStringBehavior,
    CachePolicy,
    CachedMethods,
    Distribution,
    EdgeLambda,
    ErrorResponse,
    Function,
    FunctionAssociation,
    FunctionCode,
    FunctionEventType,
    HeadersFrameOption,
    HeadersReferrerPolicy,
    HttpVersion,
    LambdaEdgeEventType,
    OriginRequestCookieBehavior,
    OriginRequestHeaderBehavior,
    OriginRequestPolicy,
    OriginRequestQueryStringBehavior,
    PriceClass,
    ResponseCustomHeader,
    ResponseCustomHeadersBehavior,
    ResponseHeadersContentSecurityPolicy,
    ResponseHeadersContentTypeOptions,
    ResponseHeadersCorsBehavior,
    ResponseHeadersFrameOptions,
    ResponseHeadersPolicy,
    ResponseHeadersReferrerPolicy,
    ResponseHeadersStrictTransportSecurity,
    ResponseHeadersXSSProtection,
    ResponseSecurityHeadersBehavior,
    SecurityPolicyProtocol,
    ViewerProtocolPolicy,
)
from aws_cdk.aws_cloudfront_origins import OriginGroup, S3BucketOrigin
from aws_cdk.aws_iam import PolicyDocument, PolicyStatement
from aws_cdk.aws_s3 import (
    CfnBucketPolicy,
    CfnBucketPolicyProps,
    BlockPublicAccess,
    Bucket,
    BucketEncryption,
    BucketPolicy,
    EventType,
    HttpMethods,
    NotificationKeyFilter,
)
from aws_cdk.aws_s3_notifications import LambdaDestination
from benedict import benedict
from json import dumps, loads
from typing import Any, List, Dict

from src.backend.configuration.common import (
    create_dns_records,
    create_lambda_custom_resource_empty_bucket,
    format_logical_name_uppercase,
    generate_lambda_custom_resource_cloudfront_behavior_edge_lambda,
    generate_lambda_custom_resource_cloudfront_function_redirect_placeholders,
    get_cdk_config,
    get_cloudfront_config,
    get_deploy_region,
    get_iam_config,
    get_iam_policy,
    get_lambda_function,
    get_resource_attribute,
    get_resource_by_logical_name,
    get_s3_config,
    get_ssl_certificate,
    replace_iam_statements_placeholders,
    replace_placeholders_in_string,
)

from vars import env, root_dir


class create_web_infrastructure:
    def __init__(self, stack: Stack, deploy_type: str):
        self.deploy_type = deploy_type
        self.stack = stack
        self.cdk_config = get_cdk_config()
        self.cloudfront_config = get_cloudfront_config()
        self.iam_config = get_iam_config()
        self.s3_config = get_s3_config()

    def configure_s3_buckets_policies(self) -> None:
        for bucket_name, bucket_config in self.s3_config.items():
            bucket = self._get_bucket(bucket_name)
            if bucket_config.bucket_policy and bucket_config.bucket_policy != "":
                policy_document_dict = self.iam_config.resource_based_policies.s3_policies[
                    bucket_config.bucket_policy
                ].policy

                policy_document = PolicyDocument.from_json(
                    replace_iam_statements_placeholders(self.stack, policy_document_dict)
                )
                policy_logical_name = self.iam_config.resource_based_policies.s3_policies[
                    bucket_config.bucket_policy
                ].logical_name

                policy = CfnBucketPolicy(
                    self.stack, policy_logical_name, bucket=bucket.bucket_name, policy_document=policy_document
                )
                policy.apply_removal_policy(RemovalPolicy.RETAIN)

    def configure_s3_buckets_cors(self) -> None:
        for bucket_name, bucket_config in self.s3_config.items():
            bucket = self._get_bucket(bucket_name)
            for cors_rule in bucket_config.cors:
                allowed_methods = []
                for method in cors_rule.allowed_methods:
                    allowed_methods.append(getattr(HttpMethods, method))

                allowed_origins_string = dumps(cors_rule.allowed_origins)
                allowed_origins = loads(replace_placeholders_in_string(self.stack, allowed_origins_string))

                allowed_headers = cors_rule.allowed_headers if cors_rule.allowed_headers else None

                exposed_headers = cors_rule.expose_headers if cors_rule.expose_headers else None

                rule_id = cors_rule.id if cors_rule.id else None

                max_age = replace_placeholders_in_string(self.stack, cors_rule.max_age) if cors_rule.max_age else None

                bucket.add_cors_rule(
                    allowed_methods=allowed_methods,
                    allowed_origins=allowed_origins,
                    allowed_headers=allowed_headers,
                    exposed_headers=exposed_headers,
                    id=rule_id,
                    max_age=max_age,
                )

    def create_s3_buckets(self) -> None:
        for bucket_name, bucket_config in self.s3_config.items():
            block_public_access = BlockPublicAccess(
                block_public_acls=(
                    bucket_config.block_public_access.block_all or bucket_config.block_public_access.block_public_acls
                ),
                block_public_policy=(
                    bucket_config.block_public_access.block_all or bucket_config.block_public_access.block_public_policy
                ),
                ignore_public_acls=(
                    bucket_config.block_public_access.block_all or bucket_config.block_public_access.ignore_public_acls
                ),
                restrict_public_buckets=(
                    bucket_config.block_public_access.block_all
                    or bucket_config.block_public_access.restrict_public_buckets
                ),
            )
            removal_policy = (
                RemovalPolicy.RETAIN
                if env.APP_DEPLOY_ENV == "PROD" and bucket_config.retain_in_prod
                else RemovalPolicy.DESTROY
            )
            self.stack.resources.s3.buckets[bucket_name] = Bucket(
                self.stack,
                bucket_config.logical_name,
                block_public_access=block_public_access,
                encryption=BucketEncryption(bucket_config.encryption),
                enforce_ssl=bucket_config.enforce_ssl,
                versioned=bucket_config.versioned,
                removal_policy=removal_policy,
            )

            if (
                "post_deployment_custom_resources" in bucket_config.keys()
                and bucket_config.post_deployment_custom_resources
            ):
                for (
                    custom_resource_name,
                    custom_resource_config,
                ) in bucket_config.post_deployment_custom_resources.items():
                    if custom_resource_config.resource_type == "Custom::EmptyBucket":
                        if env.APP_DEPLOY_ENV != "PROD" or (
                            env.APP_DEPLOY_ENV == "PROD" and custom_resource_config.empty_on_prod
                        ):
                            create_lambda_custom_resource_empty_bucket(
                                stack=self.stack,
                                bucket_name=self.stack.resources.s3.buckets[bucket_name].bucket_name,
                                bucket_region=get_deploy_region(),
                                bucket_arn=self.stack.resources.s3.buckets[bucket_name].bucket_arn,
                                custom_resource_name=custom_resource_name,
                                custom_resource_logical_name=custom_resource_config.logical_name,
                                custom_resource_type=custom_resource_config.resource_type,
                                custom_resource_provider=custom_resource_config.provider,
                                custom_resource_dependencies=custom_resource_config.depends_on,
                            )
                    else:
                        raise NotImplementedError(
                            f"Custom resources of type {custom_resource_config.resource_type} has not yet been implemented"
                        )

    def create_s3_event_notifications(self) -> benedict:
        for bucket_name, bucket_config in self.s3_config.items():
            for event_notification in bucket_config.event_notifications:
                if event_notification.destination_type == "lambda":
                    bucket = self._get_bucket(bucket_name)
                    function_dict = get_lambda_function(self.stack, event_notification.destination.function_name)
                    if event_notification.destination.function_alias:
                        function = function_dict.aliases[event_notification.destination.function_alias]
                    else:
                        function = function_dict.function

                    destination = LambdaDestination(function)

                    filter_prefix = event_notification.prefix if event_notification.prefix else None
                    filter_suffix = event_notification.suffix if event_notification.suffix else None
                    notification_key_filter = (
                        NotificationKeyFilter(prefix=filter_prefix, suffix=filter_suffix)
                        if filter_prefix or filter_suffix
                        else None
                    )

                    for event_type in event_notification.event_types:

                        bucket.add_event_notification(
                            getattr(EventType, event_type),
                            destination,
                            notification_key_filter,
                        )
                else:
                    raise NotImplementedError("Destination types other than Lambda have not yet been implemented")

    def _get_bucket(self, bucket_name):
        if self.stack.resources.s3.buckets[bucket_name].stack.stack_name != self.stack.stack_name:
            existing_bucket_region = self.stack.resources.s3.buckets[bucket_name].stack.region
            new_bucket_name = bucket_name + self.stack.stack_name
            if new_bucket_name in self.stack.resources.s3.buckets:
                return self.stack.resources.s3.buckets[new_bucket_name]
            exiting_bucket_logical_name = self.s3_config[bucket_name].logical_name
            new_bucket_logical_name = self.s3_config[bucket_name].logical_name + self.stack.stack_name
            bucket_arn = get_resource_attribute(self.stack, exiting_bucket_logical_name, "bucket_arn")
            self.stack.resources.s3.buckets[new_bucket_name] = Bucket.from_bucket_attributes(
                self.stack, new_bucket_logical_name, bucket_arn=bucket_arn, region=existing_bucket_region
            )
            return self.stack.resources.s3.buckets[new_bucket_name]
        else:
            return self.stack.resources.s3.buckets[bucket_name]

    def create_cloudfront_distributions(self) -> benedict:
        for distribution_name, distribution_config in self.cloudfront_config.distributions.items():
            self.stack.resources.cloudfront.distributions[distribution_name] = {}
            domain_names = [
                replace_placeholders_in_string(self.stack, cname) for cname in distribution_config.general.cnames
            ]
            certificate = get_ssl_certificate(self.stack, distribution_config.general.certificate)
            default_behavior = self._create_cloudfront_behavior(
                distribution_config.default_behavior,
                distribution_config,
                distribution_name,
                is_default_cache_behavior=True,
            )
            additional_behaviors = {}
            for behavior_config in distribution_config.additional_behaviors:
                additional_behaviors[behavior_config.path_pattern] = self._create_cloudfront_behavior(
                    behavior_config, distribution_config, distribution_name, is_default_cache_behavior=False
                )

            error_responses = []
            for error_response in distribution_config.error_responses:
                error_responses.append(
                    ErrorResponse(
                        http_status=error_response.http_status,
                        response_http_status=error_response.response_http_status,
                        response_page_path=(
                            replace_placeholders_in_string(self.stack, error_response.response_page_path)
                            if error_response.response_page_path
                            else None
                        ),
                        ttl=(Duration.parse(error_response.ttl) if error_response.ttl else None),
                    )
                )

            distribution = Distribution(
                self.stack,
                distribution_config.logical_name,
                default_behavior=default_behavior,
                additional_behaviors=additional_behaviors,
                certificate=certificate,
                comment=(distribution_config.general.comment if distribution_config.general.comment else ""),
                default_root_object=(
                    distribution_config.general.default_root_object
                    if distribution_config.general.default_root_object
                    else None
                ),
                domain_names=domain_names,
                enabled=(distribution_config.general.enabled if distribution_config.general.enabled else True),
                enable_ipv6=(
                    distribution_config.general.enable_ipv6 if distribution_config.general.enable_ipv6 else False
                ),
                enable_logging=(
                    distribution_config.logging.enable_logging
                    if "enable_logging" in distribution_config.logging.keys()
                    else False
                ),
                error_responses=error_responses,
                http_version=(
                    getattr(HttpVersion, distribution_config.general.http_version)
                    if distribution_config.general.http_version
                    else HttpVersion.HTTP2_AND_3
                ),
                log_bucket=(
                    self.stack.resources.s3.buckets[distribution_config.logging.log_bucket]
                    if "enable_logging" in distribution_config.logging.keys()
                    and distribution_config.enable_logging is True
                    and distribution_config.logging.log_bucket
                    else None
                ),
                log_file_prefix=(
                    distribution_config.logging.log_file_prefix
                    if "enable_logging" in distribution_config.logging.keys()
                    and distribution_config.enable_logging is True
                    and distribution_config.logging.log_bucket
                    and distribution_config.logging.log_file_prefix
                    else None
                ),
                log_includes_cookies=(
                    distribution_config.logging.log_includes_cookies
                    if "enable_logging" in distribution_config.logging.keys()
                    and distribution_config.enable_logging is True
                    and distribution_config.logging.log_bucket
                    and distribution_config.logging.log_includes_cookies
                    else None
                ),
                minimum_protocol_version=(
                    getattr(SecurityPolicyProtocol, distribution_config.general.minimum_protocol_version)
                    if distribution_config.general.minimum_protocol_version
                    else None
                ),
                price_class=(
                    getattr(PriceClass, distribution_config.general.price_class)
                    if distribution_config.general.price_class
                    else PriceClass.PRICE_CLASS_100
                ),
                publish_additional_metrics=(
                    distribution_config.general.publish_additional_metrics
                    if "publish_additional_metrics" in distribution_config.general.keys()
                    else False
                ),
            )

            create_dns_records(self.stack, distribution_config.general.dns_recordset)

            for dependency_logical_name in distribution_config.general.depends_on:
                dependency = get_resource_by_logical_name(self.stack, dependency_logical_name)
                distribution.node.add_dependency(dependency)

            behavior_configs = [distribution_config.default_behavior] + distribution_config.additional_behaviors
            for behavior_config in behavior_configs:
                for event_type, event_config in behavior_config.functions.items():
                    if (
                        "post_deployment_custom_resources" in event_config.keys()
                        and event_config.post_deployment_custom_resources
                    ):
                        for (
                            custom_resource_name,
                            custom_resource_config,
                        ) in event_config.post_deployment_custom_resources.items():
                            if custom_resource_config.resource_type == "Custom::CloudFrontBehaviorEdgeLambdaUpdater":
                                generate_lambda_custom_resource_cloudfront_behavior_edge_lambda(
                                    stack=self.stack,
                                    distribution_name=distribution_name,
                                    distribution_id=get_resource_attribute(
                                        self.stack, distribution_config.logical_name, "distribution_id"
                                    ),
                                    path_pattern=behavior_config.path_pattern,
                                    function_name=event_config.function_name,
                                    function_version="LATEST",
                                    event_type=event_type.lower().replace("_", "-"),
                                    include_body=(
                                        custom_resource_config.include_body
                                        if custom_resource_config.include_body
                                        else False
                                    ),
                                    is_default_cache_behavior=(
                                        False if "path_pattern" in behavior_config.keys() else True
                                    ),
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

            self.stack.resources.cloudfront.distributions[distribution_name].distribution = distribution

    def _create_cloudfront_behavior(
        self,
        behavior_config: benedict,
        distribution_config: benedict,
        distribution_name: str,
        is_default_cache_behavior: bool,
    ) -> BehaviorOptions:
        allowed_methods = (
            getattr(AllowedMethods, behavior_config.allowed_http_methods)
            if behavior_config.allowed_http_methods
            else AllowedMethods.ALLOW_GET_HEAD
        )
        cached_methods = (
            getattr(CachedMethods, behavior_config.cached_http_methods)
            if behavior_config.cached_http_methods
            else CachedMethods.CACHE_GET_HEAD
        )
        if behavior_config.cache_policy:
            policy_name = behavior_config.cache_policy.name
            if behavior_config.cache_policy.aws_managed:
                cache_policy = getattr(CachePolicy, policy_name)
            else:
                cache_policy = self._get_cloudfront_cache_policy(policy_name)

        edge_lambdas = []
        function_associations = []
        for event_type, event_config in behavior_config.functions.items():
            if event_config.type == "EdgeLambda":
                function_dict = get_lambda_function(self.stack, event_config.function_name, create_version=True)
                # Create the function, but if it has any post-deployment resources, do not assign it as an edge lambda
                # The lambda function "cfn-update-cloudfront-behavior-edge-lambda-version" will create the association
                if not function_dict.has_post_deployment_custom_resources:
                    version = function_dict.versions[-1]
                    edge_lambdas.append(
                        EdgeLambda(
                            event_type=getattr(LambdaEdgeEventType, event_type),
                            function_version=version,
                            include_body=(event_config.include_body if event_config.include_body else False),
                        )
                    )
            elif event_config.type == "cloudfront":
                function = self._get_cloudfront_function(event_config.function_name)
                if function:
                    function_associations.append(
                        FunctionAssociation(event_type=getattr(FunctionEventType, event_type), function=function)
                    )

        origin_request_policy = None
        if behavior_config.origin_request_policy:
            policy_name = behavior_config.origin_request_policy.name
            if behavior_config.origin_request_policy.aws_managed:
                origin_request_policy = getattr(OriginRequestPolicy, policy_name)
            else:
                origin_request_policy = self._get_cloudfront_origin_request_policy(policy_name)

        response_headers_policy = None
        if behavior_config.response_headers_policy:
            policy_name = behavior_config.response_headers_policy.name
            if behavior_config.response_headers_policy.aws_managed:
                response_headers_policy = getattr(ResponseHeadersPolicy, policy_name)
            else:
                response_headers_policy = self._get_cloudfront_response_header_policy(policy_name)

        viewer_protocol_policy = (
            getattr(ViewerProtocolPolicy, behavior_config.viewer_protocol_policy)
            if behavior_config.viewer_protocol_policy
            else None
        )

        origin_name = behavior_config.origin.name
        if behavior_config.origin.origin_group:
            origin = self._get_cloudfront_distribution_origin_group(
                origin_name, distribution_config.origin_groups[origin_name], distribution_name
            )
        else:
            origin = self._get_cloudfront_distribution_origin(
                origin_name, distribution_config.origins[origin_name], distribution_name
            )

        return BehaviorOptions(
            allowed_methods=allowed_methods,
            cached_methods=cached_methods,
            cache_policy=cache_policy,
            compress=(behavior_config.compress if behavior_config.compress else None),
            edge_lambdas=edge_lambdas,
            function_associations=function_associations,
            origin_request_policy=origin_request_policy,
            response_headers_policy=response_headers_policy,
            smooth_streaming=(behavior_config.smooth_streaming if behavior_config.smooth_streaming else None),
            viewer_protocol_policy=viewer_protocol_policy,
            origin=origin,
        )

    def _get_cloudfront_distribution_origin(
        self, origin_name: str, origin_config: benedict, distribution_name: str
    ) -> Any:
        if (
            origin_config.bucket_name
            not in self.stack.resources.cloudfront.distributions[distribution_name].origins.keys()
        ):
            self.stack.resources.cloudfront.distributions[distribution_name].origins[origin_config.bucket_name] = (
                self._create_cloudfront_distribution_origin(origin_name, origin_config)
            )
        return self.stack.resources.cloudfront.distributions[distribution_name].origins[origin_config.bucket_name]

    def _create_cloudfront_distribution_origin(self, origin_name: str, origin_config: benedict) -> Any:
        if origin_config.origin_type == "S3BucketOriginWithOAC":
            bucket = self._get_bucket(origin_config.bucket_name)
            return S3BucketOrigin.with_origin_access_control(bucket, origin_path=origin_config.origin_path)
        else:
            raise NotImplementedError("Origins other than S3 have not been implemented")

    def _get_cloudfront_distribution_origin_group(
        self, origin_group_name: str, origin_group_config: benedict, distribution_name: str
    ) -> OriginGroup:
        if (
            origin_group_name
            not in self.stack.resources.cloudfront.distributions[distribution_name].origin_groups.keys()
        ):
            self.stack.resources.cloudfront.distributions[distribution_name].origin_groups[origin_group_name] = (
                self._create_cloudfront_distribution_origin_group(
                    origin_group_name, origin_group_config, distribution_name
                )
            )
        return self.stack.resources.cloudfront.distributions[distribution_name].origin_groups[origin_group_name]

    def _create_cloudfront_distribution_origin_group(
        self, origin_group_name: str, origin_group_config: benedict, distribution_name: str
    ) -> OriginGroup:

        primary_origin_config = self.cdk_config.distributions[distribution_name].origins[
            origin_group_config.primary_origin
        ]
        primary_origin = self._get_cloudfront_distribution_origin(
            origin_group_config.primary_origin, primary_origin_config, distribution_name
        )
        fallback_origin_config = self.cdk_config.distributions[distribution_name].origins[
            origin_group_config.fallback_origin
        ]
        fallback_origin = self._get_cloudfront_distribution_origin(
            origin_group_config.fallback_origin, fallback_origin_config, distribution_name
        )

        return OriginGroup(
            primary_origin=primary_origin,
            fallback_origin=fallback_origin,
            fallback_status_codes=(
                origin_group_config.fallback_status_codes if origin_group_config.fallback_status_codes else None
            ),
        )

    def _get_cloudfront_function(self, function_name: str) -> Function:
        function_config = self.cloudfront_config.cloudfront_functions[function_name]
        if function_name not in self.stack.resources.cloudfront.functions:
            self.stack.resources.cloudfront.functions[function_name] = self._create_cloudfront_function(
                function_name, function_config
            )
        return self.stack.resources.cloudfront.functions[function_name]

    def _create_cloudfront_function(self, function_name: str, function_config: benedict) -> Function:
        function = Function(
            self.stack,
            function_config.logical_name,
            code=FunctionCode.from_file(file_path=f"{root_dir}/{function_config.code_location}"),
            auto_publish=function_config.auto_publish,
            function_name=f"{function_name}_{env.APP_STACK_PREFIX}",
        )

        if (
            "post_deployment_custom_resources" in function_config.keys()
            and function_config.post_deployment_custom_resources
        ):
            for (
                custom_resource_name,
                custom_resource_config,
            ) in function_config.post_deployment_custom_resources.items():
                if custom_resource_config.resource_type == "Custom::CloudFrontFunctionPlaceholderReplacer":
                    generate_lambda_custom_resource_cloudfront_function_redirect_placeholders(
                        stack=self.stack,
                        cloudfront_function_logical_name=function_config.logical_name,
                        cloudfront_function_name=f"{function_name}_{env.APP_STACK_PREFIX}",
                        function_stage="DEVELOPMENT",
                        domain_name=custom_resource_config.domain_name,
                        domain_uri=custom_resource_config.domain_uri,
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

        return function

    def _get_cloudfront_cache_policy(self, policy_name: str) -> CachePolicy:
        if policy_name not in self.stack.resources.cloudfront.policies.cache_policies.keys():
            self.stack.resources.cloudfront.policies.cache_policies[policy_name] = self._create_cloudfront_cache_policy(
                policy_name
            )
        return self.stack.resources.cloudfront.policies.cache_policies[policy_name]

    def _create_cloudfront_cache_policy(self, policy_name: str) -> CachePolicy:
        policy_config = self.cloudfront_config.policies.cache[policy_name]
        cookie_behavior = CacheCookieBehavior.none()
        if policy_config.cookie_behavior.type:
            if policy_config.cookie_behavior.type == "all":
                cookie_behavior = CacheCookieBehavior.all()
            elif policy_config.cookie_behavior.type == "allow_list":
                cookie_behavior = CacheCookieBehavior.allow_list(*policy_config.cookie_behavior.cookies)
            elif policy_config.cookie_behavior.type == "deny_list":
                cookie_behavior = CacheCookieBehavior.allow_list(*policy_config.cookie_behavior.cookies)

        header_behavior = CacheHeaderBehavior.none()
        if policy_config.header_behavior.type and policy_config.header_behavior.type == "allow_list":
            header_behavior = CacheHeaderBehavior.allow_list(*policy_config.header_behavior.headers)

        query_string_behavior = CacheQueryStringBehavior.none()
        if policy_config.query_string_behavior.type:
            if policy_config.query_string_behavior.type == "all":
                query_string_behavior = CacheQueryStringBehavior.all()
            elif policy_config.query_string_behavior.type == "allow_list":
                query_string_behavior = CacheQueryStringBehavior.allow_list(
                    *policy_config.query_string_behavior.query_strings
                )
            elif policy_config.query_string_behavior.type == "deny_list":
                query_string_behavior = CacheQueryStringBehavior.allow_list(
                    *policy_config.query_string_behavior.query_strings
                )

        duration_zero_seconds = Duration.seconds(0)
        duration_one_day = Duration.days(1)
        duration_one_year = Duration.year(1)
        min_ttl = Duration.parse(policy_config.min_ttl if policy_config.min_ttl else duration_zero_seconds)
        default_ttl = (
            Duration.parse(policy_config.default_ttl)
            if policy_config.default_ttl
            else (min_ttl if min_ttl.to_milliseconds > duration_one_day else duration_one_day)
        )
        max_ttl = (
            Duration.parse(policy_config.max_ttl)
            if policy_config.max_ttl
            else (default_ttl if default_ttl.to_milliseconds > duration_one_year else duration_one_year)
        )

        return CachePolicy(
            self.stack,
            policy_config.logical_name,
            cache_policy_name=f"{policy_name}_{env.APP_STACK_PREFIX}",
            comment=(policy_config.comment if policy_config.comment else ""),
            cookie_behavior=cookie_behavior,
            default_ttl=default_ttl,
            enable_accept_encoding_brotli=(
                policy_config.enable_accept_encoding_brotli if policy_config.enable_accept_encoding_brotli else False
            ),
            enable_accept_encoding_gzip=(
                policy_config.enable_accept_encoding_gzip if policy_config.enable_accept_encoding_gzip else False
            ),
            header_behavior=header_behavior,
            max_ttl=max_ttl,
            min_ttl=min_ttl,
            query_string_behavior=query_string_behavior,
        )

    def _get_cloudfront_origin_request_policy(self, policy_name: str) -> OriginRequestPolicy:
        if policy_name not in self.stack.resources.cloudfront.policies.origin_request_policies.keys():
            self.stack.resources.cloudfront.policies.origin_request_policies[policy_name] = (
                self._create_cloudfront_origin_request_policy(policy_name)
            )
        return self.stack.resources.cloudfront.policies.origin_request_policies[policy_name]

    def _create_cloudfront_origin_request_policy(self, policy_name: str) -> OriginRequestPolicy:
        policy_config = self.cloudfront_config.policies.origin_request[policy_name]
        cookie_behavior = OriginRequestCookieBehavior.none()
        if policy_config.cookie_behavior.type:
            if policy_config.cookie_behavior.type == "all":
                cookie_behavior = OriginRequestCookieBehavior.all()
            elif policy_config.cookie_behavior.type == "allow_list":
                cookie_behavior = OriginRequestCookieBehavior.allow_list(*policy_config.cookie_behavior.cookies)
            elif policy_config.cookie_behavior.type == "deny_list":
                cookie_behavior = OriginRequestCookieBehavior.allow_list(*policy_config.cookie_behavior.cookies)

        header_behavior = OriginRequestHeaderBehavior.none()
        if policy_config.header_behavior.type:
            if policy_config.header_behavior.type == "all":
                cookie_behavior = OriginRequestHeaderBehavior.all()
            elif policy_config.header_behavior.type == "allow_list":
                cookie_behavior = OriginRequestHeaderBehavior.allow_list(*policy_config.header_behavior.headers)
            elif policy_config.header_behavior.type == "deny_list":
                cookie_behavior = OriginRequestHeaderBehavior.allow_list(*policy_config.header_behavior.headers)

        query_string_behavior = OriginRequestQueryStringBehavior.none()
        if policy_config.query_string_behavior.type:
            if policy_config.query_string_behavior.type == "all":
                query_string_behavior = OriginRequestQueryStringBehavior.all()
            elif policy_config.query_string_behavior.type == "allow_list":
                query_string_behavior = OriginRequestQueryStringBehavior.allow_list(
                    *policy_config.query_string_behavior.query_strings
                )
            elif policy_config.query_string_behavior.type == "deny_list":
                query_string_behavior = OriginRequestQueryStringBehavior.allow_list(
                    *policy_config.query_string_behavior.query_strings
                )

        return OriginRequestPolicy(
            self.stack,
            policy_config.logical_name,
            comment=(policy_config.comment if policy_config.comment else ""),
            cookie_behavior=cookie_behavior,
            header_behavior=header_behavior,
            origin_request_policy_name=f"{policy_name}_{env.APP_STACK_PREFIX}",
            query_string_behavior=query_string_behavior,
        )

    def _get_cloudfront_response_header_policy(self, policy_name: str) -> ResponseHeadersPolicy:
        if policy_name not in self.stack.resources.cloudfront.policies.response_headers_policies.keys():
            self.stack.resources.cloudfront.policies.response_headers_policies[policy_name] = (
                self._create_cloudfront_response_header_policy(policy_name)
            )
        return self.stack.resources.cloudfront.policies.response_headers_policies[policy_name]

    def _create_cloudfront_response_header_policy(self, policy_name: str) -> ResponseHeadersPolicy:
        policy_config = self.cloudfront_config.policies.response_header[policy_name]
        cors_behavior_config = policy_config.cors_behavior
        cors_behavior = None
        if cors_behavior_config:
            cors_behavior = ResponseHeadersCorsBehavior(
                access_control_allow_credentials=(
                    cors_behavior_config.access_control_allow_credentials
                    if "access_control_allow_credentials" in cors_behavior_config.keys()
                    else False
                ),
                access_control_allow_headers=(
                    cors_behavior_config.access_control_allow_headers
                    if cors_behavior_config.access_control_allow_headers
                    else []
                ),
                access_control_allow_methods=(
                    cors_behavior_config.access_control_allow_methods
                    if cors_behavior_config.access_control_allow_methods
                    else []
                ),
                access_control_allow_origins=(
                    cors_behavior_config.access_control_allow_origins
                    if cors_behavior_config.access_control_allow_origins
                    else []
                ),
                origin_override=(
                    cors_behavior_config.origin_override if "origin_override" in cors_behavior_config.keys() else False
                ),
                access_control_expose_headers=(
                    cors_behavior_config.access_control_expose_headers
                    if cors_behavior_config.access_control_expose_headers
                    else []
                ),
                access_control_max_age=(
                    Duration.parse(cors_behavior_config.access_control_max_age)
                    if cors_behavior_config.access_control_max_age
                    else None
                ),
            )

        custom_headers = []
        for custom_header in policy_config.custom_headers:
            custom_headers.append(
                ResponseCustomHeader(
                    header=custom_header.header, override=custom_header.override, value=custom_header.value
                )
            )

        custom_headers_behavior = (
            ResponseCustomHeadersBehavior(custom_headers=custom_headers) if custom_headers else None
        )

        remove_headers = policy_config.remove_headers if policy_config.remove_headers else []

        if policy_config.security_headers_behavior:

            content_security_policy = (
                ResponseHeadersContentSecurityPolicy(
                    content_security_policy=policy_config.security_headers_behavior.content_security_policy.policy,
                    override=policy_config.security_headers_behavior.content_security_policy.override,
                )
                if policy_config.security_headers_behavior.content_security_policy
                else None
            )
            content_type_options = (
                ResponseHeadersContentTypeOptions(
                    override=policy_config.security_headers_behavior.content_type_options.override,
                )
                if policy_config.security_headers_behavior.content_type_options
                else None
            )
            frame_options = (
                ResponseHeadersFrameOptions(
                    frame_option=getattr(HeadersFrameOption, policy_config.security_headers_behavior.frame_option),
                    override=policy_config.security_headers_behavior.frame_options.override,
                )
                if policy_config.security_headers_behavior.frame_options
                else None
            )
            referrer_policy = (
                ResponseHeadersReferrerPolicy(
                    referrer_policy=getattr(
                        HeadersReferrerPolicy, policy_config.security_headers_behavior.referrer_policy.policy
                    ),
                    override=policy_config.security_headers_behavior.referrer_policy.override,
                )
                if policy_config.security_headers_behavior.referrer_policy
                else None
            )
            strict_transport_security = (
                ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=policy_config.security_headers_behavior.strict_transport_security.access_control_max_age,
                    include_subdomains=policy_config.security_headers_behavior.strict_transport_security.include_subdomains,
                    preload=policy_config.security_headers_behavior.strict_transport_security.preload,
                    override=policy_config.security_headers_behavior.strict_transport_security.override,
                )
                if policy_config.security_headers_behavior.strict_transport_security
                else None
            )
            xss_protection = (
                ResponseHeadersXSSProtection(
                    protection=policy_config.security_headers_behavior.xss_protection.protection,
                    mode_block=policy_config.security_headers_behavior.xss_protection.mode_block,
                    report_uri=policy_config.security_headers_behavior.xss_protection.report_uri,
                    override=policy_config.security_headers_behavior.xss_protection.override,
                )
                if policy_config.security_headers_behavior.xss_protection
                else None
            )
            security_headers_behavior = ResponseSecurityHeadersBehavior(
                content_security_policy=content_security_policy,
                content_type_options=content_type_options,
                frame_options=frame_options,
                referrer_policy=referrer_policy,
                strict_transport_security=strict_transport_security,
                xss_protection=xss_protection,
            )
        else:
            security_headers_behavior = None

        server_timing_sampling_rate = (
            policy_config.server_timing_sampling_rate if policy_config.server_timing_sampling_rate else None
        )

        return ResponseHeadersPolicy(
            self.stack,
            policy_config.logical_name,
            comment=(policy_config.comment if policy_config.comment else ""),
            cors_behavior=cors_behavior,
            custom_headers_behavior=custom_headers_behavior,
            remove_headers=remove_headers,
            response_headers_policy_name=f"{policy_name}_{env.APP_STACK_PREFIX}",
            security_headers_behavior=security_headers_behavior,
            server_timing_sampling_rate=server_timing_sampling_rate,
        )
