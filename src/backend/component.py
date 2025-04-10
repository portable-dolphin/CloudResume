#!/usr/bin/env python3
from aws_cdk import aws_lambda as Lambda, aws_signer as Signer, CfnOutput, Environment, Fn, Stack
from aws_cdk.aws_certificatemanager import Certificate
from benedict import benedict
from constructs import Construct
from json import dumps
from os import environ

from vars import env

from src.backend.configuration.common import (
    export_cdk_variables,
    format_logical_name_uppercase,
    get_cdk_config,
    get_deploy_region,
    get_dns_config,
    get_lambda_config,
    get_resource_by_logical_name,
    replace_placeholders_in_string,
)

import src.backend.api.infrastructure as api_infrastructure
import src.backend.database.infrastructure as database_infrastructure
import src.backend.monitoring.infrastructure as monitoring_infrastructure
import src.backend.userstore.infrastructure as userstore_infrastructure
import src.backend.webapp.infrastructure as webapp_infrastructure

cdk_config = get_cdk_config()


class ResumeWebAppBackend:
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        stack_prefix = f"{env.APP_STACK_PREFIX}"

        deploy_region = get_deploy_region()

        resources = benedict({}, keyattr_dynamic=True)
        resources.exported_values = []

        deploy_region_stack_part_1 = ResumeWebAppDeployRegionStack(
            scope,
            f"{stack_prefix}P1",
            resources,
            env=Environment(account=kwargs["env"].account, region=deploy_region),
            cross_region_references=True,
        )

        deploy_region_stack_part_1.create_s3_buckets()
        deploy_region_stack_part_1.configure_s3_buckets_cors()
        deploy_region_stack_part_1.create_cognito()

        us_east_1_stack_part_1 = ResumeAppUsEast1Stack(
            scope,
            f"{stack_prefix}P2",
            deploy_region_stack_part_1.resources,
            env=Environment(account=kwargs["env"].account, region="us-east-1"),
            cross_region_references=True,
        )
        us_east_1_stack_part_1.add_dependency(target=deploy_region_stack_part_1)

        us_east_1_stack_part_1.create_cloudfront()

        deploy_region_stack_part_2 = ResumeWebAppDeployRegionStack(
            scope,
            f"{stack_prefix}P3",
            us_east_1_stack_part_1.resources,
            env=Environment(account=kwargs["env"].account, region=deploy_region),
            cross_region_references=True,
        )
        deploy_region_stack_part_2.add_dependency(target=us_east_1_stack_part_1)

        deploy_region_stack_part_2.create_dynamodb()
        deploy_region_stack_part_2.configure_s3_buckets()
        deploy_region_stack_part_2.configure_cognito()
        deploy_region_stack_part_2.create_api()

        us_east_1_stack_part_2 = ResumeAppUsEast1Stack(
            scope,
            f"{stack_prefix}P4",
            deploy_region_stack_part_2.resources,
            env=Environment(account=kwargs["env"].account, region="us-east-1"),
            cross_region_references=True,
        )
        us_east_1_stack_part_2.add_dependency(target=deploy_region_stack_part_2)

        for function_config in us_east_1_stack_part_2.resources.custom_resources.to_resolve["us-east-1"].values():
            function_config.function(us_east_1_stack_part_2, **function_config.props)

        us_east_1_stack_part_2.create_monitoring()

        deploy_region_stack_part_3 = ResumeWebAppDeployRegionStack(
            scope,
            f"{stack_prefix}P5",
            us_east_1_stack_part_2.resources,
            env=Environment(account=kwargs["env"].account, region=deploy_region),
            cross_region_references=True,
        )
        deploy_region_stack_part_3.add_dependency(target=us_east_1_stack_part_2)

        for function_config in deploy_region_stack_part_3.resources.custom_resources.to_resolve[deploy_region].values():
            function_config.function(deploy_region_stack_part_3, **function_config.props)

        deploy_region_stack_part_3.create_monitoring()

        export_cdk_variables(deploy_region_stack_part_3)


class ResumeAppUsEast1Stack(Stack):
    def __init__(self, scope: Construct, construct_id: str, resources: benedict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.resources = resources
        if self.region not in self.resources.custom_resources.to_resolve.keys():
            self.resources.custom_resources.to_resolve[self.region] = {}

    def create_cloudfront(self, certificate: Certificate = None):
        webapp = webapp_infrastructure.create_web_infrastructure(self, env.APP_DEPLOY_ENV)
        webapp.create_cloudfront_distributions()

    def create_monitoring(self):
        monitoring_infrastructure.create_monitoring(self, self.region)


class ResumeWebAppDeployRegionStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, resources: benedict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env = kwargs["env"]
        self.resources = resources
        self.webapp = webapp_infrastructure.create_web_infrastructure(self, env.APP_DEPLOY_ENV)
        self.api = api_infrastructure.CreateApi(self, env.APP_DEPLOY_ENV)
        if self.region not in self.resources.custom_resources.to_resolve.keys():
            self.resources.custom_resources.to_resolve[self.region] = {}

    def configure_cognito(self):
        userstore_infrastructure.configure_userstores(self)

    def configure_s3_buckets(self):
        self.webapp.configure_s3_buckets_policies()
        self.webapp.create_s3_event_notifications()

    def configure_s3_buckets_cors(self):
        self.webapp.configure_s3_buckets_cors()

    def create_api(self):
        self.api.create_apis()

    def create_cognito(self):
        userstore_infrastructure.create_userstores(self)

    def create_dynamodb(self):
        database_infrastructure.create_dynamodb_databases(self)

    def create_monitoring(self):
        monitoring_infrastructure.create_monitoring(self, self.region)

    def create_s3_buckets(self):
        self.webapp.create_s3_buckets()
