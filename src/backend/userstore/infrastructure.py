from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk.aws_certificatemanager import Certificate
from aws_cdk.aws_cognito import (
    AuthFlow,
    AutoVerifiedAttrs,
    AccountRecovery,
    CognitoDomainOptions,
    CustomDomainOptions,
    FeaturePlan,
    IUserPool,
    KeepOriginalAttrs,
    Mfa,
    MfaSecondFactor,
    OAuthFlows,
    OAuthScope,
    OAuthSettings,
    PasswordPolicy,
    SignInAliases,
    UserPool,
    UserPoolDomain,
    UserPoolEmail,
)
from json import loads
from random import randrange
from typing import List

from src.backend.configuration.common import (
    check_if_string_has_placeholder,
    create_dns_records,
    get_cdk_config,
    get_cognito_config,
    get_lambda_function,
    get_placeholder_value,
    get_resource_attribute,
    get_resource_by_logical_name,
    get_sns_config,
    get_sns_topic,
    get_ssl_certificate,
    replace_placeholders_in_string,
)

from vars import env

_cognito_config = get_cognito_config()


def create_userstores(stack: Stack) -> None:
    for userpool_name, userpool_config in _cognito_config.userpools.items():
        auto_verify = AutoVerifiedAttrs(
            email=(True if "email" in userpool_config.authentication.auto_verify else False),
            phone=(True if "phone" in userpool_config.authentication.auto_verify else False),
        )

        if userpool_config.email.type == "cognito":
            userpool_email = UserPoolEmail.with_cognito(
                reply_to=replace_placeholders_in_string(stack, userpool_config.email.reply_to)
            )
        else:
            raise NotImplementedError(
                f"User pool email type of {userpool_config.email.type} has not yet been implemented"
            )

        lambda_triggers = {}
        for lambda_trigger, lambda_trigger_config in userpool_config.lambda_triggers.items():
            if "sns_topic" in lambda_trigger_config.keys():
                _ = get_sns_topic(stack, lambda_trigger_config.sns_topic)
            function_dict = get_lambda_function(stack, lambda_trigger_config.name)
            function = function_dict.function
            lambda_triggers[lambda_trigger] = function

        mfa_second_factor = (
            MfaSecondFactor(
                otp=(True if "otp" in userpool_config.authentication.mfa_second_factor else False),
                sms=(True if "sms" in userpool_config.authentication.mfa_second_factor else False),
            )
            if env.APP_DEPLOY_ENV == "PROD"
            else None
        )

        password_policy_config = userpool_config.authentication.password_policy
        password_policy = PasswordPolicy(
            min_length=(password_policy_config.min_length if password_policy_config.min_length else 16),
            require_digits=(password_policy_config.require_digits if password_policy_config.require_digits else True),
            require_lowercase=(
                password_policy_config.require_lowercase if password_policy_config.require_lowercase else True
            ),
            require_symbols=(
                password_policy_config.require_symbols if password_policy_config.require_symbols else True
            ),
            require_uppercase=(
                password_policy_config.require_uppercase if password_policy_config.require_uppercase else True
            ),
            temp_password_validity=(
                Duration.parse(password_policy_config.temp_password_validity)
                if password_policy_config.temp_password_validity
                else Duration.days(1)
            ),
        )

        stack.resources.cognito.userpools[userpool_name] = UserPool(
            stack,
            userpool_config.logical_name,
            account_recovery=getattr(AccountRecovery, userpool_config.authentication.account_recovery),
            auto_verify=auto_verify,
            email=userpool_email,
            feature_plan=getattr(FeaturePlan, userpool_config.feature_plan),
            keep_original=KeepOriginalAttrs(email=False, phone=False),
            lambda_triggers=(lambda_triggers if lambda_triggers else None),
            mfa=(getattr(Mfa, userpool_config.authentication.mfa)),
            mfa_second_factor=mfa_second_factor,
            password_policy=password_policy,
            removal_policy=(RemovalPolicy.RETAIN if env.APP_DEPLOY_ENV == "PROD" else RemovalPolicy.DESTROY),
            self_sign_up_enabled=(
                userpool_config.authentication.self_sign_up_enabled
                if "self_sign_up_enabled" in userpool_config.authentication.keys()
                else False
            ),
            sign_in_aliases=(
                userpool_config.authentication.sign_in_aliases
                if userpool_config.authentication.sign_in_aliases
                else {SignInAliases(username=True, email=True)}
            ),
            sign_in_case_sensitive=(
                userpool_config.authentication.sign_in_case_sensitive
                if userpool_config.authentication.sign_in_case_sensitive
                else False
            ),
            standard_attributes=(
                userpool_config.authentication.standard_attributes
                if userpool_config.authentication.standard_attributes
                else None
            ),
            user_pool_name=userpool_config.name,
        )

        for group_config in userpool_config.groups:
            group_names = (
                get_placeholder_value(stack, group_config.group_names)
                if check_if_string_has_placeholder(group_config.group_names)
                else group_config.group_names
            )
            group_names = loads(group_names) if isinstance(group_names, str) else group_names
            for group in group_names:
                group_logical_name = f"{userpool_config.logical_name}Group{group}"
                stack.resources.cognito.userpools[userpool_name].add_group(
                    group_logical_name, group_name=group, description=group_config.description
                )


def _get_user_pool(stack: Stack, userpool_name: str) -> IUserPool:
    if stack.resources.cognito.userpools[userpool_name].stack.stack_name != stack.stack_name:
        new_userpool_name = userpool_name + stack.stack_name
        if new_userpool_name in stack.resources.cognito.userpools:
            return stack.resources.cognito.userpools[new_userpool_name]
        exiting_userpool_logical_name = _cognito_config.userpools[userpool_name].logical_name
        new_userpool_logical_name = _cognito_config.userpools[userpool_name].logical_name + stack.stack_name
        userpool_arn = get_resource_attribute(stack, exiting_userpool_logical_name, "user_pool_arn")
        stack.resources.cognito.userpools[new_userpool_name] = UserPool.from_user_pool_arn(
            stack, new_userpool_logical_name, userpool_arn
        )
        return stack.resources.cognito.userpools[new_userpool_name]
    else:
        return stack.resources.cognito.userpools[userpool_name]


def configure_userstores(stack: Stack) -> None:
    for userpool_name, userpool_config in _cognito_config.userpools.items():
        certificate = get_ssl_certificate(stack, userpool_config.domain.certificate)
        _create_userstore_domain(
            stack,
            userpool_name,
            certificate,
            userpool_config.domain.custom_domain_name,
            userpool_config.domain.logical_name,
            userpool_config.domain.dns_recordset,
            userpool_config.domain.depends_on if "depends_on" in userpool_config.domain.keys() else [],
        )
        for client_config in userpool_config.app_clients.values():
            if "auth_flows" in client_config.keys():
                auth_flows = AuthFlow(
                    admin_user_password=(True if "admin_user_password" in client_config.auth_flows.keys() else False),
                    custom=(True if "custom" in client_config.auth_flows.keys() else False),
                    user=(True if "user" in client_config.auth_flows.keys() else False),
                    user_password=(True if "user_password" in client_config.auth_flows.keys() else False),
                    user_srp=(True if "user_srp" in client_config.auth_flows.keys() else False),
                )
            else:
                auth_flows = AuthFlow.user_srp

            if "oauth_flows" in client_config.keys():
                config_oauth_flows = client_config.oauth_flows
                oauth_flows = OAuthFlows(
                    authorization_code_grant=(
                        config_oauth_flows.authorization_code_grant
                        if "authorization_code_grant" in config_oauth_flows.keys()
                        else False
                    ),
                    client_credentials=(
                        config_oauth_flows.client_credentials
                        if "client_credentials" in config_oauth_flows.keys()
                        else False
                    ),
                    implicit_code_grant=(
                        config_oauth_flows.implicit_code_grant
                        if "implicit_code_grant" in config_oauth_flows.keys()
                        else False
                    ),
                )
            else:
                oauth_flows = OAuthFlows(authorization_code_grant=True)

            _create_userstore_client(
                stack=stack,
                userpool_name=userpool_name,
                client_name=client_config.name,
                client_logical_name=client_config.logical_name,
                oauth_scopes=client_config.oauth_settings.oauth_scopes,
                callback_urls=client_config.oauth_settings.callback_urls,
                default_redirect_uri=client_config.oauth_settings.default_redirect_uri,
                oauth_flows=oauth_flows,
                logout_urls=client_config.oauth_settings.logout_urls,
                access_token_validity=(
                    Duration.parse(client_config.access_token_validity)
                    if "access_token_validity" in client_config.keys()
                    else Duration.minutes(60)
                ),
                auth_flows=auth_flows,
                auth_session_validity=(
                    Duration.parse(client_config.auth_session_validity)
                    if client_config.auth_session_validity
                    else Duration.minutes(3)
                ),
                disable_o_auth=(client_config.disable_o_auth if client_config.disable_o_auth else False),
                enable_propagate_additional_user_context_data=(
                    client_config.enable_propagate_additional_user_context_data
                    if client_config.enable_propagate_additional_user_context_data
                    else False
                ),
                enable_token_revocation=(
                    client_config.enable_token_revocation if client_config.enable_token_revocation else True
                ),
                generate_secret=(client_config.generate_secret if client_config.generate_secret else False),
                id_token_validity=(
                    Duration.parse(client_config.id_token_validity)
                    if client_config.id_token_validity
                    else Duration.minutes(60)
                ),
                prevent_user_existence_errors=(
                    client_config.prevent_user_existence_errors if client_config.prevent_user_existence_errors else True
                ),
                refresh_token_validity=(
                    Duration.parse(client_config.refresh_token_validity)
                    if client_config.refresh_token_validity
                    else Duration.days(3)
                ),
            )


def _create_userstore_client(
    stack: Stack,
    userpool_name: str,
    client_logical_name: str,
    client_name: str,
    oauth_scopes: List[str],
    callback_urls: List[str],
    default_redirect_uri: str,
    oauth_flows: OAuthFlows,
    logout_urls: str,
    access_token_validity: Duration,
    auth_flows: AuthFlow,
    auth_session_validity: Duration,
    disable_o_auth: bool,
    enable_propagate_additional_user_context_data: bool,
    enable_token_revocation: bool,
    generate_secret: bool,
    id_token_validity: Duration,
    prevent_user_existence_errors: bool,
    refresh_token_validity: Duration,
) -> None:
    oauth_scopes = [getattr(OAuthScope, scope) for scope in oauth_scopes]
    oauth_settings = OAuthSettings(
        callback_urls=[replace_placeholders_in_string(stack, url) for url in callback_urls],
        default_redirect_uri=replace_placeholders_in_string(stack, default_redirect_uri),
        flows=oauth_flows,
        logout_urls=[replace_placeholders_in_string(stack, url) for url in logout_urls],
        scopes=oauth_scopes,
    )
    userpool = _get_user_pool(stack, userpool_name)
    userpool.add_client(
        client_logical_name,
        access_token_validity=access_token_validity,
        auth_flows=auth_flows,
        auth_session_validity=auth_session_validity,
        disable_o_auth=disable_o_auth,
        enable_propagate_additional_user_context_data=enable_propagate_additional_user_context_data,
        enable_token_revocation=enable_token_revocation,
        generate_secret=generate_secret,
        id_token_validity=id_token_validity,
        o_auth=oauth_settings,
        prevent_user_existence_errors=prevent_user_existence_errors,
        refresh_token_validity=refresh_token_validity,
        user_pool_client_name=client_name,
    )


def _create_userstore_domain(
    stack: Stack,
    userpool_name: str,
    certificate: Certificate,
    custom_domain_name: str,
    domain_logical_name: str,
    dns_recordset: str,
    depends_on: List[str] = [],
) -> None:
    userpool = _get_user_pool(stack, userpool_name)
    domain_options = CustomDomainOptions(
        certificate=certificate,
        domain_name=replace_placeholders_in_string(stack, custom_domain_name),
    )
    domain = UserPoolDomain(stack, domain_logical_name, user_pool=userpool, custom_domain=domain_options)

    create_dns_records(stack, dns_recordset)

    if env.APP_DEPLOY_ENV == "PROD":
        domain.apply_removal_policy(RemovalPolicy.RETAIN)
    else:
        domain.apply_removal_policy(RemovalPolicy.DESTROY)

    for dependency_logical_name in depends_on:
        dependency = get_resource_by_logical_name(stack, dependency_logical_name)
        domain.node.add_dependency(dependency)

    stack.resources.cognito.userpools[userpool_name].domain = domain
