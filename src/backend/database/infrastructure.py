from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk.aws_dynamodb import (
    Attribute,
    AttributeType,
    Billing,
    GlobalSecondaryIndexPropsV2,
    ProjectionType,
    StreamViewType,
    TableV2,
)
from aws_cdk.aws_lambda import MetricsConfig, MetricType, StartingPosition
from aws_cdk.aws_lambda_event_sources import DynamoEventSource
from json import dumps

from src.backend.configuration.common import get_dynamodb_config, get_lambda_function

from vars import env


def create_dynamodb_databases(stack: Stack) -> None:
    dynamodb_config = get_dynamodb_config()
    for table_name, table_config in dynamodb_config.items():
        partition_key = Attribute(
            name=table_config.partition_key.name,
            type=getattr(AttributeType, table_config.partition_key.type),
        )

        sort_key = (
            Attribute(name=table_config.sort_key.name, type=getattr(AttributeType, table_config.sort_key.type))
            if table_config.sort_key
            else None
        )

        stream = getattr(StreamViewType, table_config.stream.view_type) if "stream" in table_config.keys() else None

        stack.resources.databases.dynamodb[table_name] = TableV2(
            stack,
            table_config.logical_name,
            partition_key=partition_key,
            billing=Billing.on_demand(),
            dynamo_stream=stream,
            removal_policy=(RemovalPolicy.RETAIN if env.APP_DEPLOY_ENV == "PROD" else RemovalPolicy.DESTROY),
            sort_key=sort_key,
            deletion_protection=(env.APP_DEPLOY_ENV == "PROD"),
        )

        for global_index_name, global_index_config in table_config.global_indexes.items():
            gsi_partition_key = Attribute(
                name=global_index_config.partition_key.name,
                type=getattr(AttributeType, global_index_config.partition_key.type),
            )

            gsi_sort_key = (
                Attribute(
                    name=global_index_config.sort_key.name,
                    type=getattr(AttributeType, global_index_config.sort_key.type),
                )
                if global_index_config.sort_key
                else None
            )

            stack.resources.databases.dynamodb[table_name].add_global_secondary_index(
                partition_key=gsi_partition_key,
                sort_key=gsi_sort_key,
                index_name=global_index_name,
                projection_type=(
                    getattr(ProjectionType, global_index_config.projection_type)
                    if global_index_config.projection_type
                    else ProjectionType.KEYS_ONLY
                ),
                non_key_attributes=(
                    global_index_config.non_key_attributes if global_index_config.non_key_attributes else None
                ),
            )

        for local_index_name, local_index_config in table_config.local_indexes.items():
            lsi_sort_key = (
                Attribute(
                    name=local_index_config.sort_key.name,
                    type=getattr(AttributeType, local_index_config.sort_key.type),
                )
                if local_index_config.sort_key
                else None
            )

            stack.resources.databases.dynamodb[table_name].add_global_secondary_index(
                sort_key=lsi_sort_key,
                index_name=local_index_name,
                projection_type=(
                    getattr(ProjectionType, local_index_config.projection_type)
                    if local_index_config.projection_type
                    else ProjectionType.KEYS_ONLY
                ),
                non_key_attributes=(
                    local_index_config.non_key_attributes if local_index_config.non_key_attributes else None
                ),
            )

        if "stream" in table_config.keys():
            stream_config = table_config.stream

            metrics_config = (
                MetricsConfig(metrics=[getattr(MetricType.EVENT_COUNT, stream_config.metrics_config)])
                if stream_config.metrics_config
                else None
            )

            event_source = DynamoEventSource(
                stack.resources.databases.dynamodb[table_name],
                bisect_batch_on_error=(
                    stream_config.bisect_batch_on_error if stream_config.bisect_batch_on_error else None
                ),
                filters=(
                    [{"pattern": dumps(stream_filter.pattern)} for stream_filter in stream_config.filters]
                    if stream_config.filters
                    else None
                ),
                max_record_age=(Duration.parse(stream_config.max_record_age) if stream_config.max_record_age else None),
                metrics_config=metrics_config,
                parallelization_factor=(
                    stream_config.parallelization_factor if stream_config.parallelization_factor else None
                ),
                report_batch_item_failures=(
                    stream_config.report_batch_item_failures if stream_config.report_batch_item_failures else None
                ),
                retry_attempts=(stream_config.retry_attempts if stream_config.retry_attempts else 0),
                tumbling_window=(
                    Duration.parse(stream_config.tumbling_window) if stream_config.tumbling_window else None
                ),
                starting_position=(
                    getattr(StartingPosition, stream_config.starting_position)
                    if stream_config.starting_position
                    else StartingPosition.LATEST
                ),
                batch_size=(stream_config.batch_size if stream_config.batch_size else None),
                enabled=(stream_config.enabled if stream_config.enabled else None),
                max_batching_window=(
                    Duration.parse(stream_config.max_batching_window) if stream_config.max_batching_window else None
                ),
            )

            function_dict = get_lambda_function(stack, stream_config.function_name)

            function_dict.function.add_event_source(event_source)
