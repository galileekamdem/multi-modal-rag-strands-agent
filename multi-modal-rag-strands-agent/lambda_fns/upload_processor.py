import os
import json
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes import event_source, S3Event
from aws_lambda_powertools.utilities.data_classes.appsync import scalar_types_utils


QUEUE = os.getenv("QUEUE")
BUCKET = os.getenv("BUCKET")
PREFIX_INCOMING = os.getenv("PREFIX_INCOMING", "uploads/")
PREFIX_PROCESSED = os.getenv("PREFIX_PROCESSED", "processed/")

if not QUEUE or not BUCKET:
    raise ValueError("Required env vars QUEUE and BUCKET must be set")

# ────────────────────────────  AWS  ────────────────────────────
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
logger = Logger()


@logger.inject_lambda_context(log_event=True)
@event_source(data_class=S3Event)
def lambda_handler(event: S3Event, context):
    logger.info(f"received s3 event {event}")
    uuid = scalar_types_utils.make_id()
    for record in event.records:
        if record.event_name.endswith(":Copy"):
            logger.info(
                "Ignoring ObjectCreated:Copy event for %s", record.s3.get_object.key
            )
            continue

        bucket_name = record.s3.bucket.name
        key_raw = record.s3.get_object.key
        key = unquote_plus(key_raw)

        logger.info("Processing raw key %s", key_raw)
        logger.info("Processing key %s", key)

        if bucket_name != BUCKET:
            logger.warning("Skipping key from unexpected bucket %s", bucket_name)
            continue
        if key.startswith(PREFIX_PROCESSED):
            logger.info("Key %s already in target prefix – skipping", key)
            continue

        root, ext = os.path.splitext(key)
        new_key = f"{PREFIX_PROCESSED}{uuid}{ext}"
        copy_src = {"Bucket": bucket_name, "Key": key}
        new_s3_uri = f"s3://{bucket_name}/{new_key}"

        try:
            s3.copy_object(CopySource=copy_src, Bucket=bucket_name, Key=new_key)
            logger.info("Copied %s  ➜  %s", key, new_key)
        except ClientError as e:
            logger.exception("Failed to copy object: %s", e)
            continue  # move on to the next record

        message = {
            "documentId": uuid,
            "original_key": key,
            "key": new_key,
            "extension": ext,
            "bucket": bucket_name,
            "s3_uri": new_s3_uri,
            "original_s3_uri": f"s3://{bucket_name}/{key}",
            "timestamp": scalar_types_utils.aws_timestamp(),  # milliseconds
        }

        try:
            resp = sqs.send_message(QueueUrl=QUEUE, MessageBody=json.dumps(message))
            logger.info("Enqueued message %s for %s", resp.get("MessageId"), key)
        except ClientError as e:
            logger.exception("Failed to send SQS message: %s", e)
            # (optional) rollback the copy if desired