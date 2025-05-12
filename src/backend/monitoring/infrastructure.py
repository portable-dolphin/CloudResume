from aws_cdk import Duration, Stack
from cdk_monitoring_constructs import (
    AlarmFactoryDefaults,
    ErrorRateThreshold,
    HighTpsThreshold,
    LatencyThreshold,
    MonitoringFacade,
    NoopAlarmActionStrategy,
    RateComputationMethod,
    SnsAlarmActionStrategy,
)

from src.backend.configuration.common import (
    get_api_config,
    get_monitoring_config,
    get_resource_by_logical_name,
    get_sns_topic,
    is_duration_string,
    replace_placeholders_in_string,
)

from vars import env


def _create_facade(stack: Stack, facade_name, facade_config):
    if not facade_config.alarm_defaults.action_type:
        alarm_default_action = None
    elif facade_config.alarm_defaults.action_type not in ["sns_topic"]:
        raise RuntimeError(
            f"The alarm action type of {facade_config.alarm_defaults.action_type} is invalid or has not yet been implemented."
        )
    elif facade_config.alarm_defaults.action_type == "sns_topic":
        if not facade_config.alarm_defaults.action.on_alarm_topic:
            raise RuntimeError(f"No on_alarm_topic for monitoring facade {facade_name} was defined.")
        on_alarm_topic = get_sns_topic(stack, facade_config.alarm_defaults.action.on_alarm_topic).topic
        on_insufficient_data_topic = (
            get_sns_topic(stack, facade_config.alarm_defaults.action.on_insufficient_data_topic).topic
            if facade_config.alarm_defaults.action.on_insufficient_data_topic
            else None
        )
        on_ok_topic = (
            get_sns_topic(stack, facade_config.alarm_defaults.action.on_ok_topic).topic
            if facade_config.alarm_defaults.action.on_ok_topic
            else None
        )

        alarm_default_action = SnsAlarmActionStrategy(
            on_alarm_topic=on_alarm_topic,
            on_insufficient_data_topic=on_insufficient_data_topic,
            on_ok_topic=on_ok_topic,
        )

    return MonitoringFacade(
        stack,
        f"{facade_config.logical_name}_{env.APP_STACK_PREFIX}",
        alarm_factory_defaults=AlarmFactoryDefaults(
            actions_enabled=(
                facade_config.alarm_defaults.actions_enabled
                if "actions_enabled" in facade_config.alarm_defaults.keys()
                else (True if alarm_default_action else False)
            ),
            alarm_name_prefix=(
                facade_config.alarm_defaults.alarm_name_prefix
                if facade_config.alarm_defaults.alarm_name_prefix
                else None
            ),
            action=alarm_default_action,
        ),
    )


def _create_facade_header(facade, header_size, header_text, add_to_summary: bool = None, add_to_alarm: bool = None):
    headers = {"large": facade.add_large_header, "medium": facade.add_medium_header, "small": facade.add_small_header}
    if header_size not in headers.keys():
        raise RuntimeError(f"Header size {header_size} is invalid. Must be one of {', '.join(headers.keys())}")

    headers[header_size](text=header_text, add_to_summary=add_to_summary, add_to_alarm=add_to_alarm)


def _predefined_alarm_props(
    alarms,
    alarm_strategy_override,
    alarm_strategy_config,
    valid_threshold_types,
    disable_actions=False,
    alarm_name_prefix="",
):
    valid_alarm_strategy_overrides = ["NoopAlarmActionStrategy"]
    if alarm_strategy_override:
        if alarm_strategy_override not in valid_alarm_strategy_overrides:
            raise ValueError(
                f"Alarm strategy of {alarm_strategy_override} is invalid or has not yet been implemented. Must be one of {', '.join(valid_alarm_strategy_overrides)}"
            )
        if alarm_strategy_override == "NoopAlarmActionStrategy":
            alarm_strategy = NoopAlarmActionStrategy()
    else:
        alarm_strategy = None
    props = {}
    for alarm_config in alarms:
        if alarm_config.threshold_details.threshold_type not in valid_threshold_types:
            raise ValueError(
                f"Threshold type of {alarm_config.threshold_details.threshold_type} is invalid or has not yet been implemented. Must be one of {', '.join(valid_threshold_types)}"
            )
        if alarm_config.threshold_details.threshold_type == "ErrorRateThreshold":
            threshold = ErrorRateThreshold
        elif alarm_config.threshold_details.threshold_type == "HighTpsThreshold":
            threshold = HighTpsThreshold
        elif alarm_config.threshold_details.threshold_type == "LatencyThreshold":
            threshold = LatencyThreshold
        threshold_props = {
            item.name: (
                Duration.parse(item.value)
                if isinstance(item.value, str) and is_duration_string(item.value)
                else item.value
            )
            for item in alarm_config.threshold_details.threshold_arguments
        }
        if alarm_strategy:
            threshold_props["action_override"] = alarm_strategy
        if disable_actions:
            threshold_props["actions_enabled"] = False
        props[alarm_config.error_name] = {
            f"{alarm_name_prefix}-{alarm_config.logical_name_suffix}-{env.APP_STACK_PREFIX}": threshold(
                **threshold_props
            )
        }

    return props


def create_monitoring(stack: Stack, region: str) -> None:
    monitoring_config = get_monitoring_config()

    valid_resources = ["api_gateway", "cloudfront"]
    valid_facade_types = ["header", "monitor"]

    for facade_name, facade_config in monitoring_config.facades.items():
        if facade_config.type not in valid_resources:
            raise ValueError(
                f"The resource to be monitored of {facade_config.type} is invalid or has not yet been implemented. Must be one of {', '.join(valid_resources)}"
            )
        resource_region = replace_placeholders_in_string(stack, facade_config.region)
        if resource_region != stack.region:
            continue

        facade = _create_facade(stack, facade_name, facade_config)
        for facade_part in facade_config.facade_parts:
            if facade_part.type not in valid_facade_types:
                raise RuntimeError(
                    f"Facade part type of {facade_part.type} is invalid or has not yet been implemented. Must be one of {', '.join(valid_facade_types)}"
                )
            if facade_part.type == "header":
                header_props = {
                    "facade": facade,
                    "header_size": facade_part.config.type,
                    "header_text": facade_part.config.text,
                }
                if facade_part.config.add_to_summary:
                    header_props["add_to_summary"] = facade_part.config.add_to_summary
                if facade_part.config.add_to_alarm:
                    header_props["add_to_alarm"] = facade_part.config.add_to_alarm
                _create_facade_header(**header_props)
            elif facade_part.type == "monitor":
                resource = get_resource_by_logical_name(
                    stack, facade_part.config.monitored_resource_logical_name, resource_expected_region=stack.region
                )
                if facade_config.type == "api_gateway":
                    _create_api_gateway_monitoring(stack, facade, facade_part.config, resource)
                elif facade_config.type == "cloudfront":
                    _create_cloudfront_monitoring(stack, facade, facade_part.config, resource)


def _create_api_gateway_monitoring(stack: Stack, facade, monitor_config, api) -> None:
    def _monitor_sub_resources_and_methods(
        stack,
        facade,
        monitor_config,
        api,
        api_config_resource,
        rate_computation_method=None,
        resource_path="",
    ):
        for resource_name, resource_config in api_config_resource.items():
            sub_resource_path = f"{resource_path}/{resource_name}"
            if "resources" in resource_config.keys():
                _monitor_sub_resources_and_methods(
                    stack,
                    facade,
                    monitor_config,
                    api,
                    resource_config.resources,
                    rate_computation_method=rate_computation_method,
                    resource_path=sub_resource_path,
                )

            for method in resource_config.methods.keys():
                predefined_alarm_props = _predefined_alarm_props(
                    alarms=monitor_config.predefined_alarms,
                    alarm_strategy_override=(
                        monitor_config.alarm_strategy_override
                        if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
                        else None
                    ),
                    alarm_strategy_config=(
                        monitor_config.alarm_strategy_config
                        if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
                        else None
                    ),
                    valid_threshold_types=valid_threshold_types,
                    disable_actions=(monitor_config.disable_actions if monitor_config.disable_actions else None),
                    alarm_name_prefix=(sub_resource_path.replace("/", "-") + method),
                )
                facade.monitor_api_gateway(
                    api=api,
                    api_method=method,
                    api_resource=sub_resource_path,
                    fill_tps_with_zeroes=(
                        monitor_config.fill_tps_with_zeroes if "fill_tps_with_zeroes" in monitor_config.keys() else None
                    ),
                    rate_computation_method=rate_computation_method,
                    alarm_friendly_name=monitor_config.alarm_friendly_name,
                    human_readable_name=monitor_config.human_readable_name,
                    add_to_alarm_dashboard=monitor_config.add_to_alarm_dashboard,
                    **predefined_alarm_props,
                )

    valid_threshold_types = [
        "ErrorCountThreshold",
        "ErrorRateThreshold",
        "LowTpsThreshold",
        "HighTpsThreshold",
        "LatencyThreshold",
    ]
    if monitor_config.metric_category not in ["by_method", "by_api_name"]:
        raise RuntimeError(
            f"The metric category {monitor_config.metric_category} is invalid or has not yet been implemented"
        )

    rate_computation_method = (
        getattr(RateComputationMethod, monitor_config.rate_computation_method)
        if monitor_config.rate_computation_method
        else None
    )

    if monitor_config.metric_category == "by_method":
        full_api_config = get_api_config()
        api_config = [
            config
            for config in full_api_config.values()
            if config.logical_name == monitor_config.monitored_resource_logical_name
        ][0]
        _monitor_sub_resources_and_methods(
            stack,
            facade,
            monitor_config,
            api,
            api_config.resources,
            rate_computation_method=rate_computation_method,
        )
    elif monitor_config.metric_category == "by_api_name":
        predefined_alarm_props = _predefined_alarm_props(
            alarms=monitor_config.predefined_alarms,
            alarm_strategy_override=(
                monitor_config.alarm_strategy_override
                if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
                else None
            ),
            alarm_strategy_config=(
                monitor_config.alarm_strategy_config
                if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
                else None
            ),
            valid_threshold_types=valid_threshold_types,
            disable_actions=(monitor_config.disable_actions if monitor_config.disable_actions else None),
        )
        facade.monitor_api_gateway(
            api=api,
            fill_tps_with_zeroes=(
                monitor_config.fill_tps_with_zeroes if "fill_tps_with_zeroes" in monitor_config.keys() else None
            ),
            rate_computation_method=rate_computation_method,
            alarm_friendly_name=monitor_config.alarm_friendly_name,
            human_readable_name=monitor_config.human_readable_name,
            add_to_alarm_dashboard=monitor_config.add_to_alarm_dashboard,
            **predefined_alarm_props,
        )


def _create_cloudfront_monitoring(stack: Stack, facade, monitor_config, distribution) -> None:
    valid_threshold_types = ["ErrorRateThreshold", "LowTpsThreshold", "HighTpsThreshold"]
    predefined_alarm_props = _predefined_alarm_props(
        alarms=monitor_config.predefined_alarms,
        alarm_strategy_override=(
            monitor_config.alarm_strategy_override
            if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
            else None
        ),
        alarm_strategy_config=(
            monitor_config.alarm_strategy_config
            if monitor_config.alarm_strategy_override and monitor_config.alarm_strategy_config
            else None
        ),
        valid_threshold_types=valid_threshold_types,
        disable_actions=(monitor_config.disable_actions if monitor_config.disable_actions else None),
    )
    rate_computation_method = (
        getattr(RateComputationMethod, monitor_config.rate_computation_method)
        if monitor_config.rate_computation_method
        else None
    )
    facade.monitor_cloud_front_distribution(
        distribution=distribution,
        additional_metrics_enabled=(
            monitor_config.additional_metrics_enabled if monitor_config.additional_metrics_enabled else None
        ),
        fill_tps_with_zeroes=(
            monitor_config.fill_tps_with_zeroes if "fill_tps_with_zeroes" in monitor_config.keys() else None
        ),
        rate_computation_method=rate_computation_method,
        alarm_friendly_name=monitor_config.alarm_friendly_name,
        human_readable_name=monitor_config.human_readable_name,
        add_to_alarm_dashboard=monitor_config.add_to_alarm_dashboard,
        **predefined_alarm_props,
    )
