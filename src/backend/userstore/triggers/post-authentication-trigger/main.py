from ast import literal_eval
import boto3

sns_client = boto3.client("sns")

with open("./config.json", "r") as f:
    config = literal_eval(f.read())


def handler(event, context):
    message = (
        f"Successful management login to {config['domain_url']} account {event['request']['userAttributes']['email']}"
    )
    _ = sns_client.publish(TopicArn=config["sns_topic"], Message=message)
    return event
